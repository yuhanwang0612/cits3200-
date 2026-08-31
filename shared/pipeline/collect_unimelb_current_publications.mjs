import fs from "node:fs/promises";
import path from "node:path";
import {
  clean,
  extractIssns,
  fetchWithRetry,
  normalizeDoi,
  parallelMap,
  personKey,
  sleep,
  stableId,
  writeCsv,
} from "./common.mjs";

const SEARCH_URL = "https://minerva-access.unimelb.edu.au/server/api/discover/search/objects";

function values(metadata, key) {
  return (metadata?.[key] || []).map((entry) => clean(entry.value)).filter(Boolean);
}

function first(metadata, key) {
  return values(metadata, key)[0] || "";
}

function parseInternalAuthor(value) {
  const parts = clean(value).split(";").map(clean).filter(Boolean);
  const orcid = clean(value).match(/\b\d{4}-\d{4}-\d{4}-\d{3}[\dX]\b/i)?.[0].toUpperCase() || "";
  const internalId = parts.slice(1).find((part) => /^\d+$/.test(part)) || "";
  return { name: parts[0] || "", internal_id: internalId, orcid, raw: clean(value) };
}

function parseItem(wrapper) {
  const item = wrapper?._embedded?.indexableObject || wrapper;
  if (!item || item.type !== "item") return null;
  const metadata = item.metadata || {};
  const doi = normalizeDoi(first(metadata, "dc.identifier.doi"));
  const issuedDate = first(metadata, "dc.date.issued");
  const itemUrl = item.handle ? `https://hdl.handle.net/${item.handle}` : "";
  const openAccessUrl = first(metadata, "melbourne.openaccess.url");
  const internalAuthors = values(metadata, "melbourne.internal.authorids").map(parseInternalAuthor).filter((entry) => entry.name);
  const issns = extractIssns(first(metadata, "dc.identifier.issn"), first(metadata, "dc.identifier.eissn"));
  return {
    publication_id: stableId("unimelb-pub", doi || item.uuid || itemUrl),
    source_record_id: item.uuid || item.id || "",
    item_uuid: item.uuid || item.id || "",
    handle: item.handle || "",
    item_url: itemUrl,
    title: first(metadata, "dc.title") || clean(item.name),
    publication_year: issuedDate.match(/\b(18|19|20)\d{2}\b/)?.[0] || "",
    issued_date: issuedDate,
    article_url: doi ? `https://doi.org/${doi}` : openAccessUrl || itemUrl,
    doi,
    journal_name: first(metadata, "melbourne.source.title"),
    issn: issns[0] || "",
    eissn: issns[1] || "",
    all_issns: issns,
    volume: first(metadata, "melbourne.source.volume"),
    issue: first(metadata, "melbourne.source.issue"),
    pages: first(metadata, "melbourne.source.pages"),
    authors: values(metadata, "dc.contributor.author"),
    melbourne_authors: values(metadata, "melbourne.contributor.author"),
    internal_authors: internalAuthors,
    author_count: values(metadata, "dc.contributor.author").length || "",
    publication_type: first(metadata, "dc.type"),
    publisher: first(metadata, "dc.publisher"),
    departments: values(metadata, "melbourne.affiliation.department"),
    faculties: values(metadata, "melbourne.affiliation.faculty"),
    open_access_status: first(metadata, "melbourne.openaccess.status"),
    open_access_url: openAccessUrl,
    last_modified: item.lastModified || "",
    official_source: "University of Melbourne Minerva Access",
    source_url: itemUrl,
  };
}

function identityKey(identity) {
  return `${identity.internal_id}|${identity.orcid}`;
}

function buildResearcherIdentities(researchers, seedPublications) {
  const byName = new Map();
  for (const publication of seedPublications) {
    for (const identity of publication.internal_authors) {
      const key = personKey(identity.name);
      if (!key) continue;
      if (!byName.has(key)) byName.set(key, new Map());
      const map = byName.get(key);
      const existing = map.get(identity.internal_id || identity.orcid || identity.raw) || identity;
      map.set(identity.internal_id || identity.orcid || identity.raw, {
        name: existing.name || identity.name,
        internal_id: existing.internal_id || identity.internal_id,
        orcid: existing.orcid || identity.orcid,
        raw: existing.raw || identity.raw,
      });
    }
  }
  return researchers.map((researcher) => {
    const candidates = [...(byName.get(personKey(researcher.name_display))?.values() || [])];
    const internalIds = [...new Set(candidates.map((entry) => entry.internal_id).filter(Boolean))];
    const orcids = [...new Set(candidates.map((entry) => entry.orcid).filter(Boolean))];
    const unambiguous = internalIds.length <= 1 && orcids.length <= 1 && candidates.length > 0;
    return {
      researcher_id: researcher.researcher_id,
      researcher_name: researcher.name_display,
      profile_url: researcher.profile_url,
      internal_id: unambiguous ? internalIds[0] || "" : "",
      orcid: unambiguous ? orcids[0] || "" : "",
      repository_author_name: unambiguous ? candidates[0]?.name || "" : "",
      identity_match_method: candidates.length ? "exact_normalized_full_name_to_minerva_internal_author" : "unmatched",
      identity_match_confidence: unambiguous ? "high" : candidates.length ? "ambiguous" : "none",
      candidate_count: candidates.length,
      requires_review: !unambiguous,
      review_reason: !candidates.length
        ? "No Minerva internal-author identity found in the departmental seed collection"
        : !unambiguous
          ? `Name maps to ${internalIds.length} internal IDs and ${orcids.length} ORCIDs`
          : "",
    };
  });
}

function relationshipFor(publication, identity) {
  const exactOrcid = identity.orcid && publication.internal_authors.some((author) => author.orcid === identity.orcid);
  const exactInternalId = identity.internal_id && publication.internal_authors.some((author) => author.internal_id === identity.internal_id);
  const exactName = publication.internal_authors.some((author) => personKey(author.name) === personKey(identity.researcher_name))
    || publication.melbourne_authors.some((author) => personKey(author) === personKey(identity.researcher_name));
  if (!exactOrcid && !exactInternalId && !exactName) return null;
  return {
    researcher_id: identity.researcher_id,
    researcher_name: identity.researcher_name,
    publication_id: publication.publication_id,
    relationship_source: "University of Melbourne Minerva author metadata",
    researcher_match_method: exactOrcid ? "exact_orcid" : exactInternalId ? "exact_internal_author_id" : "exact_normalized_full_name",
    researcher_match_confidence: exactOrcid || exactInternalId ? "high" : "medium",
    requires_review: !(exactOrcid || exactInternalId),
    review_reason: exactOrcid || exactInternalId ? "" : "Name-only repository relationship; persistent identifier unavailable",
  };
}

async function searchResearcher(identity) {
  const query = identity.orcid || identity.repository_author_name || identity.researcher_name;
  const records = [];
  let page = 0;
  let totalPages = 1;
  do {
    const url = new URL(SEARCH_URL);
    url.searchParams.set("query", query);
    url.searchParams.set("page", String(page));
    url.searchParams.set("size", "100");
    url.searchParams.set("sort", "dc.date.issued,DESC");
    const response = await (await fetchWithRetry(url, { attempts: 2, delayMs: 750 })).json();
    const searchResult = response?._embedded?.searchResult;
    totalPages = searchResult?.page?.totalPages || 1;
    records.push(...(searchResult?._embedded?.objects || []).map(parseItem).filter(Boolean));
    page += 1;
    if (page < totalPages) await sleep(150);
  } while (page < totalPages);
  return records.filter((publication) => relationshipFor(publication, identity));
}

export async function collectUnimelbCurrentPublications({ moduleDirectory, prefix, field }) {
  const outputDirectory = path.join(moduleDirectory, "output");
  const [researchers, seedPayload] = await Promise.all([
    fs.readFile(path.join(outputDirectory, `${prefix}_researchers.json`), "utf8").then(JSON.parse),
    fs.readFile(path.join(outputDirectory, `${prefix}_publications.json`), "utf8").then(JSON.parse),
  ]);
  const seedPublications = seedPayload.records.map((record) => parseItem({
    type: "item",
    uuid: record.item_uuid,
    id: record.item_uuid,
    handle: record.handle,
    lastModified: record.last_modified,
    metadata: {
      "dc.title": [{ value: record.title }],
      "dc.date.issued": [{ value: record.issued_date }],
      "dc.identifier.doi": [{ value: record.doi }],
      "dc.identifier.issn": [{ value: record.issn }],
      "dc.identifier.eissn": [{ value: record.eissn }],
      "dc.contributor.author": (record.authors || []).map((value) => ({ value })),
      "melbourne.contributor.author": (record.melbourne_authors || []).map((value) => ({ value })),
      "melbourne.internal.authorids": (record.internal_author_ids || []).map((value) => ({ value })),
      "melbourne.source.title": [{ value: record.journal }],
      "melbourne.source.volume": [{ value: record.volume }],
      "melbourne.source.issue": [{ value: record.issue }],
      "melbourne.source.pages": [{ value: record.pages }],
      "dc.type": [{ value: record.item_type }],
      "dc.publisher": [{ value: record.publisher }],
      "melbourne.affiliation.department": (record.departments || []).map((value) => ({ value })),
      "melbourne.affiliation.faculty": (record.faculties || []).map((value) => ({ value })),
      "melbourne.openaccess.status": [{ value: record.open_access_status }],
      "melbourne.openaccess.url": [{ value: record.open_access_url }],
    },
  })).filter(Boolean).map((record) => ({ ...record, field_of_research: field }));

  const identities = buildResearcherIdentities(researchers, seedPublications);
  const acceptedIdentities = identities.filter((identity) => !identity.requires_review && (identity.orcid || identity.internal_id));
  let previousRecords = [];
  let previousFailedIds = new Set(acceptedIdentities.map((identity) => identity.researcher_id));
  try {
    const [previousPayload, previousQuality] = await Promise.all([
      fs.readFile(path.join(outputDirectory, `${prefix}_current_staff_publications.json`), "utf8").then(JSON.parse),
      fs.readFile(path.join(outputDirectory, `${prefix}_current_staff_publication_quality.json`), "utf8").then(JSON.parse),
    ]);
    previousRecords = previousPayload.records || [];
    previousFailedIds = new Set((previousQuality.search_failures || []).map((failure) => failure.researcher_id));
  } catch {}
  const searchFailures = [];
  let searchCacheHits = 0;
  let completed = 0;
  const globalResults = await parallelMap(acceptedIdentities, 3, async (identity) => {
    try {
      if (!previousFailedIds.has(identity.researcher_id)) {
        searchCacheHits += 1;
        return { identity, records: previousRecords.filter((publication) => relationshipFor(publication, identity)) };
      }
      const records = await searchResearcher(identity);
      completed += 1;
      console.log(`${prefix}: searched ${completed}/${acceptedIdentities.length} identified researchers`);
      return { identity, records };
    } catch (error) {
      searchFailures.push({ researcher_id: identity.researcher_id, query: identity.orcid || identity.repository_author_name, error: error.message });
      return { identity, records: [] };
    }
  });

  const publicationsById = new Map(seedPublications.map((publication) => [publication.publication_id, publication]));
  for (const result of globalResults) {
    for (const publication of result.records) publicationsById.set(publication.publication_id, { ...publication, field_of_research: field });
  }
  const publications = [...publicationsById.values()];
  const relationshipMap = new Map();
  for (const identity of identities) {
    for (const publication of publications) {
      const relationship = relationshipFor(publication, identity);
      if (!relationship) continue;
      const key = `${relationship.researcher_id}|${relationship.publication_id}`;
      const existing = relationshipMap.get(key);
      if (!existing || (existing.researcher_match_confidence !== "high" && relationship.researcher_match_confidence === "high")) {
        relationshipMap.set(key, relationship);
      }
    }
  }
  const relationships = [...relationshipMap.values()];
  const harvestedAt = new Date().toISOString();
  const quality = {
    generated_at: harvestedAt,
    scope: `Current official University of Melbourne ${field} roster linked to Minerva by persistent author identifiers where available`,
    staff_records: researchers.length,
    staff_with_high_confidence_minerva_identity: acceptedIdentities.length,
    staff_with_orcid: identities.filter((identity) => identity.orcid).length,
    staff_requiring_identity_review: identities.filter((identity) => identity.requires_review).length,
    departmental_seed_publications: seedPublications.length,
    combined_unique_publications: publications.length,
    researcher_publication_links: relationships.length,
    high_confidence_links: relationships.filter((row) => row.researcher_match_confidence === "high").length,
    name_only_links_requiring_review: relationships.filter((row) => row.requires_review).length,
    search_failures: searchFailures,
    researcher_search_cache_hits: searchCacheHits,
  };
  await Promise.all([
    fs.writeFile(path.join(outputDirectory, `${prefix}_researcher_identities.json`), `${JSON.stringify(identities, null, 2)}\n`, "utf8"),
    fs.writeFile(path.join(outputDirectory, `${prefix}_current_staff_publications.json`), `${JSON.stringify({ harvested_at: harvestedAt, records: publications }, null, 2)}\n`, "utf8"),
    fs.writeFile(path.join(outputDirectory, `${prefix}_current_staff_relationships.json`), `${JSON.stringify(relationships, null, 2)}\n`, "utf8"),
    fs.writeFile(path.join(outputDirectory, `${prefix}_current_staff_publication_quality.json`), `${JSON.stringify(quality, null, 2)}\n`, "utf8"),
    writeCsv(path.join(outputDirectory, `${prefix}_researcher_identities.csv`), Object.keys(identities[0] || {}), identities),
    writeCsv(path.join(outputDirectory, `${prefix}_current_staff_publications.csv`), [
      "publication_id", "source_record_id", "item_uuid", "handle", "item_url", "title", "publication_year", "issued_date",
      "article_url", "doi", "journal_name", "issn", "eissn", "all_issns", "volume", "issue", "pages", "authors",
      "melbourne_authors", "author_count", "publication_type", "publisher", "departments", "faculties", "open_access_status",
      "open_access_url", "last_modified", "field_of_research", "official_source", "source_url",
    ], publications),
    writeCsv(path.join(outputDirectory, `${prefix}_current_staff_relationships.csv`), Object.keys(relationships[0] || {}), relationships),
  ]);
  console.log(JSON.stringify(quality, null, 2));
  return quality;
}
