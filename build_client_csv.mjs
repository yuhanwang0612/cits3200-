import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import {
  clean,
  normalizeDoi,
  normalizeIssn,
  normalizeText,
  parseCsv,
  stableId,
  writeCsv,
} from "./shared/pipeline/common.mjs";

const ROOT = path.dirname(fileURLToPath(import.meta.url));
const OUTPUT_DIR = path.resolve(process.argv[2] || path.join(ROOT, "cleaned_data"));
const MODULES = [
  { prefix: "uwa_accounting", university: "The University of Western Australia", field: "Accounting" },
  { prefix: "uwa_finance", university: "The University of Western Australia", field: "Finance" },
  { prefix: "unimelb_accounting", university: "The University of Melbourne", field: "Accounting" },
  { prefix: "unimelb_finance", university: "The University of Melbourne", field: "Finance" },
];

function rankingIndexes(rankings) {
  const byIssn = new Map();
  const byTitle = new Map();
  for (const ranking of rankings) {
    for (const value of [ranking.issn, ranking.online_issn]) {
      const issn = normalizeIssn(value);
      if (!issn) continue;
      if (!byIssn.has(issn)) byIssn.set(issn, []);
      byIssn.get(issn).push(ranking);
    }
    const title = normalizeText(ranking.journal_title).replace(/^the /, "");
    if (title) {
      if (!byTitle.has(title)) byTitle.set(title, []);
      byTitle.get(title).push(ranking);
    }
  }
  return { byIssn, byTitle };
}

function lookupRanking(record, indexes) {
  const issns = [...new Set([record.issn, record.eissn].map(normalizeIssn).filter(Boolean))];
  let candidates = issns.flatMap((issn) => indexes.byIssn.get(issn) || []);
  let method = candidates.length ? "ISSN/eISSN" : "";
  if (!candidates.length && record.journal_name) {
    const key = normalizeText(record.journal_name).replace(/^the /, "");
    candidates = indexes.byTitle.get(key) || [];
    if (candidates.length) method = "exact normalized journal title";
  }
  const unique = [...new Map(candidates.map((entry) => [`${entry.journal_title}|${entry.abdc_rating}|${entry.field_of_research}`, entry])).values()];
  const ratings = [...new Set(unique.map((entry) => entry.abdc_rating))];
  return {
    quality_rank: ratings.length === 1 ? ratings[0] : "",
    abdc_for: [...new Set(unique.map((entry) => entry.field_of_research))].join("; "),
    abdc_journal_title: [...new Set(unique.map((entry) => entry.journal_title))].join("; "),
    abdc_match_method: method || "unmatched",
    abdc_match_status: !unique.length ? "unmatched" : ratings.length === 1 ? "matched" : "ambiguous",
    abdc_list_version: unique[0]?.abdc_list_version || "2025-v2-270526",
  };
}

function clarivateIndexes(rows, available) {
  const byLocalJournalId = new Map();
  const byIssn = new Map();
  for (const row of rows) {
    if (row.local_journal_id) byLocalJournalId.set(row.local_journal_id, row);
    for (const value of [row.issn, row.eissn]) {
      const issn = normalizeIssn(value);
      if (!issn) continue;
      if (!byIssn.has(issn)) byIssn.set(issn, []);
      byIssn.get(issn).push(row);
    }
  }
  return { available, byLocalJournalId, byIssn };
}

function lookupClarivate(record, indexes, localJournalId = "") {
  const empty = {
    impact_factor: "",
    five_year_impact_factor: "",
    jcr_year: "",
    jcr_journal_id: "",
    clarivate_match_method: "not_collected",
    clarivate_match_status: "not_collected",
    impact_factor_status: "not_collected_from_clarivate",
    clarivate_retrieved_at: "",
  };
  if (!indexes.available) return empty;

  let candidates = localJournalId && indexes.byLocalJournalId.has(localJournalId)
    ? [indexes.byLocalJournalId.get(localJournalId)]
    : [...new Set([record.issn, record.eissn].map(normalizeIssn).filter(Boolean))]
      .flatMap((issn) => indexes.byIssn.get(issn) || []);
  candidates = [...new Map(candidates.map((row) => [row.local_journal_id || `${row.issn}|${row.eissn}|${row.jcr_journal_id}`, row])).values()];
  if (!candidates.length) return empty;

  const matchedIds = [...new Set(candidates.filter((row) => row.clarivate_match_status === "matched").map((row) => row.jcr_journal_id).filter(Boolean))];
  if (matchedIds.length > 1) {
    return { ...empty, clarivate_match_method: "conflicting_exact_issn_results", clarivate_match_status: "ambiguous", impact_factor_status: "requires_manual_review" };
  }
  const row = candidates.find((candidate) => candidate.clarivate_match_status === "matched") || candidates[0];
  const statusMap = {
    no_issn: "no_issn",
    not_found: "not_found_in_clarivate",
    title_candidate_only: "requires_manual_review",
    ambiguous: "requires_manual_review",
    error: "clarivate_error",
    not_collected: "not_collected_from_clarivate",
  };
  return {
    impact_factor: row.impact_factor || "",
    five_year_impact_factor: row.five_year_impact_factor || "",
    jcr_year: row.jcr_year || "",
    jcr_journal_id: row.jcr_journal_id || "",
    clarivate_match_method: row.clarivate_match_method || "none",
    clarivate_match_status: row.clarivate_match_status || "not_collected",
    impact_factor_status: row.clarivate_match_status === "matched"
      ? row.impact_factor ? "collected" : "matched_no_jif"
      : statusMap[row.clarivate_match_status] || "not_collected_from_clarivate",
    clarivate_retrieved_at: row.clarivate_retrieved_at || "",
  };
}

function authorText(value) {
  if (Array.isArray(value)) return value.map((entry) => typeof entry === "object" ? entry.name : entry).filter(Boolean).join("; ");
  return clean(value).replace(/\s*,\s*(?=[A-Z][a-z]+,?\s)/g, "; ");
}

function authorCount(value) {
  if (Array.isArray(value)) return value.filter(Boolean).length;
  const text = clean(value);
  if (!text) return "";
  return text.split(/\s*;\s*|\s*,\s*/).filter(Boolean).length;
}

function chooseOfficial(primary, secondary, field) {
  return primary?.[field] ?? secondary?.[field] ?? "";
}

function metadataConflicts(official, openalex) {
  if (!openalex) return [];
  const conflicts = [];
  if (official.doi && openalex.doi && normalizeDoi(official.doi) !== normalizeDoi(openalex.doi)) conflicts.push("doi");
  if (official.publication_year && openalex.publication_year && String(official.publication_year) !== String(openalex.publication_year)) conflicts.push("year");
  if (official.title && openalex.title && normalizeText(official.title) !== normalizeText(openalex.title)) conflicts.push("title");
  return conflicts;
}

function titleYearKey(record) {
  const title = normalizeText(record.title);
  const year = clean(record.year || record.publication_year);
  if (title && year) return `title-year:${title}|${year}`;
  const doi = normalizeDoi(record.doi);
  return doi ? `doi:${doi}` : `source:${clean(record.publication_id || record.openalex_work_id || record.article_url)}`;
}

const rankingText = await fs.readFile(path.join(ROOT, "uwa_accounting", "data", "abdc_2025.csv"), "utf8");
const rankings = rankingIndexes(parseCsv(rankingText));
let clarivateRows = [];
let clarivateAvailable = false;
try {
  clarivateRows = JSON.parse(await fs.readFile(path.join(ROOT, "clarivate_data", "journal_metrics.json"), "utf8"));
  clarivateAvailable = true;
} catch (error) {
  if (error.code !== "ENOENT") throw error;
}
const clarivate = clarivateIndexes(clarivateRows, clarivateAvailable);
const staffRows = [];
const relationRows = [];
const reviewRows = [];
const moduleQuality = [];

for (const moduleInfo of MODULES) {
  const moduleDirectory = path.join(ROOT, moduleInfo.prefix);
  const output = path.join(moduleDirectory, "output");
  const openalex = path.join(output, "openalex_v2");
  const [researchers, officialPayload, officialRelationships, authorMatches, openalexWorks, enrichedRelationships, openalexOnly, collectionQuality, openalexQuality] = await Promise.all([
    fs.readFile(path.join(output, `${moduleInfo.prefix}_researchers.json`), "utf8").then(JSON.parse),
    fs.readFile(path.join(output, `${moduleInfo.prefix}_current_staff_publications.json`), "utf8").then(JSON.parse),
    fs.readFile(path.join(output, `${moduleInfo.prefix}_current_staff_relationships.json`), "utf8").then(JSON.parse),
    fs.readFile(path.join(openalex, `${moduleInfo.prefix}_openalex_authors.json`), "utf8").then(JSON.parse),
    fs.readFile(path.join(openalex, `${moduleInfo.prefix}_openalex_works.json`), "utf8").then(JSON.parse),
    fs.readFile(path.join(openalex, `${moduleInfo.prefix}_official_relationships_enriched.json`), "utf8").then(JSON.parse),
    fs.readFile(path.join(openalex, `${moduleInfo.prefix}_openalex_only_relationships.json`), "utf8").then(JSON.parse),
    fs.readFile(path.join(output, `${moduleInfo.prefix}_current_staff_publication_quality.json`), "utf8").then(JSON.parse),
    fs.readFile(path.join(openalex, `${moduleInfo.prefix}_openalex_quality.json`), "utf8").then(JSON.parse),
  ]);
  let identities = [];
  try {
    identities = JSON.parse(await fs.readFile(path.join(output, `${moduleInfo.prefix}_researcher_identities.json`), "utf8"));
  } catch {}
  const identityById = new Map(identities.map((identity) => [identity.researcher_id, identity]));
  const authorById = new Map(authorMatches.map((author) => [author.researcher_id, author]));
  const researcherById = new Map(researchers.map((researcher) => [researcher.researcher_id, researcher]));
  const officialById = new Map(officialPayload.records.map((publication) => [publication.publication_id, publication]));
  const worksByResearcher = new Map(openalexWorks.map((entry) => [entry.researcher_id, new Map(entry.works.map((work) => [work.openalex_work_id, work]))]));
  const enrichedByKey = new Map(enrichedRelationships.map((relationship) => [`${relationship.researcher_id}|${relationship.publication_id}`, relationship]));

  for (const researcher of researchers) {
    const identity = identityById.get(researcher.researcher_id);
    const openalexAuthor = authorById.get(researcher.researcher_id);
    const reviewReasons = [researcher.inclusion_review_reason, identity?.review_reason, openalexAuthor?.review_reason].filter(Boolean);
    const row = {
      researcher_id: researcher.researcher_id,
      name: researcher.name_display,
      job_title: researcher.academic_title || researcher.role || "",
      academic_level: researcher.academic_level || "",
      field_of_research: moduleInfo.field,
      university: moduleInfo.university,
      profile_url: researcher.profile_url,
      email: researcher.email || "",
      orcid: identity?.orcid || researcher.orcid || "",
      openalex_author_id: openalexAuthor?.openalex_author_id || "",
      official_roster_source: researcher.source_url || "",
      inclusion_review_required: Boolean(researcher.inclusion_review_required),
      identity_review_required: Boolean(identity?.requires_review),
      openalex_review_required: Boolean(openalexAuthor?.requires_review),
      review_reason: [...new Set(reviewReasons)].join("; "),
      harvested_at: researcher.harvested_at || collectionQuality.generated_at,
    };
    staffRows.push(row);
    if (row.inclusion_review_required || row.identity_review_required || row.openalex_review_required) reviewRows.push({
      review_type: "researcher",
      university: row.university,
      field_of_research: row.field_of_research,
      researcher_id: row.researcher_id,
      researcher_name: row.name,
      publication_id: "",
      title: "",
      review_reason: row.review_reason || "Official inclusion category or persistent identity requires confirmation",
      source_url: row.profile_url,
    });
  }

  for (const relationship of officialRelationships) {
    const official = officialById.get(relationship.publication_id);
    const researcher = researcherById.get(relationship.researcher_id);
    if (!official || !researcher) continue;
    const enriched = enrichedByKey.get(`${relationship.researcher_id}|${relationship.publication_id}`);
    const oaWork = enriched?.openalex_work_id ? worksByResearcher.get(relationship.researcher_id)?.get(enriched.openalex_work_id) : null;
    const conflicts = metadataConflicts(official, oaWork);
    const authors = authorText(official.authors) || authorText(oaWork?.authors);
    const record = {
      researcher_id: relationship.researcher_id,
      name: researcher.name_display,
      job_title: researcher.academic_title || researcher.role || "",
      academic_level: researcher.academic_level || "",
      field_of_research: moduleInfo.field,
      university: moduleInfo.university,
      profile_url: researcher.profile_url,
      orcid: identityById.get(relationship.researcher_id)?.orcid || researcher.orcid || "",
      openalex_author_id: authorById.get(relationship.researcher_id)?.openalex_author_id || "",
      publication_id: official.publication_id,
      openalex_work_id: oaWork?.openalex_work_id || "",
      title: official.title,
      year: official.publication_year,
      doi: normalizeDoi(official.doi || oaWork?.doi),
      article_url: official.article_url || oaWork?.article_url,
      authors,
      author_count: oaWork?.author_count || authorCount(official.authors) || official.author_count || "",
      publication_type: official.publication_type || oaWork?.publication_type || "",
      journal_name: official.journal_name || oaWork?.journal_name || "",
      issn: official.issn || oaWork?.issn || "",
      eissn: official.eissn || oaWork?.eissn || "",
      source: official.official_source,
      source_url: official.source_url || official.article_url,
      additional_source: oaWork ? "OpenAlex" : "",
      repository_verified: true,
      record_origin: "official_repository",
      researcher_match_method: relationship.researcher_match_method,
      researcher_match_confidence: relationship.researcher_match_confidence,
      openalex_match_method: enriched?.openalex_match_method || "unmatched",
      metadata_conflict: conflicts.length > 0,
      metadata_conflict_fields: conflicts.join("; "),
      cited_by_count: oaWork?.cited_by_count ?? "",
      fwci: oaWork?.fwci ?? "",
      citation_percentile: oaWork?.citation_percentile ?? "",
      is_retracted: oaWork?.is_retracted ?? false,
      is_open_access: oaWork?.is_open_access ?? official.open_access ?? "",
      open_access_status: oaWork?.open_access_status || official.open_access_status || "",
      requires_review: Boolean(relationship.requires_review || conflicts.length || oaWork?.is_retracted),
      review_reason: [relationship.review_reason, conflicts.length ? `Official/OpenAlex metadata conflict: ${conflicts.join(", ")}` : "", oaWork?.is_retracted ? "OpenAlex marks this work as retracted" : ""].filter(Boolean).join("; "),
      harvested_at: officialPayload.harvested_at || collectionQuality.generated_at,
      openalex_retrieved_at: enriched?.openalex_retrieved_at || "",
    };
    relationRows.push({ ...record, ...lookupRanking(record, rankings), ...lookupClarivate(record, clarivate) });
  }

  for (const row of openalexOnly) {
    const researcher = researcherById.get(row.researcher_id);
    if (!researcher) continue;
    const authors = authorText(row.authors);
    const record = {
      researcher_id: row.researcher_id,
      name: researcher.name_display,
      job_title: researcher.academic_title || researcher.role || "",
      academic_level: researcher.academic_level || "",
      field_of_research: moduleInfo.field,
      university: moduleInfo.university,
      profile_url: researcher.profile_url,
      orcid: identityById.get(row.researcher_id)?.orcid || researcher.orcid || "",
      openalex_author_id: row.openalex_author_id,
      publication_id: stableId("openalex-pub", row.doi || row.openalex_work_id),
      openalex_work_id: row.openalex_work_id,
      title: row.title,
      year: row.publication_year,
      doi: normalizeDoi(row.doi),
      article_url: row.article_url,
      authors,
      author_count: row.author_count,
      publication_type: row.publication_type,
      journal_name: row.journal_name,
      issn: row.issn,
      eissn: row.eissn,
      source: "OpenAlex",
      source_url: row.article_url || `https://openalex.org/${row.openalex_work_id}`,
      additional_source: "",
      repository_verified: false,
      record_origin: "openalex_orcid_only",
      researcher_match_method: "official_orcid_to_openalex_author",
      researcher_match_confidence: "high",
      openalex_match_method: "author_orcid",
      metadata_conflict: false,
      metadata_conflict_fields: "",
      cited_by_count: row.cited_by_count ?? "",
      fwci: row.fwci ?? "",
      citation_percentile: row.citation_percentile ?? "",
      is_retracted: row.is_retracted,
      is_open_access: row.is_open_access ?? "",
      open_access_status: row.open_access_status,
      requires_review: Boolean(row.requires_review),
      review_reason: row.review_reason,
      harvested_at: "",
      openalex_retrieved_at: row.openalex_retrieved_at,
    };
    relationRows.push({ ...record, ...lookupRanking(record, rankings), ...lookupClarivate(record, clarivate) });
  }
  moduleQuality.push({
    module: moduleInfo.prefix,
    university: moduleInfo.university,
    field_of_research: moduleInfo.field,
    staff_records: collectionQuality.staff_records,
    official_unique_publications: collectionQuality.extracted_unique_publications ?? collectionQuality.combined_unique_publications,
    official_relationships: officialRelationships.length,
    openalex_authors_high_confidence: openalexQuality.openalex_authors_high_confidence,
    openalex_orcid_only_relationships: openalexQuality.openalex_orcid_only_work_links,
    official_source_search_failures: (collectionQuality.search_failures || collectionQuality.detail_failures || []).length,
    official_count_reconciliation_warnings: (collectionQuality.profile_feeds_not_reconciled || []).length,
  });
}

// Keep one row per researcher/publication. Prefer an official record over an OpenAlex-only duplicate.
const relationMap = new Map();
for (const row of relationRows.sort((a, b) => Number(b.repository_verified) - Number(a.repository_verified))) {
  const key = `${row.researcher_id}|${titleYearKey(row)}`;
  const existing = relationMap.get(key);
  if (!existing) relationMap.set(key, row);
  else if (normalizeDoi(existing.doi) && normalizeDoi(row.doi) && normalizeDoi(existing.doi) !== normalizeDoi(row.doi)) {
    reviewRows.push({
      review_type: "duplicate_publication_version",
      university: row.university,
      field_of_research: row.field_of_research,
      researcher_id: row.researcher_id,
      researcher_name: row.name,
      publication_id: row.publication_id,
      title: row.title,
      review_reason: `Same normalized title/year has different DOI versions (${existing.doi}; ${row.doi}); primary CSV retained the higher-priority record`,
      source_url: row.source_url,
    });
  }
}
const deduplicatedRelations = [...relationMap.values()].sort((a, b) =>
  a.university.localeCompare(b.university)
  || a.field_of_research.localeCompare(b.field_of_research)
  || a.name.localeCompare(b.name)
  || Number(b.year || 0) - Number(a.year || 0)
  || a.title.localeCompare(b.title));

const rowsExcludedFromPrimary = deduplicatedRelations.filter((row) => !row.title);
for (const row of rowsExcludedFromPrimary) reviewRows.push({
  review_type: "excluded_publication_relationship",
  university: row.university,
  field_of_research: row.field_of_research,
  researcher_id: row.researcher_id,
  researcher_name: row.name,
  publication_id: row.publication_id,
  title: "",
  review_reason: "Excluded from primary CSV because the source record has no title",
  source_url: row.source_url,
});
const finalRelations = deduplicatedRelations.filter((row) => row.title);

for (const row of finalRelations) {
  const reasons = [];
  if (row.requires_review) reasons.push(row.review_reason || "Relationship requires review");
  if (!row.title) reasons.push("Missing title");
  if (!/^\d{4}$/.test(String(row.year)) || Number(row.year) < 1800 || Number(row.year) > new Date().getFullYear() + 1) reasons.push("Missing or invalid publication year");
  if (row.doi && !/^10\.\d{4,9}\/.+/.test(row.doi)) reasons.push("DOI format requires review");
  if ((row.issn && !/^\d{4}-\d{3}[\dX]$/i.test(row.issn)) || (row.eissn && !/^\d{4}-\d{3}[\dX]$/i.test(row.eissn))) reasons.push("ISSN format requires review");
  if (row.abdc_match_status === "ambiguous") reasons.push("ABDC match is ambiguous");
  if (reasons.length) {
    row.requires_review = true;
    row.review_reason = [...new Set([row.review_reason, ...reasons].filter(Boolean))].join("; ");
    reviewRows.push({
      review_type: "publication_relationship",
      university: row.university,
      field_of_research: row.field_of_research,
      researcher_id: row.researcher_id,
      researcher_name: row.name,
      publication_id: row.publication_id,
      title: row.title,
      review_reason: row.review_reason,
      source_url: row.source_url,
    });
  }
}

// Build publication entities with transitive DOI and title/year deduplication, then assign a true foreign key to every relationship.
const parents = finalRelations.map((_, index) => index);
const findRoot = (index) => {
  while (parents[index] !== index) {
    parents[index] = parents[parents[index]];
    index = parents[index];
  }
  return index;
};
const union = (left, right) => {
  const a = findRoot(left);
  const b = findRoot(right);
  if (a !== b) parents[b] = a;
};
const firstByDoi = new Map();
const firstByTitleYear = new Map();
for (const [index, row] of finalRelations.entries()) {
  const doi = normalizeDoi(row.doi);
  if (doi) {
    if (firstByDoi.has(doi)) union(index, firstByDoi.get(doi));
    else firstByDoi.set(doi, index);
  }
  const titleYear = titleYearKey(row);
  if (titleYear.startsWith("title-year:")) {
    if (firstByTitleYear.has(titleYear)) union(index, firstByTitleYear.get(titleYear));
    else firstByTitleYear.set(titleYear, index);
  }
}
const publicationGroups = new Map();
for (let index = 0; index < finalRelations.length; index += 1) {
  const root = findRoot(index);
  if (!publicationGroups.has(root)) publicationGroups.set(root, []);
  publicationGroups.get(root).push(index);
}
const publications = [];
for (const indexes of publicationGroups.values()) {
  const rows = indexes.map((index) => finalRelations[index]);
  const primary = [...rows].sort((a, b) =>
    Number(b.repository_verified) - Number(a.repository_verified)
    || Number(Boolean(b.doi)) - Number(Boolean(a.doi))
    || Number(Boolean(b.issn || b.eissn)) - Number(Boolean(a.issn || a.eissn))
    || Number(b.cited_by_count || 0) - Number(a.cited_by_count || 0))[0];
  const groupIdentity = [
    ...new Set(rows.map((row) => normalizeDoi(row.doi)).filter(Boolean)),
    ...new Set(rows.map(titleYearKey).filter((key) => key.startsWith("title-year:"))),
  ].sort().join("|");
  const publicationId = stableId("publication", groupIdentity || primary.publication_id);
  for (const index of indexes) {
    finalRelations[index].source_publication_id = finalRelations[index].publication_id;
    finalRelations[index].publication_id = publicationId;
  }
  const firstValue = (field) => rows.map((row) => row[field]).find((value) => value !== "" && value !== null && value !== undefined) ?? "";
  const citationValues = rows.map((row) => row.cited_by_count).filter((value) => value !== "" && value !== null && value !== undefined).map(Number);
  publications.push({
    publication_id: publicationId,
    title: primary.title,
    year: primary.year,
    doi: firstValue("doi"),
    article_url: firstValue("article_url"),
    authors: firstValue("authors"),
    author_count: firstValue("author_count"),
    publication_type: firstValue("publication_type"),
    journal_name: firstValue("journal_name"),
    issn: firstValue("issn"),
    eissn: firstValue("eissn"),
    quality_rank: firstValue("quality_rank"),
    abdc_for: firstValue("abdc_for"),
    abdc_match_method: firstValue("abdc_match_method"),
    abdc_match_status: firstValue("abdc_match_status"),
    abdc_list_version: firstValue("abdc_list_version"),
    impact_factor: firstValue("impact_factor"),
    five_year_impact_factor: firstValue("five_year_impact_factor"),
    jcr_year: firstValue("jcr_year"),
    jcr_journal_id: firstValue("jcr_journal_id"),
    clarivate_match_method: firstValue("clarivate_match_method"),
    clarivate_match_status: firstValue("clarivate_match_status"),
    impact_factor_status: firstValue("impact_factor_status"),
    clarivate_retrieved_at: firstValue("clarivate_retrieved_at"),
    cited_by_count: citationValues.length ? Math.max(...citationValues) : "",
    fwci: firstValue("fwci"),
    citation_percentile: firstValue("citation_percentile"),
    source: primary.source,
    source_url: primary.source_url,
    repository_verified: rows.some((row) => row.repository_verified),
    requires_review: rows.some((row) => row.metadata_conflict || row.is_retracted),
  });
}
publications.sort((a, b) => Number(b.year || 0) - Number(a.year || 0) || a.title.localeCompare(b.title));

const journalMap = new Map();
for (const row of publications) {
  if ((!row.issn && !row.eissn && !row.quality_rank) || (!row.journal_name && !row.issn && !row.eissn)) continue;
  const key = normalizeIssn(row.issn || row.eissn) || normalizeText(row.journal_name);
  const existing = journalMap.get(key) || {
    journal_id: stableId("journal", key),
    journal_name: row.journal_name,
    issn: row.issn,
    eissn: row.eissn,
    quality_rank: row.quality_rank,
    abdc_for: row.abdc_for,
    abdc_match_method: row.abdc_match_method,
    abdc_match_status: row.abdc_match_status,
    abdc_list_version: row.abdc_list_version,
    ...lookupClarivate(row, clarivate, stableId("journal", key)),
    publication_count: 0,
  };
  existing.publication_count += 1;
  if (!existing.journal_name && row.journal_name) existing.journal_name = row.journal_name;
  if (!existing.issn && row.issn) existing.issn = row.issn;
  if (!existing.eissn && row.eissn) existing.eissn = row.eissn;
  journalMap.set(key, existing);
}
const journals = [...journalMap.values()].sort((a, b) => a.journal_name.localeCompare(b.journal_name));

for (const journal of journals) {
  if (!["ambiguous", "title_candidate_only", "error"].includes(journal.clarivate_match_status)) continue;
  const metricRecord = clarivate.byLocalJournalId.get(journal.journal_id);
  reviewRows.push({
    review_type: "journal_metric",
    university: "",
    field_of_research: "",
    researcher_id: "",
    researcher_name: "",
    publication_id: "",
    title: journal.journal_name,
    review_reason: metricRecord?.review_reason || `Clarivate journal match status: ${journal.clarivate_match_status}`,
    source_url: journal.jcr_journal_id ? `${"https://api.clarivate.com/apis/wos-journals/v1/journals/"}${journal.jcr_journal_id}` : "",
  });
}

const relationshipHeaders = [
  "researcher_id", "name", "job_title", "academic_level", "field_of_research", "university", "profile_url", "orcid",
  "openalex_author_id", "publication_id", "source_publication_id", "openalex_work_id", "title", "year", "doi", "article_url", "authors",
  "author_count", "publication_type", "journal_name", "issn", "eissn", "quality_rank", "abdc_for", "abdc_match_method",
  "abdc_match_status", "abdc_list_version", "impact_factor", "five_year_impact_factor", "jcr_year", "jcr_journal_id",
  "clarivate_match_method", "clarivate_match_status", "impact_factor_status", "clarivate_retrieved_at", "cited_by_count", "fwci",
  "citation_percentile", "source", "source_url", "additional_source", "repository_verified", "record_origin",
  "researcher_match_method", "researcher_match_confidence", "openalex_match_method", "metadata_conflict",
  "metadata_conflict_fields", "is_retracted", "is_open_access", "open_access_status", "requires_review", "review_reason",
  "harvested_at", "openalex_retrieved_at",
];
const staffHeaders = [
  "researcher_id", "name", "job_title", "academic_level", "field_of_research", "university", "profile_url", "email", "orcid",
  "openalex_author_id", "official_roster_source", "inclusion_review_required", "identity_review_required", "openalex_review_required",
  "review_reason", "harvested_at",
];
const publicationHeaders = [
  "publication_id", "title", "year", "doi", "article_url", "authors", "author_count", "publication_type", "journal_name",
  "issn", "eissn", "quality_rank", "abdc_for", "abdc_match_method", "abdc_match_status", "abdc_list_version",
  "impact_factor", "five_year_impact_factor", "jcr_year", "jcr_journal_id", "clarivate_match_method",
  "clarivate_match_status", "impact_factor_status", "clarivate_retrieved_at", "cited_by_count", "fwci", "citation_percentile", "source", "source_url",
  "repository_verified", "requires_review",
];
const qualitySummary = {
  generated_at: new Date().toISOString(),
  scope: "Current official Accounting and Finance rosters at UWA and the University of Melbourne; official repository records plus OpenAlex works accepted only after exact ORCID author matching.",
  caveat: "OpenAlex-only rows are not university-repository verified. Name-only repository links and all uncertain inclusion categories remain visible and are exported to the review queue.",
  staff_rows: staffRows.length,
  unique_staff_ids: new Set(staffRows.map((row) => row.researcher_id)).size,
  researcher_publication_rows: finalRelations.length,
  high_confidence_rows: finalRelations.filter((row) => row.researcher_match_confidence === "high" && !row.requires_review).length,
  official_repository_rows: finalRelations.filter((row) => row.repository_verified).length,
  openalex_orcid_only_rows: finalRelations.filter((row) => row.record_origin === "openalex_orcid_only").length,
  unique_publications: publications.length,
  publication_rows_with_doi: finalRelations.filter((row) => row.doi).length,
  publication_rows_with_issn: finalRelations.filter((row) => row.issn || row.eissn).length,
  publication_rows_with_abdc_rank: finalRelations.filter((row) => row.quality_rank).length,
  journal_rows_processed_by_clarivate: journals.filter((row) => row.clarivate_match_status !== "not_collected").length,
  journal_rows_matched_to_clarivate: journals.filter((row) => row.clarivate_match_status === "matched").length,
  journal_rows_with_impact_factor: journals.filter((row) => row.impact_factor).length,
  journal_rows_with_five_year_impact_factor: journals.filter((row) => row.five_year_impact_factor).length,
  publication_rows_with_impact_factor: finalRelations.filter((row) => row.impact_factor).length,
  publication_rows_with_openalex_citations: finalRelations.filter((row) => row.cited_by_count !== "").length,
  review_queue_rows: reviewRows.length,
  duplicate_researcher_publication_rows_removed: relationRows.length - deduplicatedRelations.length,
  rows_excluded_from_primary_for_missing_required_fields: rowsExcludedFromPrimary.length,
  modules: moduleQuality,
};

await fs.mkdir(OUTPUT_DIR, { recursive: true });
await Promise.all([
  writeCsv(path.join(OUTPUT_DIR, "client_researcher_publications.csv"), relationshipHeaders, finalRelations),
  writeCsv(path.join(OUTPUT_DIR, "client_high_confidence_researcher_publications.csv"), relationshipHeaders, finalRelations.filter((row) => row.researcher_match_confidence === "high" && !row.requires_review)),
  writeCsv(path.join(OUTPUT_DIR, "staff.csv"), staffHeaders, staffRows),
  writeCsv(path.join(OUTPUT_DIR, "publications.csv"), publicationHeaders, publications),
  writeCsv(path.join(OUTPUT_DIR, "journals.csv"), Object.keys(journals[0] || {}), journals),
  writeCsv(path.join(OUTPUT_DIR, "review_queue.csv"), ["review_type", "university", "field_of_research", "researcher_id", "researcher_name", "publication_id", "title", "review_reason", "source_url"], reviewRows),
  writeCsv(path.join(OUTPUT_DIR, "quality_summary.csv"), [
    "generated_at", "staff_rows", "unique_staff_ids", "researcher_publication_rows", "high_confidence_rows", "official_repository_rows",
    "openalex_orcid_only_rows", "unique_publications", "publication_rows_with_doi", "publication_rows_with_issn",
    "publication_rows_with_abdc_rank", "journal_rows_processed_by_clarivate", "journal_rows_matched_to_clarivate",
    "journal_rows_with_impact_factor", "journal_rows_with_five_year_impact_factor", "publication_rows_with_impact_factor",
    "publication_rows_with_openalex_citations", "review_queue_rows",
    "duplicate_researcher_publication_rows_removed", "rows_excluded_from_primary_for_missing_required_fields",
  ], [qualitySummary]),
  fs.writeFile(path.join(OUTPUT_DIR, "quality_summary.json"), `${JSON.stringify(qualitySummary, null, 2)}\n`, "utf8"),
]);

console.log(JSON.stringify(qualitySummary, null, 2));
