import crypto from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import {
  clean,
  normalizeIssn,
  parseCsv,
  sleep,
  writeCsv,
} from "./shared/pipeline/common.mjs";

const ROOT = path.dirname(fileURLToPath(import.meta.url));
const API_ROOT = "https://api.clarivate.com/apis/wos-journals/v1";
const OUTPUT_DIR = path.join(ROOT, "clarivate_data");
const CACHE_DIR = path.join(OUTPUT_DIR, "cache");
const INPUT_FILE = path.join(ROOT, "cleaned_data", "journals.csv");
const USER_AGENT = "CITS3200-Team20/2.0 (academic journal-metrics enrichment)";
const REQUEST_INTERVAL_MS = 250; // Four requests/second; the subscribed limit is five.

function parseArguments(argv) {
  const options = { force: false, limit: Infinity, year: null, dryRun: false };
  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index];
    if (argument === "--force") options.force = true;
    else if (argument === "--dry-run") options.dryRun = true;
    else if (argument === "--limit") options.limit = Number(argv[++index]);
    else if (argument === "--year") options.year = Number(argv[++index]);
    else if (argument === "--help") {
      console.log(`Usage: node enrich_clarivate.mjs [--limit N] [--year YYYY] [--force] [--dry-run]\n\nReads cleaned_data/journals.csv and writes Clarivate matches to clarivate_data/.\nThe API key is read from CLARIVATE_API_KEY or the repository-local .env file.`);
      process.exit(0);
    } else throw new Error(`Unknown argument: ${argument}`);
  }
  if (!Number.isFinite(options.limit) && options.limit !== Infinity) throw new Error("--limit must be a positive number");
  if (options.limit !== Infinity && options.limit < 1) throw new Error("--limit must be at least 1");
  if (options.year && (!Number.isInteger(options.year) || options.year < 1997 || options.year > new Date().getFullYear())) {
    throw new Error("--year must be a valid JCR year from 1997 to the current year");
  }
  return options;
}

async function loadLocalEnvironment(filename) {
  let text;
  try {
    text = await fs.readFile(filename, "utf8");
  } catch (error) {
    if (error.code === "ENOENT") return;
    throw error;
  }
  for (const rawLine of text.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line || line.startsWith("#")) continue;
    const match = line.match(/^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$/);
    if (!match || process.env[match[1]]) continue;
    let value = match[2].trim();
    if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) value = value.slice(1, -1);
    process.env[match[1]] = value;
  }
}

class ApiError extends Error {
  constructor(status, message, url) {
    super(`${status}: ${message}`);
    this.name = "ApiError";
    this.status = status;
    this.url = url;
  }
}

let nextRequestAt = 0;
async function respectRateLimit() {
  const now = Date.now();
  if (nextRequestAt > now) await sleep(nextRequestAt - now);
  nextRequestAt = Date.now() + REQUEST_INTERVAL_MS;
}

function cacheFilename(url) {
  return path.join(CACHE_DIR, `${crypto.createHash("sha256").update(url).digest("hex")}.json`);
}

async function requestJson(url, apiKey, { force = false, attempts = 4 } = {}) {
  const filename = cacheFilename(url);
  if (!force) {
    try {
      const cached = JSON.parse(await fs.readFile(filename, "utf8"));
      if (cached.status === 200) return cached.body;
    } catch (error) {
      if (error.code !== "ENOENT" && !(error instanceof SyntaxError)) throw error;
    }
  }

  let lastError;
  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    await respectRateLimit();
    try {
      const response = await fetch(url, {
        headers: { Accept: "application/json", "User-Agent": USER_AGENT, "X-ApiKey": apiKey },
        signal: AbortSignal.timeout(60_000),
      });
      const text = await response.text();
      let body;
      try {
        body = text ? JSON.parse(text) : {};
      } catch {
        body = { raw_response: text.slice(0, 500) };
      }
      if (!response.ok) {
        const message = clean(body.message || body.error || body.detail || response.statusText);
        throw new ApiError(response.status, message, url);
      }
      await fs.mkdir(CACHE_DIR, { recursive: true });
      await fs.writeFile(filename, `${JSON.stringify({ url, status: response.status, fetched_at: new Date().toISOString(), body }, null, 2)}\n`, "utf8");
      return body;
    } catch (error) {
      lastError = error;
      const retryable = !error.status || error.status === 429 || error.status >= 500;
      if (!retryable || attempt === attempts) break;
      const retryAfter = Number(error.retryAfter || 0);
      await sleep(retryAfter || 750 * 2 ** (attempt - 1));
    }
  }
  throw lastError;
}

function listHits(payload) {
  if (Array.isArray(payload?.hits)) return payload.hits;
  if (Array.isArray(payload?.data)) return payload.data;
  if (Array.isArray(payload?.journals)) return payload.journals;
  return [];
}

function journalIssns(journal) {
  return [...new Set([
    normalizeIssn(journal?.issn),
    normalizeIssn(journal?.eIssn || journal?.eissn),
    ...(Array.isArray(journal?.previousIssn) ? journal.previousIssn.map(normalizeIssn) : []),
  ].filter(Boolean))];
}

function availableReportYears(journal) {
  const reports = journal?.journalCitationReports;
  if (!Array.isArray(reports)) return [];
  const years = reports.flatMap((report) => {
    const values = [report?.year, report?.jcrYear, report?.reportYear];
    const selfMatch = clean(report?.self || report?.url).match(/\/year\/(\d{4})(?:\b|\/|$)/);
    if (selfMatch) values.push(selfMatch[1]);
    return values.map(Number).filter((year) => Number.isInteger(year) && year >= 1997 && year <= new Date().getFullYear());
  });
  return [...new Set(years)].sort((left, right) => right - left);
}

async function search(apiKey, query, options) {
  const url = new URL(`${API_ROOT}/journals`);
  url.searchParams.set("q", query);
  url.searchParams.set("limit", "50");
  url.searchParams.set("page", "1");
  return listHits(await requestJson(url.toString(), apiKey, options));
}

async function journalDetail(apiKey, id, options) {
  return requestJson(`${API_ROOT}/journals/${encodeURIComponent(id)}`, apiKey, options);
}

async function exactCandidatesForIssn(apiKey, issn, options) {
  const hits = await search(apiKey, issn, options);
  const candidates = [];
  for (const hit of hits) {
    if (!hit?.id) continue;
    const detail = await journalDetail(apiKey, hit.id, options);
    if (journalIssns(detail).includes(issn)) candidates.push({ id: hit.id, hit, detail });
  }
  return candidates;
}

async function determineReport(apiKey, candidate, requestedYear, options) {
  const knownYears = availableReportYears(candidate.detail);
  const currentYear = new Date().getFullYear();
  const years = requestedYear
    ? [requestedYear]
    : [...new Set([...knownYears, ...Array.from({ length: 6 }, (_, index) => currentYear - 1 - index)])];
  let lastNotFound;
  for (const year of years) {
    try {
      const report = await requestJson(`${API_ROOT}/journals/${encodeURIComponent(candidate.id)}/reports/year/${year}`, apiKey, options);
      return { year: Number(report.year || year), report };
    } catch (error) {
      if (error.status === 404) {
        lastNotFound = error;
        continue;
      }
      throw error;
    }
  }
  if (lastNotFound) return { year: requestedYear || knownYears[0] || "", report: null };
  return { year: "", report: null };
}

function metricValue(value) {
  if (value === null || value === undefined) return "";
  const text = typeof value === "object"
    ? clean(value.value ?? value.displayValue ?? value.formattedValue)
    : clean(value);
  return /^(?:<\s*)?\d+(?:\.\d+)?$/.test(text) ? text.replace(/\s+/g, "") : "";
}

function baseResult(journal, retrievedAt) {
  return {
    local_journal_id: journal.journal_id,
    local_journal_name: journal.journal_name,
    issn: normalizeIssn(journal.issn),
    eissn: normalizeIssn(journal.eissn),
    jcr_journal_id: "",
    jcr_journal_name: "",
    jcr_issn: "",
    jcr_eissn: "",
    impact_factor: "",
    five_year_impact_factor: "",
    jcr_year: "",
    clarivate_match_method: "none",
    clarivate_match_status: "not_collected",
    clarivate_retrieved_at: retrievedAt,
    review_reason: "",
  };
}

async function enrichJournal(journal, apiKey, options, retrievedAt) {
  const result = baseResult(journal, retrievedAt);
  const localIssns = [...new Set([result.issn, result.eissn].filter(Boolean))];
  if (!localIssns.length) {
    result.clarivate_match_status = "no_issn";
    result.review_reason = "No local ISSN/eISSN is available for an exact Clarivate match";
    return result;
  }

  try {
    const candidateMap = new Map();
    const matchedBy = new Map();
    for (const issn of localIssns) {
      const candidates = await exactCandidatesForIssn(apiKey, issn, options);
      for (const candidate of candidates) {
        candidateMap.set(candidate.id, candidate);
        if (!matchedBy.has(candidate.id)) matchedBy.set(candidate.id, []);
        matchedBy.get(candidate.id).push(issn);
      }
    }
    const candidates = [...candidateMap.values()];
    if (candidates.length !== 1) {
      result.clarivate_match_status = candidates.length ? "ambiguous" : "not_found";
      result.clarivate_match_method = "exact_issn_search";
      if (candidates.length) {
        result.review_reason = `Local ISSN/eISSN values matched multiple Clarivate journal IDs: ${candidates.map((entry) => entry.id).join("; ")}`;
      } else {
        const titleHits = journal.journal_name ? await search(apiKey, journal.journal_name, options) : [];
        const suggestions = titleHits.slice(0, 5).map((hit) => `${hit.id}: ${clean(hit.name)}`).join("; ");
        result.clarivate_match_status = suggestions ? "title_candidate_only" : "not_found";
        result.clarivate_match_method = suggestions ? "title_candidate_for_manual_review" : "exact_issn_search";
        result.review_reason = suggestions
          ? `No exact ISSN match. Title-search candidates were not auto-accepted: ${suggestions}`
          : "No exact Clarivate ISSN/eISSN match was found";
      }
      return result;
    }

    const candidate = candidates[0];
    const matchedIssns = [...new Set(matchedBy.get(candidate.id) || [])];
    const { year, report } = await determineReport(apiKey, candidate, options.year, options);
    const impact = report?.metrics?.impactMetrics || {};
    result.jcr_journal_id = candidate.id;
    result.jcr_journal_name = clean(candidate.detail.name || candidate.hit.name);
    result.jcr_issn = normalizeIssn(candidate.detail.issn);
    result.jcr_eissn = normalizeIssn(candidate.detail.eIssn || candidate.detail.eissn);
    result.impact_factor = metricValue(impact.jif);
    result.five_year_impact_factor = metricValue(impact.jif5Years);
    result.jcr_year = year;
    result.clarivate_match_method = matchedIssns.length === localIssns.length && localIssns.length > 1
      ? "exact_issn_and_eissn"
      : matchedIssns[0] === result.eissn && result.issn !== result.eissn ? "exact_eissn" : "exact_issn";
    result.clarivate_match_status = "matched";
    if (!report) result.review_reason = "Exact journal match found, but no JCR report was available for the requested/recent years";
    else if (!result.impact_factor) result.review_reason = "Exact journal match found, but this JCR report does not provide a Journal Impact Factor";
    return result;
  } catch (error) {
    result.clarivate_match_status = "error";
    result.clarivate_match_method = "exact_issn_search";
    result.review_reason = `Clarivate request failed${error.status ? ` (${error.status})` : ""}: ${clean(error.message)}`;
    return result;
  }
}

const options = parseArguments(process.argv.slice(2));
await loadLocalEnvironment(path.join(ROOT, ".env"));
if (!options.year && process.env.CLARIVATE_JCR_YEAR) {
  const year = Number(process.env.CLARIVATE_JCR_YEAR);
  if (!Number.isInteger(year) || year < 1997 || year > new Date().getFullYear()) throw new Error("CLARIVATE_JCR_YEAR must be a valid year from 1997 to the current year");
  options.year = year;
}
const journals = parseCsv(await fs.readFile(INPUT_FILE, "utf8"));
const selected = journals.slice(0, options.limit);
if (options.dryRun) {
  console.log(JSON.stringify({
    mode: "dry-run",
    input_file: INPUT_FILE,
    total_journals: journals.length,
    selected_journals: selected.length,
    journals_with_issn_or_eissn: selected.filter((journal) => normalizeIssn(journal.issn) || normalizeIssn(journal.eissn)).length,
    requested_jcr_year: options.year || "latest available",
    api_key_available: Boolean(process.env.CLARIVATE_API_KEY),
  }, null, 2));
  process.exit(0);
}

const apiKey = process.env.CLARIVATE_API_KEY;
if (!apiKey) throw new Error("CLARIVATE_API_KEY is not set. Put CLARIVATE_API_KEY=... in the repository-local .env file; .env is git-ignored.");

await fs.mkdir(OUTPUT_DIR, { recursive: true });
const retrievedAt = new Date().toISOString();
const results = [];
for (const [index, journal] of selected.entries()) {
  const result = await enrichJournal(journal, apiKey, options, retrievedAt);
  results.push(result);
  console.log(`[${index + 1}/${selected.length}] ${journal.journal_name || journal.issn || journal.eissn}: ${result.clarivate_match_status}${result.impact_factor ? ` (JIF ${result.impact_factor})` : ""}`);
}

const headers = Object.keys(baseResult({}, retrievedAt));
const reviewRows = results.filter((row) => row.clarivate_match_status !== "matched" || row.review_reason);
const unmatchedRows = results.filter((row) => row.clarivate_match_status !== "matched");
const quality = {
  generated_at: retrievedAt,
  source: "Clarivate Web of Science Journals API",
  input_journals: journals.length,
  processed_journals: results.length,
  partial_run: results.length < journals.length,
  requested_jcr_year: options.year || "latest available",
  matched_journals: results.filter((row) => row.clarivate_match_status === "matched").length,
  journals_with_impact_factor: results.filter((row) => row.impact_factor).length,
  journals_with_five_year_impact_factor: results.filter((row) => row.five_year_impact_factor).length,
  not_found_journals: results.filter((row) => row.clarivate_match_status === "not_found").length,
  title_candidate_only_journals: results.filter((row) => row.clarivate_match_status === "title_candidate_only").length,
  ambiguous_journals: results.filter((row) => row.clarivate_match_status === "ambiguous").length,
  no_issn_journals: results.filter((row) => row.clarivate_match_status === "no_issn").length,
  api_error_journals: results.filter((row) => row.clarivate_match_status === "error").length,
  automatic_match_policy: "Only a Clarivate journal detail record containing the same normalized local ISSN/eISSN is accepted. Title-only candidates require manual review.",
};

await Promise.all([
  fs.writeFile(path.join(OUTPUT_DIR, "journal_metrics.json"), `${JSON.stringify(results, null, 2)}\n`, "utf8"),
  writeCsv(path.join(OUTPUT_DIR, "journal_metrics.csv"), headers, results),
  writeCsv(path.join(OUTPUT_DIR, "clarivate_review_queue.csv"), headers, reviewRows),
  writeCsv(path.join(OUTPUT_DIR, "clarivate_unmatched.csv"), headers, unmatchedRows),
  fs.writeFile(path.join(OUTPUT_DIR, "clarivate_quality.json"), `${JSON.stringify(quality, null, 2)}\n`, "utf8"),
]);

console.log(JSON.stringify(quality, null, 2));
