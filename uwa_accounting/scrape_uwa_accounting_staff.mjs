import fs from "node:fs/promises";
import path from "node:path";
import * as cheerio from "cheerio";

const ORG_PAGE = "https://research-repository.uwa.edu.au/en/organisations/accounting/";
const ORG_RSS = "https://research-repository.uwa.edu.au/en/organisations/accounting/persons/?format=rss";
const OUTPUT_DIR = path.resolve(process.argv[2] || "output");
const USER_AGENT = "Mozilla/5.0 (compatible; CITS3200-Team20/1.0; academic research)";

const clean = (value) => String(value ?? "").replace(/\s+/g, " ").trim();
const csvCell = (value) => `"${String(value ?? "").replaceAll('"', '""')}"`;
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function fetchText(url, attempts = 3) {
  let lastError;
  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    try {
      const response = await fetch(url, {
        headers: { "user-agent": USER_AGENT, accept: "text/html,application/xhtml+xml" },
        signal: AbortSignal.timeout(45_000),
      });
      if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
      const text = await response.text();
      if (/security verification|安全验证|cf-chl-/i.test(text)) throw new Error("Cloudflare verification page returned");
      return text;
    } catch (error) {
      lastError = error;
      if (attempt < attempts) await sleep(800 * attempt);
    }
  }
  throw new Error(`Failed to fetch ${url}: ${lastError?.message || lastError}`);
}

function academicLevel(title) {
  const value = clean(title).toLowerCase();
  if (/\bassistant professor\b/.test(value)) return { level: "", source: "Assistant Professor mapping requires confirmation" };
  if (/\bassociate professor\b/.test(value)) return { level: "D", source: "Associate Professor title" };
  if (/\bprofessor\b/.test(value) && !/\bassociate professor\b/.test(value)) return { level: "E", source: "Professor title" };
  if (/\bsenior lecturer\b/.test(value)) return { level: "C", source: "Senior Lecturer title" };
  if (/\bassociate lecturer\b/.test(value)) return { level: "A", source: "Associate Lecturer title" };
  if (/\blecturer\b/.test(value)) return { level: "B", source: "Lecturer title" };
  return { level: "", source: "No standard UWA A-E title found" };
}

function decodeEmail(element) {
  const encoded = element.attr("data-md5");
  if (encoded) {
    try {
      return Buffer.from(encoded, "base64").toString("utf8").replace(/^mailto:/, "");
    } catch {}
  }
  return clean(element.text()).replace(" encryptedA(); ", "@").replace(" encryptedDot(); ", ".");
}

function normalizeUrl(url) {
  if (!url) return "";
  const absolute = new URL(url, ORG_PAGE);
  absolute.hash = "";
  if (!absolute.pathname.endsWith("/")) absolute.pathname += "/";
  return absolute.toString();
}

function extractReportedCount(html, label) {
  const match = clean(cheerio.load(html)("body").text()).match(new RegExp(`View all (\\d+) ${label}`, "i"));
  return match ? Number(match[1]) : null;
}

function extractRssProfiles(rssHtml) {
  const $ = cheerio.load(rssHtml, { xmlMode: true });
  return $("item").map((_, item) => {
    const url = normalizeUrl($(item).find("link").first().text());
    const descriptionHtml = $(item).find("description").first().text();
    const fragment = cheerio.load(descriptionHtml);
    return {
      url,
      person_type: clean(fragment("p.type").first().text()).replace(/^Person:\s*/i, "").replaceAll("&amp;", "&"),
    };
  }).get().filter((item) => item.url);
}

function parseProfile(html, profileUrl, personType) {
  const $ = cheerio.load(html);
  const name = clean($(".page-section-header h1").first().text() || $("h1").first().text());
  const affiliations = [];
  $(".rendering_personorganisationlistrendererportal li").each((_, element) => {
    const row = $(element);
    affiliations.push({
      title: clean(row.find(".job-title").first().text()),
      organisation: clean(row.find('a[rel="Organisation"] span').first().text()),
      organisation_url: normalizeUrl(row.find('a[rel="Organisation"]').first().attr("href")),
    });
  });
  const accounting = affiliations.find((item) => item.organisation_url.includes("/en/organisations/accounting/"));
  if (!accounting) return null;

  const emailElement = $(".rendering_personorganisationcontactrendererportal a.email").first();
  const orcidUrl = $('a[href*="orcid.org/"]').first().attr("href") || "";
  const scopusUrl = $('a[href*="scopus.com/authid/detail.uri"]').first().attr("href") || "";
  const recentPublicationUrls = [];
  $(".rendering_researchoutput_portal-short h3.title a[href*='/en/publications/']").each((_, element) => {
    recentPublicationUrls.push(normalizeUrl($(element).attr("href")));
  });
  const outputCountMatch = clean($("body").text()).match(/View all (\d+) research outputs/i);
  const mapped = academicLevel(accounting.title);
  const reviewReasons = [];
  if (!mapped.level) reviewReasons.push("academic level requires client confirmation");
  if (/assistant professor/i.test(accounting.title)) reviewReasons.push("UWA Assistant Professor mapping requires confirmation");
  if (/teaching|honorary|emeritus|adjunct|visitor|contractor|fellow/i.test(accounting.title)) {
    reviewReasons.push("appointment category requires client decision");
  }
  if (personType && !/research/i.test(personType)) reviewReasons.push("Pure profile is not classified as research");

  return {
    name_display: name,
    academic_title: accounting.title || "Not stated",
    academic_level: mapped.level,
    academic_level_source: mapped.source,
    department: "Accounting",
    person_type: personType,
    affiliations,
    email: decodeEmail(emailElement),
    orcid: orcidUrl.replace(/^https?:\/\/(?:www\.)?orcid\.org\//, ""),
    scopus_profile_url: scopusUrl,
    profile_url: profileUrl,
    reported_profile_output_count: outputCountMatch ? Number(outputCountMatch[1]) : null,
    recent_publication_urls: [...new Set(recentPublicationUrls)],
    inclusion_review_required: reviewReasons.length > 0,
    inclusion_review_reason: reviewReasons.join("; "),
  };
}

async function mapWithConcurrency(items, concurrency, worker) {
  const results = new Array(items.length);
  let cursor = 0;
  await Promise.all(Array.from({ length: concurrency }, async () => {
    while (cursor < items.length) {
      const index = cursor++;
      results[index] = await worker(items[index], index);
      await sleep(250);
    }
  }));
  return results;
}

await fs.mkdir(OUTPUT_DIR, { recursive: true });
const [rssHtml, organisationHtml] = await Promise.all([fetchText(ORG_RSS), fetchText(ORG_PAGE)]);
const rssProfiles = extractRssProfiles(rssHtml);
const candidateUrls = rssProfiles.map((item) => item.url);
const personTypeByUrl = new Map(rssProfiles.map((item) => [item.url, item.person_type]));
const failures = [];
const profiles = await mapWithConcurrency(candidateUrls, 3, async (url) => {
  try {
    return parseProfile(await fetchText(url), url, personTypeByUrl.get(url) || "");
  } catch (error) {
    failures.push({ url, error: error.message });
    return null;
  }
});

const records = profiles.filter(Boolean).sort((a, b) => a.name_display.localeCompare(b.name_display));
const harvestedAt = new Date().toISOString();
const reportedProfileCount = extractReportedCount(organisationHtml, "profiles");
const payload = {
  source_page: ORG_PAGE,
  candidate_source_page: ORG_RSS,
  harvested_at: harvestedAt,
  reported_profile_count: reportedProfileCount,
  candidate_profile_count: candidateUrls.length,
  extracted_accounting_profile_count: records.length,
  count_reconciles: reportedProfileCount === records.length,
  failed_profile_requests: failures,
  records,
};

await fs.writeFile(path.join(OUTPUT_DIR, "uwa_accounting_staff.json"), `${JSON.stringify(payload, null, 2)}\n`, "utf8");
const columns = [
  "name_display", "academic_title", "academic_level", "academic_level_source", "department", "person_type", "email", "orcid",
  "scopus_profile_url", "profile_url", "reported_profile_output_count", "recent_publication_urls",
  "inclusion_review_required", "inclusion_review_reason",
];
const rows = [columns.map(csvCell).join(","), ...records.map((record) => columns.map((column) => {
  if (column === "recent_publication_urls") return csvCell(record.recent_publication_urls.join("; "));
  return csvCell(record[column]);
}).join(","))];
await fs.writeFile(path.join(OUTPUT_DIR, "uwa_accounting_staff.csv"), `${rows.join("\n")}\n`, "utf8");

console.log(JSON.stringify({
  reported_profiles: reportedProfileCount,
  candidate_profiles: candidateUrls.length,
  extracted_accounting_profiles: records.length,
  count_reconciles: payload.count_reconciles,
  failed_requests: failures.length,
}, null, 2));
