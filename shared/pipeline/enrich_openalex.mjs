import crypto from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";
import {
  clean,
  extractIssns,
  namesAgree,
  normalizeDoi,
  normalizeText,
  parallelMap,
  sleep,
  writeCsv,
} from "./common.mjs";

const API_BASE = process.env.OPENALEX_API_BASE || "https://api.openalex.org";
const API_KEY = process.env.OPENALEX_API_KEY || "";
const MAILTO = process.env.OPENALEX_MAILTO || "";

const compactId = (value) => clean(value).replace(/^https:\/\/openalex\.org\//, "");

function authorInstitutions(author) {
  const names = new Set();
  for (const institution of author.last_known_institutions || []) if (institution?.display_name) names.add(institution.display_name);
  for (const affiliation of author.affiliations || []) if (affiliation?.institution?.display_name) names.add(affiliation.institution.display_name);
  return [...names];
}

function profileNameAlias(profileUrl) {
  try {
    return decodeURIComponent(new URL(profileUrl).pathname.split("/").filter(Boolean).at(-1) || "")
      .replace(/-\d+$/, "")
      .replaceAll("-", " ");
  } catch {
    return "";
  }
}

function normalizeWork(work) {
  const source = work.primary_location?.source || work.best_oa_location?.source || {};
  const issns = extractIssns(source.issn || [], source.issn_l || "");
  const authors = (work.authorships || []).map((authorship) => ({
    openalex_author_id: compactId(authorship.author?.id),
    name: authorship.author?.display_name || "",
    orcid: clean(authorship.author?.orcid).replace(/^https:\/\/orcid\.org\//, ""),
    institutions: (authorship.institutions || []).map((institution) => institution.display_name).filter(Boolean),
  }));
  const doi = normalizeDoi(work.doi);
  return {
    openalex_work_id: compactId(work.id),
    title: work.title || work.display_name || "",
    publication_year: work.publication_year ?? "",
    publication_date: work.publication_date || "",
    doi,
    article_url: doi ? `https://doi.org/${doi}` : work.primary_location?.landing_page_url || work.id || "",
    journal_name: source.display_name || "",
    issn: issns[0] || "",
    eissn: issns[1] || "",
    all_issns: issns,
    authors,
    author_count: authors.length,
    publication_type: work.type || "",
    cited_by_count: work.cited_by_count ?? null,
    fwci: work.fwci ?? null,
    citation_percentile: work.citation_normalized_percentile?.value
      ?? work.citation_normalized_percentile?.is_in_top_10_percent
      ?? work.citation_normalized_percentile
      ?? null,
    is_retracted: Boolean(work.is_retracted),
    is_open_access: work.open_access?.is_oa ?? null,
    open_access_status: work.open_access?.oa_status || "",
    open_access_url: work.open_access?.oa_url || work.best_oa_location?.landing_page_url || "",
    topics: (work.topics || []).slice(0, 5).map((topic) => topic.display_name).filter(Boolean),
    updated_date: work.updated_date || "",
    source_updated_date: work.updated_date || "",
  };
}

class OpenAlexClient {
  constructor(cacheDirectory) {
    this.cacheDirectory = cacheDirectory;
    this.requests = 0;
    this.cacheHits = 0;
  }

  async request(endpoint, parameters) {
    const url = new URL(endpoint, API_BASE);
    for (const [key, value] of Object.entries(parameters || {})) if (value !== "" && value !== null && value !== undefined) url.searchParams.set(key, String(value));
    if (API_KEY) url.searchParams.set("api_key", API_KEY);
    if (MAILTO) url.searchParams.set("mailto", MAILTO);
    const cacheFile = path.join(this.cacheDirectory, `${crypto.createHash("sha256").update(url.toString()).digest("hex")}.json`);
    try {
      const cached = JSON.parse(await fs.readFile(cacheFile, "utf8"));
      this.cacheHits += 1;
      return cached;
    } catch (error) {
      if (error.code !== "ENOENT") throw error;
    }
    let lastError;
    for (let attempt = 1; attempt <= 5; attempt += 1) {
      try {
        this.requests += 1;
        const response = await fetch(url, {
          headers: { "User-Agent": `CITS3200-Team20/2.0${MAILTO ? ` (mailto:${MAILTO})` : ""}` },
          signal: AbortSignal.timeout(60_000),
        });
        if (!response.ok) {
          const error = new Error(`OpenAlex ${response.status}: ${(await response.text()).slice(0, 200)}`);
          error.status = response.status;
          throw error;
        }
        const data = await response.json();
        await fs.mkdir(this.cacheDirectory, { recursive: true });
        await fs.writeFile(cacheFile, `${JSON.stringify(data, null, 2)}\n`, "utf8");
        await sleep(150);
        return data;
      } catch (error) {
        lastError = error;
        if (attempt === 5 || (error.status && error.status < 500 && error.status !== 429)) break;
        await sleep(750 * 2 ** (attempt - 1));
      }
    }
    throw lastError;
  }

  async authorsByOrcid(orcid) {
    return (await this.request("/authors", { filter: `orcid:${orcid}`, "per-page": 25 })).results || [];
  }

  async worksByAuthor(authorId) {
    const works = [];
    let cursor = "*";
    do {
      const page = await this.request("/works", { filter: `author.id:${authorId}`, "per-page": 100, cursor });
      works.push(...(page.results || []));
      cursor = page.meta?.next_cursor || "";
    } while (cursor);
    return works;
  }
}

function officialOpenAlexMatch(publication, works) {
  const doi = normalizeDoi(publication.doi);
  let candidates = doi ? works.filter((work) => work.doi === doi) : [];
  let method = candidates.length ? "exact_doi" : "";
  if (!candidates.length) {
    const title = normalizeText(publication.title);
    candidates = works.filter((work) => title
      && normalizeText(work.title) === title
      && Math.abs(Number(work.publication_year || 0) - Number(publication.publication_year || 0)) <= 1);
    if (candidates.length) method = "exact_normalized_title_year";
  }
  return { candidates, method, work: candidates.length === 1 ? candidates[0] : null };
}

export async function enrichWithOpenAlex({ moduleDirectory, prefix, universityPattern }) {
  const outputDirectory = path.join(moduleDirectory, "output");
  const openalexDirectory = path.join(outputDirectory, "openalex_v2");
  const client = new OpenAlexClient(path.join(openalexDirectory, "cache"));
  const [researchers, publicationsPayload, relationships] = await Promise.all([
    fs.readFile(path.join(outputDirectory, `${prefix}_researchers.json`), "utf8").then(JSON.parse),
    fs.readFile(path.join(outputDirectory, `${prefix}_current_staff_publications.json`), "utf8").then(JSON.parse),
    fs.readFile(path.join(outputDirectory, `${prefix}_current_staff_relationships.json`), "utf8").then(JSON.parse),
  ]);
  let identities = [];
  try {
    identities = JSON.parse(await fs.readFile(path.join(outputDirectory, `${prefix}_researcher_identities.json`), "utf8"));
  } catch {
    identities = researchers.map((researcher) => ({ researcher_id: researcher.researcher_id, orcid: researcher.orcid || "" }));
  }
  const identityByResearcher = new Map(identities.map((identity) => [identity.researcher_id, identity]));
  const currentResearchers = researchers.map((researcher) => ({
    ...researcher,
    orcid: identityByResearcher.get(researcher.researcher_id)?.orcid || researcher.orcid || "",
  }));
  const retrievedAt = new Date().toISOString();
  const authorMatches = await parallelMap(currentResearchers, 3, async (researcher) => {
    if (!researcher.orcid) return {
      researcher_id: researcher.researcher_id,
      researcher_name: researcher.name_display,
      orcid: "",
      openalex_author_id: "",
      openalex_display_name: "",
      openalex_institutions: [],
      match_method: "no_orcid",
      match_confidence: "none",
      name_agreement: false,
      university_affiliation_observed: false,
      candidate_count: 0,
      requires_review: true,
      review_reason: "No ORCID; automatic name-only OpenAlex matching is disabled",
      retrieved_at: retrievedAt,
    };
    const candidates = await client.authorsByOrcid(researcher.orcid);
    const alias = profileNameAlias(researcher.profile_url);
    const evidence = candidates.map((candidate) => {
      const institutions = authorInstitutions(candidate);
      return {
        candidate,
        institutions,
        nameMatches: namesAgree(researcher.name_display, candidate.display_name) || (alias && namesAgree(alias, candidate.display_name)),
        affiliationMatches: institutions.some((name) => universityPattern.test(name)),
      };
    });
    const anchors = evidence.filter((entry) => entry.nameMatches || entry.affiliationMatches);
    const acceptedEvidence = anchors.length
      ? evidence.filter((entry) => anchors.some((anchor) => namesAgree(anchor.candidate.display_name, entry.candidate.display_name)) || entry.nameMatches || entry.affiliationMatches)
      : [];
    const candidate = acceptedEvidence.sort((a, b) => Number(b.affiliationMatches) - Number(a.affiliationMatches) || Number(b.candidate.works_count || 0) - Number(a.candidate.works_count || 0))[0]?.candidate || null;
    const institutions = [...new Set(acceptedEvidence.flatMap((entry) => entry.institutions))];
    const nameMatches = acceptedEvidence.some((entry) => entry.nameMatches);
    const affiliationMatches = acceptedEvidence.some((entry) => entry.affiliationMatches);
    return {
      researcher_id: researcher.researcher_id,
      researcher_name: researcher.name_display,
      orcid: researcher.orcid,
      openalex_author_id: candidate ? compactId(candidate.id) : "",
      openalex_author_ids: acceptedEvidence.map((entry) => compactId(entry.candidate.id)),
      openalex_display_name: candidate?.display_name || "",
      openalex_works_count: candidate?.works_count ?? null,
      openalex_cited_by_count: candidate?.cited_by_count ?? null,
      openalex_institutions: institutions,
      match_method: "exact_orcid",
      match_confidence: candidate && (nameMatches || affiliationMatches) ? "high" : candidate ? "low" : "none",
      name_agreement: nameMatches,
      university_affiliation_observed: affiliationMatches,
      candidate_count: candidates.length,
      requires_review: !candidate || (!nameMatches && !affiliationMatches),
      review_reason: !candidates.length
        ? "ORCID returned no OpenAlex author"
        : !candidate
          ? `ORCID returned ${candidates.length} authors but none matched the official name/profile alias or university affiliation`
          : !nameMatches && affiliationMatches
            ? "Accepted by exact official ORCID plus university affiliation; display-name alias differs"
            : candidates.length > acceptedEvidence.length
              ? `${candidates.length - acceptedEvidence.length} split candidates were excluded because they lacked corroborating evidence`
              : "",
      retrieved_at: retrievedAt,
    };
  });
  const acceptedAuthors = authorMatches.filter((match) => match.openalex_author_id && !match.requires_review);
  const worksByResearcherEntries = await parallelMap(acceptedAuthors, 2, async (match) => {
    const authorIds = match.openalex_author_ids?.length ? match.openalex_author_ids : [match.openalex_author_id];
    const works = (await parallelMap(authorIds, 2, (authorId) => client.worksByAuthor(authorId))).flat().map(normalizeWork);
    return {
      researcher_id: match.researcher_id,
      openalex_author_id: match.openalex_author_id,
      openalex_author_ids: authorIds,
      works: [...new Map(works.map((work) => [work.openalex_work_id, work])).values()],
    };
  });
  const worksByResearcher = new Map(worksByResearcherEntries.map((entry) => [entry.researcher_id, entry.works]));
  const publicationById = new Map(publicationsPayload.records.map((publication) => [publication.publication_id, publication]));
  const matchedWorkKeys = new Set();
  const enrichedOfficialRelationships = relationships.map((relationship) => {
    const publication = publicationById.get(relationship.publication_id);
    const works = worksByResearcher.get(relationship.researcher_id) || [];
    const result = publication ? officialOpenAlexMatch(publication, works) : { candidates: [], method: "", work: null };
    if (result.work) matchedWorkKeys.add(`${relationship.researcher_id}|${result.work.openalex_work_id}`);
    return {
      ...relationship,
      openalex_work_id: result.work?.openalex_work_id || "",
      openalex_match_method: result.method || "unmatched",
      openalex_match_confidence: result.work ? (result.method === "exact_doi" ? "high" : "medium") : "none",
      openalex_candidate_count: result.candidates.length,
      openalex_requires_review: result.candidates.length !== 1,
      openalex_review_reason: result.candidates.length > 1 ? `Matched ${result.candidates.length} OpenAlex works` : result.candidates.length ? "" : "No OpenAlex work match",
      cited_by_count: result.work?.cited_by_count ?? null,
      fwci: result.work?.fwci ?? null,
      citation_percentile: result.work?.citation_percentile ?? null,
      openalex_retrieved_at: retrievedAt,
    };
  });
  const openalexOnlyRelationships = [];
  for (const entry of worksByResearcherEntries) {
    const author = authorMatches.find((match) => match.researcher_id === entry.researcher_id);
    for (const work of entry.works) {
      if (matchedWorkKeys.has(`${entry.researcher_id}|${work.openalex_work_id}`)) continue;
      openalexOnlyRelationships.push({
        researcher_id: entry.researcher_id,
        researcher_name: author?.researcher_name || "",
        openalex_author_id: entry.openalex_author_id,
        ...work,
        record_origin: "openalex_orcid_only",
        identity_match_method: "official_orcid_to_openalex_author",
        identity_match_confidence: "high",
        repository_verified: false,
        requires_review: Boolean(work.is_retracted),
        review_reason: work.is_retracted ? "OpenAlex marks this work as retracted" : "",
        openalex_retrieved_at: retrievedAt,
      });
    }
  }
  const quality = {
    generated_at: retrievedAt,
    researchers_total: currentResearchers.length,
    researchers_with_orcid: currentResearchers.filter((researcher) => researcher.orcid).length,
    openalex_authors_high_confidence: acceptedAuthors.length,
    openalex_authors_requiring_review: authorMatches.filter((match) => match.requires_review).length,
    official_researcher_publication_links: relationships.length,
    official_links_matched_by_doi: enrichedOfficialRelationships.filter((row) => row.openalex_match_method === "exact_doi" && !row.openalex_requires_review).length,
    official_links_matched_by_title_year: enrichedOfficialRelationships.filter((row) => row.openalex_match_method === "exact_normalized_title_year" && !row.openalex_requires_review).length,
    official_links_without_unique_openalex_match: enrichedOfficialRelationships.filter((row) => row.openalex_requires_review).length,
    openalex_orcid_only_work_links: openalexOnlyRelationships.length,
    retracted_openalex_works: openalexOnlyRelationships.filter((row) => row.is_retracted).length,
    requests_made: client.requests,
    cache_hits: client.cacheHits,
  };
  await fs.mkdir(openalexDirectory, { recursive: true });
  await Promise.all([
    fs.writeFile(path.join(openalexDirectory, `${prefix}_openalex_authors.json`), `${JSON.stringify(authorMatches, null, 2)}\n`, "utf8"),
    fs.writeFile(path.join(openalexDirectory, `${prefix}_openalex_works.json`), `${JSON.stringify(worksByResearcherEntries, null, 2)}\n`, "utf8"),
    fs.writeFile(path.join(openalexDirectory, `${prefix}_official_relationships_enriched.json`), `${JSON.stringify(enrichedOfficialRelationships, null, 2)}\n`, "utf8"),
    fs.writeFile(path.join(openalexDirectory, `${prefix}_openalex_only_relationships.json`), `${JSON.stringify(openalexOnlyRelationships, null, 2)}\n`, "utf8"),
    fs.writeFile(path.join(openalexDirectory, `${prefix}_openalex_quality.json`), `${JSON.stringify(quality, null, 2)}\n`, "utf8"),
    writeCsv(path.join(openalexDirectory, `${prefix}_openalex_authors.csv`), Object.keys(authorMatches[0] || {}), authorMatches),
    writeCsv(path.join(openalexDirectory, `${prefix}_official_relationships_enriched.csv`), Object.keys(enrichedOfficialRelationships[0] || {}), enrichedOfficialRelationships),
    writeCsv(path.join(openalexDirectory, `${prefix}_openalex_only_relationships.csv`), Object.keys(openalexOnlyRelationships[0] || {}), openalexOnlyRelationships),
  ]);
  console.log(JSON.stringify(quality, null, 2));
  return quality;
}
