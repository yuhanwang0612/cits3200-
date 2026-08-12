import fs from "node:fs/promises";
import path from "node:path";
import * as cheerio from "cheerio";

const ORG_PAGE = "https://research-repository.uwa.edu.au/en/organisations/accounting/";
const ORG_RSS = "https://research-repository.uwa.edu.au/en/organisations/accounting/publications/?format=rss";
const OUTPUT_DIR = path.resolve(process.argv[2] || "output");
const USER_AGENT = "Mozilla/5.0 (compatible; CITS3200-Team20/1.0; academic research)";
const clean = (value) => String(value ?? "").replace(/\s+/g, " ").trim();
const csvCell = (value) => `"${String(value ?? "").replaceAll('"', '""')}"`;
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function normalizeUrl(url) {
  if (!url) return "";
  const absolute = new URL(url, ORG_PAGE);
  absolute.hash = "";
  if (!absolute.pathname.endsWith("/")) absolute.pathname += "/";
  return absolute.toString();
}

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

function propertyValue($, label) {
  let value = "";
  $("table.properties tr").each((_, row) => {
    if (clean($(row).find("th").text()).toLowerCase() === label.toLowerCase()) value = clean($(row).find("td").text());
  });
  return value;
}

function extractBibtexField(text, field) {
  return clean(text.match(new RegExp(`${field}\\s*=\\s*[\"{]([^\"}]+)`, "i"))?.[1] || "");
}

function parsePublication(html, articleUrl) {
  const $ = cheerio.load(html);
  const organisationUrls = [];
  $(".rendering_researchoutput_associatesorganisationsportal a[rel='Organisation']").each((_, element) => {
    organisationUrls.push(normalizeUrl($(element).attr("href")));
  });
  const accountingAffiliationObserved = organisationUrls.some((url) => url.includes("/en/organisations/accounting/"));

  const title = clean($(".page-section-header h1").first().text() || $("h1").first().text());
  const authors = clean($(".rendering_researchoutput_associatespersonsclassifiedportal").first().text())
    .replace(/^,\s*/, "");
  const uwaAuthors = [];
  $(".rendering_researchoutput_associatespersonsclassifiedportal a[rel='Person']").each((_, element) => {
    uwaAuthors.push({ name: clean($(element).text().replace(/^,\s*/, "")), profile_url: normalizeUrl($(element).attr("href")) });
  });
  const bibtex = clean($(".rendering_researchoutput_bibtex").first().text());
  const publicationStatus = propertyValue($, "Publication status");
  const year = publicationStatus.match(/\b(19|20)\d{2}\b/)?.[0]
    || extractBibtexField(bibtex, "year")
    || clean($(".rendering_researchoutput_apa").first().text()).match(/\((\d{4})\)/)?.[1]
    || "";
  const doiHref = $('a[href*="doi.org/"]').first().attr("href") || "";
  const doi = doiHref.replace(/^https?:\/\/(?:dx\.)?doi\.org\//i, "");
  const journal = propertyValue($, "Journal") || extractBibtexField(bibtex, "journal");
  const issn = extractBibtexField(bibtex, "issn");
  const type = clean($(".rendering_researchoutput_publicationcontenttyperendererportalng .type").first().text());
  const scopusLink = $('a[href*="scopus.com/pages/publications/"]').first().attr("href") || "";

  return {
    publication_id: articleUrl.split("/en/publications/")[1]?.replaceAll("/", "") || articleUrl,
    title,
    publication_year: year,
    publication_status: publicationStatus,
    article_url: articleUrl,
    doi,
    journal,
    issn,
    eissn: "",
    volume: propertyValue($, "Volume") || extractBibtexField(bibtex, "volume"),
    issue: propertyValue($, "Issue number") || extractBibtexField(bibtex, "number"),
    pages: propertyValue($, "Pages (from-to)") || extractBibtexField(bibtex, "pages"),
    authors,
    uwa_authors: uwaAuthors,
    item_type: type,
    open_access: $(".open-access").length > 0,
    scopus_url: scopusLink,
    organisation_urls: [...new Set(organisationUrls)],
    accounting_affiliation_observed: accountingAffiliationObserved,
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

function publicationUrlsFromRss(rssHtml) {
  const $ = cheerio.load(rssHtml, { xmlMode: true });
  return $("item").map((_, item) => normalizeUrl($(item).find("link").first().text())).get().filter(Boolean);
}

await fs.mkdir(OUTPUT_DIR, { recursive: true });
const organisationHtml = await fetchText(ORG_PAGE);
const $organisation = cheerio.load(organisationHtml);
const reportedMatch = clean($organisation("body").text()).match(/View all (\d+) research outputs/i);
const reportedPublicationCount = reportedMatch ? Number(reportedMatch[1]) : null;
const urls = new Set();
const maximumPages = reportedPublicationCount ? Math.ceil(reportedPublicationCount / 50) + 2 : 100;
for (let page = 0; page < maximumPages; page += 1) {
  const pageUrl = page === 0 ? ORG_RSS : `${ORG_RSS}&page=${page}`;
  const pageUrls = publicationUrlsFromRss(await fetchText(pageUrl));
  const before = urls.size;
  pageUrls.forEach((url) => urls.add(url));
  if (pageUrls.length === 0 || urls.size === before || (reportedPublicationCount && urls.size >= reportedPublicationCount)) break;
}

const failedRequests = [];
const publicationUrls = [...urls].sort();
const parsed = await mapWithConcurrency(publicationUrls, 3, async (url) => {
  try {
    return parsePublication(await fetchText(url), url);
  } catch (error) {
    failedRequests.push({ url, error: error.message });
    return null;
  }
});

const harvestedAt = new Date().toISOString();
const records = parsed.filter(Boolean).map((record) => ({ ...record, source_url: ORG_PAGE, harvested_at: harvestedAt }))
  .sort((a, b) => Number(b.publication_year || 0) - Number(a.publication_year || 0) || a.title.localeCompare(b.title));
const payload = {
  source_page: ORG_PAGE,
  harvested_at: harvestedAt,
  collection_method: "All publication URLs exposed by the official paginated Accounting publications RSS feed, enriched from individual Pure publication pages",
  reported_organisation_publication_count: reportedPublicationCount,
  discovered_recent_publication_url_count: publicationUrls.length,
  extracted_accounting_publication_count: records.length,
  count_reconciles: reportedPublicationCount === records.length,
  failed_publication_requests: failedRequests,
  publication_details_without_visible_accounting_affiliation: records
    .filter((record) => !record.accounting_affiliation_observed)
    .map((record) => record.article_url),
  records,
};
await fs.writeFile(path.join(OUTPUT_DIR, "uwa_accounting_publications.json"), `${JSON.stringify(payload, null, 2)}\n`, "utf8");

const columns = [
  "publication_id", "title", "publication_year", "publication_status", "article_url", "doi", "journal", "issn", "eissn",
  "volume", "issue", "pages", "authors", "uwa_authors", "item_type", "open_access", "scopus_url",
  "accounting_affiliation_observed", "source_url", "harvested_at",
];
const rows = [columns.map(csvCell).join(","), ...records.map((record) => columns.map((column) => {
  if (column === "uwa_authors") return csvCell(record.uwa_authors.map((author) => author.name).join("; "));
  return csvCell(record[column]);
}).join(","))];
await fs.writeFile(path.join(OUTPUT_DIR, "uwa_accounting_publications.csv"), `${rows.join("\n")}\n`, "utf8");

console.log(JSON.stringify({
  reported_organisation_publications: reportedPublicationCount,
  discovered_recent_urls: publicationUrls.length,
  extracted_accounting_publications: records.length,
  count_reconciles: payload.count_reconciles,
  failed_requests: failedRequests.length,
  details_without_visible_accounting_affiliation: records.filter((record) => !record.accounting_affiliation_observed).length,
}, null, 2));
