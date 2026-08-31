import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { normalizeDoi, normalizeIssn, normalizeText, parseCsv } from "./shared/pipeline/common.mjs";

const ROOT = path.dirname(fileURLToPath(import.meta.url));
const directory = path.resolve(process.argv[2] || path.join(ROOT, "cleaned_data"));
const read = (name) => fs.readFile(path.join(directory, name), "utf8").then(parseCsv);
const [staff, relationships, highConfidence, publications, journals, review] = await Promise.all([
  read("staff.csv"),
  read("client_researcher_publications.csv"),
  read("client_high_confidence_researcher_publications.csv"),
  read("publications.csv"),
  read("journals.csv"),
  read("review_queue.csv"),
]);
const failures = [];
const assert = (condition, message) => { if (!condition) failures.push(message); };
const staffIds = new Set(staff.map((row) => row.researcher_id));
const publicationIds = new Set(publications.map((row) => row.publication_id));
const titleYearKey = (row) => {
  const title = normalizeText(row.title);
  const year = String(row.year || row.publication_year || "");
  if (title && year) return `title-year:${title}|${year}`;
  return row.doi ? `doi:${normalizeDoi(row.doi)}` : `source:${row.publication_id || row.article_url}`;
};
assert(staff.length > 0, "staff.csv is empty");
assert(staffIds.size === staff.length, `staff.csv contains ${staff.length - staffIds.size} duplicate researcher IDs`);
assert(relationships.length > 0, "client_researcher_publications.csv is empty");
assert(relationships.every((row) => staffIds.has(row.researcher_id)), "At least one publication relationship has no staff row");
assert(relationships.every((row) => publicationIds.has(row.publication_id)), "At least one relationship publication_id has no publications.csv row");
const relationshipKeys = relationships.map((row) => `${row.researcher_id}|${titleYearKey(row)}`);
assert(new Set(relationshipKeys).size === relationshipKeys.length, "Duplicate researcher/publication relationship keys remain");
assert(relationships.every((row) => row.title), "At least one relationship has no title");
assert(relationships.every((row) => !row.doi || normalizeDoi(row.doi) === row.doi), "At least one DOI is not normalized");
assert(relationships.every((row) => !row.issn || normalizeIssn(row.issn) === row.issn), "At least one ISSN is not normalized");
assert(relationships.every((row) => !row.eissn || normalizeIssn(row.eissn) === row.eissn), "At least one eISSN is not normalized");
assert(relationships.every((row) => !row.cited_by_count || Number(row.cited_by_count) >= 0), "Negative or invalid citation count found");
const validMetric = (value) => !value || /^(?:<\s*)?\d+(?:\.\d+)?$/.test(String(value).trim());
assert(relationships.every((row) => validMetric(row.impact_factor)), "Invalid Journal Impact Factor value found");
assert(relationships.every((row) => validMetric(row.five_year_impact_factor)), "Invalid five-year Journal Impact Factor value found");
assert(relationships.filter((row) => row.impact_factor).every((row) =>
  row.impact_factor_status === "collected"
  && row.clarivate_match_status === "matched"
  && row.jcr_journal_id
  && /^\d{4}$/.test(row.jcr_year)), "A populated JIF lacks an exact Clarivate match, journal ID, or JCR year");
assert(relationships.filter((row) => row.clarivate_match_status === "matched").every((row) =>
  !String(row.clarivate_match_method).includes("title")), "A title-only Clarivate candidate was incorrectly accepted as a match");
assert(relationships.filter((row) => row.record_origin === "openalex_orcid_only").every((row) => row.orcid && row.openalex_author_id && row.researcher_match_confidence === "high"), "An OpenAlex-only row lacks exact-ORCID identity evidence");
assert(relationships.filter((row) => row.record_origin === "official_repository").every((row) => row.repository_verified === "true"), "An official-repository row is not marked repository_verified");
assert(highConfidence.every((row) => row.researcher_match_confidence === "high" && row.requires_review !== "true"), "High-confidence CSV contains a review-required or non-high-confidence row");
assert(highConfidence.every((row) => row.title && /^\d{4}$/.test(row.year)), "High-confidence CSV contains a missing title/year");
assert(publications.length > 0, "publications.csv is empty");
assert(new Set(publications.map(titleYearKey)).size === publications.length, "publications.csv still contains duplicate title-year/DOI keys");
const publicationDois = publications.map((row) => normalizeDoi(row.doi)).filter(Boolean);
assert(new Set(publicationDois).size === publicationDois.length, "publications.csv still contains duplicate DOI values");
assert(journals.every((row) => row.journal_name || row.issn || row.eissn), "Empty journal row found");
assert(journals.every((row) => validMetric(row.impact_factor) && validMetric(row.five_year_impact_factor)), "Invalid Clarivate metric found in journals.csv");
assert(journals.filter((row) => row.impact_factor).every((row) =>
  row.impact_factor_status === "collected"
  && row.clarivate_match_status === "matched"
  && row.jcr_journal_id
  && /^\d{4}$/.test(row.jcr_year)), "A journal JIF lacks exact-match evidence");
const clarivateMetricByKey = new Map();
for (const row of journals.filter((entry) => entry.clarivate_match_status === "matched")) {
  const key = `${row.jcr_journal_id}|${row.jcr_year}`;
  const value = `${row.impact_factor}|${row.five_year_impact_factor}`;
  if (clarivateMetricByKey.has(key)) assert(clarivateMetricByKey.get(key) === value, `Conflicting Clarivate metrics found for ${key}`);
  else clarivateMetricByKey.set(key, value);
}
assert(review.every((row) => row.review_reason), "Review queue contains a row without a reason");
const result = {
  checked_at: new Date().toISOString(),
  staff_rows: staff.length,
  relationship_rows: relationships.length,
  unique_publications: publications.length,
  journal_rows: journals.length,
  clarivate_matched_journals: journals.filter((row) => row.clarivate_match_status === "matched").length,
  journals_with_impact_factor: journals.filter((row) => row.impact_factor).length,
  review_rows: review.length,
  failures,
  passed: failures.length === 0,
};
console.log(JSON.stringify(result, null, 2));
if (failures.length) process.exitCode = 1;
