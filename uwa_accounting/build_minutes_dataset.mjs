import fs from "node:fs/promises";
import path from "node:path";

const INPUT_DIR = path.resolve(process.argv[2] || "output");
const RANKING_FILE = path.resolve(process.argv[3] || "data/abdc_2025.csv");
const OUTPUT_DIR = path.resolve(process.argv[4] || INPUT_DIR);

const clean = (value) => String(value ?? "").replace(/\s+/g, " ").trim();
const csvCell = (value) => `"${String(value ?? "").replaceAll('"', '""')}"`;

function parseCsv(text) {
  const rows = [];
  let row = [];
  let cell = "";
  let quoted = false;
  for (let index = 0; index < text.length; index += 1) {
    const character = text[index];
    if (quoted) {
      if (character === '"' && text[index + 1] === '"') {
        cell += '"';
        index += 1;
      } else if (character === '"') {
        quoted = false;
      } else {
        cell += character;
      }
    } else if (character === '"') {
      quoted = true;
    } else if (character === ",") {
      row.push(cell);
      cell = "";
    } else if (character === "\n") {
      row.push(cell.replace(/\r$/, ""));
      if (row.some((value) => value !== "")) rows.push(row);
      row = [];
      cell = "";
    } else {
      cell += character;
    }
  }
  if (cell || row.length) {
    row.push(cell);
    rows.push(row);
  }
  const [headers, ...data] = rows;
  return data.map((values) => Object.fromEntries(headers.map((header, index) => [header, values[index] || ""])));
}

function writeCsv(records, columns, filename) {
  const rows = [
    columns.map(csvCell).join(","),
    ...records.map((record) =>
      columns
        .map((column) => csvCell(Array.isArray(record[column]) ? record[column].join("; ") : record[column]))
        .join(","),
    ),
  ];
  return fs.writeFile(path.join(OUTPUT_DIR, filename), `${rows.join("\n")}\n`, "utf8");
}

function normalizeIssn(value) {
  const compact = clean(value).toUpperCase().replace(/[^0-9X]/g, "");
  return compact.length === 8 ? compact : "";
}

function normalizeTitle(value) {
  return clean(value)
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/^the\s+/, "")
    .replace(/&/g, " and ")
    .replace(/[^a-z0-9]+/g, " ")
    .trim();
}

const PERSON_TITLES = new Set(["dr", "doctor", "prof", "professor", "associate", "mr", "ms", "mrs", "miss"]);

function personKeys(value) {
  const original = clean(value)
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase();
  const withoutParentheses = original.replace(/\([^)]*\)/g, " ");
  const parentheticalNames = [...original.matchAll(/\(([^)]*)\)/g)].map((match) => match[1]);
  const baseTokens = withoutParentheses
    .replace(/[^a-z0-9]+/g, " ")
    .split(" ")
    .filter(Boolean)
    .filter((token) => !PERSON_TITLES.has(token));
  const variants = [baseTokens];
  for (const nickname of parentheticalNames) {
    const nicknameTokens = nickname.replace(/[^a-z0-9]+/g, " ").split(" ").filter(Boolean);
    if (nicknameTokens.length && baseTokens.length >= 2) {
      variants.push([baseTokens[0], ...nicknameTokens, baseTokens.at(-1)]);
    }
  }
  return new Set(variants.map((tokens) => [...new Set(tokens)].sort().join(" ")).filter(Boolean));
}

function buildRankingIndexes(rankings) {
  const byIssn = new Map();
  const byTitle = new Map();
  const append = (index, key, ranking) => {
    if (!key) return;
    if (!index.has(key)) index.set(key, []);
    index.get(key).push(ranking);
  };
  for (const ranking of rankings) {
    append(byIssn, normalizeIssn(ranking.issn), ranking);
    append(byIssn, normalizeIssn(ranking.online_issn), ranking);
    append(byTitle, normalizeTitle(ranking.journal_title), ranking);
  }
  return { byIssn, byTitle };
}

function consolidateRankings(candidates, method) {
  const unique = [...new Map(candidates.map((candidate) => [
    `${candidate.journal_title}|${candidate.abdc_rating}|${candidate.field_of_research}`,
    candidate,
  ])).values()];
  if (!unique.length) {
    return {
      abdc_rating: "",
      abdc_for: "",
      abdc_journal_title: "",
      abdc_match_method: "unmatched",
      abdc_match_status: "unmatched",
      abdc_list_version: "2025-v2-270526",
    };
  }
  const ratings = [...new Set(unique.map((entry) => entry.abdc_rating))];
  const titles = [...new Set(unique.map((entry) => entry.journal_title))];
  return {
    abdc_rating: ratings.length === 1 ? ratings[0] : "",
    abdc_for: [...new Set(unique.map((entry) => entry.field_of_research))].join("; "),
    abdc_journal_title: titles.join("; "),
    abdc_match_method: method,
    abdc_match_status: ratings.length === 1 ? "matched" : "ambiguous",
    abdc_list_version: unique[0].abdc_list_version,
  };
}

function lookupRanking(publication, indexes) {
  const issnKeys = [...new Set([normalizeIssn(publication.issn), normalizeIssn(publication.eissn)].filter(Boolean))];
  const issnCandidates = issnKeys.flatMap((key) => indexes.byIssn.get(key) || []);
  if (issnCandidates.length) return consolidateRankings(issnCandidates, "ISSN/eISSN");
  const titleCandidates = indexes.byTitle.get(normalizeTitle(publication.journal)) || [];
  return consolidateRankings(titleCandidates, titleCandidates.length ? "normalized journal title" : "unmatched");
}

const [staffPayload, publicationPayload, rankingText] = await Promise.all([
  fs.readFile(path.join(INPUT_DIR, "uwa_accounting_staff.json"), "utf8").then(JSON.parse),
  fs.readFile(path.join(INPUT_DIR, "uwa_accounting_publications.json"), "utf8").then(JSON.parse),
  fs.readFile(RANKING_FILE, "utf8"),
]);
await fs.mkdir(OUTPUT_DIR, { recursive: true });

const rankings = parseCsv(rankingText);
const rankingIndexes = buildRankingIndexes(rankings);
const researchers = staffPayload.records.map((researcher, index) => ({
  researcher_id: `uwa-accounting-${String(index + 1).padStart(3, "0")}`,
  ...researcher,
  source_url: staffPayload.source_page,
  harvested_at: staffPayload.harvested_at,
}));

const researcherKeyIndex = new Map();
const researcherProfileIndex = new Map();
for (const researcher of researchers) {
  researcherProfileIndex.set(researcher.profile_url.replace(/\/$/, ""), researcher);
  for (const key of personKeys(researcher.name_display)) {
    if (!researcherKeyIndex.has(key)) researcherKeyIndex.set(key, []);
    researcherKeyIndex.get(key).push(researcher);
  }
}

const publications = publicationPayload.records.map((publication) => ({
  ...publication,
  publication_year:
    publication.publication_year || clean(publication.issued_date).match(/\b(18|19|20)\d{2}\b/)?.[0] || "",
  article_url:
    publication.article_url ||
    (publication.doi ? `https://doi.org/${publication.doi.replace(/^https?:\/\/(?:dx\.)?doi\.org\//i, "")}` : "") ||
    publication.open_access_url ||
    publication.item_url,
  ...lookupRanking(publication, rankingIndexes),
  source_url: publicationPayload.source_page,
  harvested_at: publicationPayload.harvested_at,
}));

const researcherPublications = [];
const unmatchedPublicationIds = [];
const ambiguousPublicationIds = [];
for (const publication of publications) {
  const matches = new Map();
  for (const author of publication.uwa_authors || []) {
    const exactResearcher = researcherProfileIndex.get(clean(author.profile_url).replace(/\/$/, ""));
    if (exactResearcher) {
      matches.set(exactResearcher.researcher_id, { researcher: exactResearcher, author, method: "Pure profile URL", confidence: "high" });
      continue;
    }
    for (const key of personKeys(author.name)) {
      for (const researcher of researcherKeyIndex.get(key) || []) {
        matches.set(researcher.researcher_id, { researcher, author, method: "normalized full name", confidence: "medium - requires validation" });
      }
    }
  }
  if (matches.size === 0) unmatchedPublicationIds.push(publication.publication_id);
  if (matches.size > 1) ambiguousPublicationIds.push(publication.publication_id);
  for (const { researcher, author, method, confidence } of matches.values()) {
    researcherPublications.push({
      researcher_id: researcher.researcher_id,
      researcher_name: researcher.name_display,
      academic_title: researcher.academic_title || researcher.role,
      academic_level: researcher.academic_level,
      publication_id: publication.publication_id,
      publication_title: publication.title,
      publication_year: publication.publication_year,
      article_url: publication.article_url,
      doi: publication.doi,
      journal: publication.journal,
      issn: publication.issn,
      eissn: publication.eissn,
      abdc_rating: publication.abdc_rating,
      abdc_for: publication.abdc_for,
      author_name_from_repository: author.name,
      researcher_match_method: method,
      researcher_match_confidence: confidence,
      abdc_match_method: publication.abdc_match_method,
      abdc_match_status: publication.abdc_match_status,
    });
  }
}

const linksByResearcher = new Map();
for (const link of researcherPublications) {
  if (!linksByResearcher.has(link.researcher_id)) linksByResearcher.set(link.researcher_id, []);
  linksByResearcher.get(link.researcher_id).push(link);
}
const researcherSummary = researchers.map((researcher) => {
  const links = linksByResearcher.get(researcher.researcher_id) || [];
  const countRating = (rating) => links.filter((link) => link.abdc_rating === rating).length;
  return {
    researcher_id: researcher.researcher_id,
    researcher_name: researcher.name_display,
    academic_title: researcher.academic_title || researcher.role,
    academic_level: researcher.academic_level,
    inclusion_review_required: researcher.inclusion_review_required,
    publication_count_in_pure_collection: links.length,
    abdc_ranked_publication_count: links.filter((link) => link.abdc_rating).length,
    abdc_a_star_count: countRating("A*"),
    abdc_a_count: countRating("A"),
    abdc_b_count: countRating("B"),
    abdc_c_count: countRating("C"),
    unranked_publication_count: links.filter((link) => !link.abdc_rating).length,
  };
});

const dataQuality = {
  generated_at: new Date().toISOString(),
  scope: "University of Western Australia Accounting proof of concept",
  staff_records: researchers.length,
  staff_records_with_academic_level: researchers.filter((record) => record.academic_level).length,
  staff_records_requiring_inclusion_review: researchers.filter((record) => record.inclusion_review_required).length,
  publication_records: publications.length,
  pure_reported_profile_count: staffPayload.reported_profile_count,
  pure_extracted_profile_count: staffPayload.extracted_accounting_profile_count,
  pure_profile_count_reconciles: staffPayload.count_reconciles,
  pure_reported_record_count: publicationPayload.reported_organisation_publication_count,
  pure_extracted_record_count: publicationPayload.extracted_accounting_publication_count,
  pure_count_reconciles: publicationPayload.count_reconciles,
  publication_details_without_visible_accounting_affiliation:
    publicationPayload.publication_details_without_visible_accounting_affiliation.length,
  publications_with_year: publications.filter((record) => record.publication_year).length,
  publications_with_article_url: publications.filter((record) => record.article_url).length,
  publications_with_abdc_rating: publications.filter((record) => record.abdc_rating).length,
  publications_with_ambiguous_abdc_match: publications.filter((record) => record.abdc_match_status === "ambiguous").length,
  researcher_publication_links: researcherPublications.length,
  publications_not_matched_to_current_staff: unmatchedPublicationIds.length,
  publications_matched_to_multiple_current_staff: ambiguousPublicationIds.length,
  warnings: [
    ...(publicationPayload.count_reconciles
      ? []
      : [
          `Pure reported ${publicationPayload.reported_organisation_publication_count} organisation outputs while ${publicationPayload.extracted_accounting_publication_count} were extracted; investigate before claiming completeness.`,
        ]),
    ...(publicationPayload.publication_details_without_visible_accounting_affiliation.length
      ? [`${publicationPayload.publication_details_without_visible_accounting_affiliation.length} RSS-listed publications do not expose an Accounting organisation link on their detail page; RSS membership was retained as authoritative.`]
      : []),
    "The Pure Accounting organisation output list is not guaranteed to contain every publication for every current researcher.",
    "Researcher-publication links primarily use exact Pure profile URLs; any name-based fallback still requires validation.",
    "Staff inclusion rules for teaching-only, professional, emeritus, visitor and postdoctoral roles require client confirmation.",
    "ABDC ratings use the official 2025-v2 list current to May 2026; confirm the required ranking version with the client.",
  ],
};

await Promise.all([
  fs.writeFile(path.join(OUTPUT_DIR, "uwa_accounting_researchers.json"), `${JSON.stringify(researchers, null, 2)}\n`),
  fs.writeFile(path.join(OUTPUT_DIR, "uwa_accounting_publications_ranked.json"), `${JSON.stringify(publications, null, 2)}\n`),
  fs.writeFile(
    path.join(OUTPUT_DIR, "uwa_accounting_researcher_publications.json"),
    `${JSON.stringify(researcherPublications, null, 2)}\n`,
  ),
  fs.writeFile(path.join(OUTPUT_DIR, "uwa_accounting_researcher_summary.json"), `${JSON.stringify(researcherSummary, null, 2)}\n`),
  fs.writeFile(path.join(OUTPUT_DIR, "uwa_accounting_data_quality.json"), `${JSON.stringify(dataQuality, null, 2)}\n`),
  writeCsv(
    researchers,
    [
      "researcher_id", "name_display", "academic_title", "academic_level", "academic_level_source", "department", "person_type",
      "email", "orcid", "scopus_profile_url", "profile_url", "reported_profile_output_count",
      "inclusion_review_required", "inclusion_review_reason", "source_url",
      "harvested_at",
    ],
    "uwa_accounting_researchers.csv",
  ),
  writeCsv(
    publications,
    [
      "publication_id", "title", "publication_year", "publication_status", "article_url", "doi", "journal", "issn", "eissn",
      "volume", "issue", "pages", "authors", "item_type", "open_access", "accounting_affiliation_observed",
      "abdc_rating", "abdc_for", "abdc_journal_title", "abdc_match_method", "abdc_match_status", "abdc_list_version",
      "source_url", "harvested_at",
    ],
    "uwa_accounting_publications_ranked.csv",
  ),
  writeCsv(
    researcherPublications,
    [
      "researcher_id", "researcher_name", "academic_title", "academic_level", "publication_id", "publication_title",
      "publication_year", "article_url", "doi", "journal", "issn", "eissn", "abdc_rating", "abdc_for",
      "author_name_from_repository", "researcher_match_method", "researcher_match_confidence", "abdc_match_method",
      "abdc_match_status",
    ],
    "uwa_accounting_researcher_publications.csv",
  ),
  writeCsv(
    researcherSummary,
    [
      "researcher_id", "researcher_name", "academic_title", "academic_level", "inclusion_review_required",
      "publication_count_in_pure_collection", "abdc_ranked_publication_count", "abdc_a_star_count", "abdc_a_count",
      "abdc_b_count", "abdc_c_count", "unranked_publication_count",
    ],
    "uwa_accounting_researcher_summary.csv",
  ),
]);

console.log(JSON.stringify(dataQuality, null, 2));
