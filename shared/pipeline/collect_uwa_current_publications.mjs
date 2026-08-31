import fs from "node:fs/promises";
import path from "node:path";
import {
  clean,
  extractIssns,
  fetchWithRetry,
  normalizeDoi,
  parallelMap,
  sleep,
  stableId,
  writeCsv,
} from "./common.mjs";

function normalizeUrl(value, base) {
  if (!value) return "";
  const url = new URL(value, base);
  url.hash = "";
  if (!url.pathname.endsWith("/")) url.pathname += "/";
  return url.toString();
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

function rssUrls(cheerio, xml, base) {
  const $ = cheerio.load(xml, { xmlMode: true });
  return $("item").map((_, item) => normalizeUrl($(item).find("link").first().text(), base)).get().filter(Boolean);
}

function parsePublication(cheerio, html, articleUrl, field) {
  const $ = cheerio.load(html);
  const bibtex = clean($(".rendering_researchoutput_bibtex").first().text());
  const publicationStatus = propertyValue($, "Publication status");
  const doiHref = $('a[href*="doi.org/"]').first().attr("href") || "";
  const doi = normalizeDoi(doiHref);
  const journal = propertyValue($, "Journal") || extractBibtexField(bibtex, "journal");
  const issns = extractIssns(
    extractBibtexField(bibtex, "issn"),
    propertyValue($, "ISSN"),
    propertyValue($, "Electronic ISSN"),
  );
  const authors = [];
  $(".rendering_researchoutput_associatespersonsclassifiedportal a[rel='Person']").each((_, element) => {
    authors.push({
      name: clean($(element).text().replace(/^,\s*/, "")),
      profile_url: normalizeUrl($(element).attr("href"), articleUrl),
    });
  });
  const title = clean($(".page-section-header h1").first().text() || $("h1").first().text());
  const year = publicationStatus.match(/\b(18|19|20)\d{2}\b/)?.[0]
    || extractBibtexField(bibtex, "year")
    || clean($(".rendering_researchoutput_apa").first().text()).match(/\((\d{4})\)/)?.[1]
    || "";
  const type = clean($(".rendering_researchoutput_publicationcontenttyperendererportalng .type").first().text())
    || clean($("p.type").first().text()).replace(/^Research output:\s*/i, "");
  const organisationUrls = [];
  $(".rendering_researchoutput_associatesorganisationsportal a[rel='Organisation']").each((_, element) => {
    organisationUrls.push(normalizeUrl($(element).attr("href"), articleUrl));
  });
  return {
    publication_id: stableId("uwa-pub", doi || articleUrl),
    source_record_id: articleUrl.split("/en/publications/")[1]?.replaceAll("/", "") || articleUrl,
    title,
    publication_year: year,
    publication_status: publicationStatus,
    article_url: articleUrl,
    doi,
    journal_name: journal,
    issn: issns[0] || "",
    eissn: issns[1] || "",
    all_issns: issns,
    volume: propertyValue($, "Volume") || extractBibtexField(bibtex, "volume"),
    issue: propertyValue($, "Issue number") || extractBibtexField(bibtex, "number"),
    pages: propertyValue($, "Pages (from-to)") || extractBibtexField(bibtex, "pages"),
    authors: clean($(".rendering_researchoutput_associatespersonsclassifiedportal").first().text()).replace(/^,\s*/, ""),
    linked_authors: authors,
    author_count: authors.length || "",
    publication_type: type,
    open_access: $(".open-access").length > 0,
    scopus_url: $('a[href*="scopus.com/pages/publications/"]').first().attr("href") || "",
    organisation_urls: [...new Set(organisationUrls)],
    field_of_research: field,
    official_source: "UWA Profiles and Research Repository (Pure)",
    source_url: articleUrl,
  };
}

export async function collectUwaCurrentPublications({ cheerio, moduleDirectory, prefix, field }) {
  const outputDirectory = path.join(moduleDirectory, "output");
  const staffPayload = JSON.parse(await fs.readFile(path.join(outputDirectory, `${prefix}_staff.json`), "utf8"));
  let normalizedResearchers = [];
  try {
    normalizedResearchers = JSON.parse(await fs.readFile(path.join(outputDirectory, `${prefix}_researchers.json`), "utf8"));
  } catch {
    normalizedResearchers = staffPayload.records.map((record) => ({ ...record }));
  }
  const idByProfile = new Map(normalizedResearchers.map((record) => [record.profile_url, record.researcher_id]));
  const researchers = staffPayload.records.map((record) => ({
    ...record,
    researcher_id: idByProfile.get(record.profile_url) || stableId("uwa-researcher", record.profile_url || record.name_display),
  }));

  const discoveryFailures = [];
  const discoveries = await parallelMap(researchers, 3, async (researcher) => {
    const base = `${researcher.profile_url.replace(/\/$/, "")}/publications/?format=rss`;
    const expected = researcher.reported_profile_output_count === null || researcher.reported_profile_output_count === undefined
      ? null
      : Number(researcher.reported_profile_output_count);
    const urls = new Set();
    const maxPages = expected ? Math.ceil(expected / 50) + 2 : 100;
    try {
      for (let page = 0; page < maxPages; page += 1) {
        const pageUrl = page === 0 ? base : `${base}&page=${page}`;
        const xml = await (await fetchWithRetry(pageUrl, { accept: "application/rss+xml,text/xml" })).text();
        const pageUrls = rssUrls(cheerio, xml, base);
        const before = urls.size;
        pageUrls.forEach((url) => urls.add(url));
        if (!pageUrls.length || urls.size === before || (expected !== null && expected > 0 && urls.size >= expected)) break;
        await sleep(150);
      }
    } catch (error) {
      discoveryFailures.push({ researcher_id: researcher.researcher_id, profile_url: researcher.profile_url, error: error.message });
    }
    return {
      researcher_id: researcher.researcher_id,
      researcher_name: researcher.name_display,
      profile_url: researcher.profile_url,
      expected_count: expected,
      discovered_count: urls.size,
      count_reconciles: expected === null ? null : expected === urls.size,
      publication_urls: [...urls],
    };
  });

  const publicationResearchers = new Map();
  for (const discovery of discoveries) {
    for (const publicationUrl of discovery.publication_urls) {
      if (!publicationResearchers.has(publicationUrl)) publicationResearchers.set(publicationUrl, []);
      publicationResearchers.get(publicationUrl).push({
        researcher_id: discovery.researcher_id,
        researcher_name: discovery.researcher_name,
        profile_url: discovery.profile_url,
      });
    }
  }
  const publicationUrls = [...publicationResearchers.keys()].sort();
  let cachedPublications = [];
  try {
    cachedPublications = JSON.parse(
      await fs.readFile(path.join(outputDirectory, `${prefix}_current_staff_publications.json`), "utf8"),
    ).records || [];
  } catch {}
  const cachedByUrl = new Map(cachedPublications.map((record) => [record.article_url, record]));
  let cacheHits = 0;
  const detailFailures = [];
  let completed = 0;
  const publications = (await parallelMap(publicationUrls, 4, async (url) => {
    try {
      if (process.env.FORCE_REFRESH_DETAILS !== "1" && cachedByUrl.has(url)) {
        cacheHits += 1;
        return cachedByUrl.get(url);
      }
      const html = await (await fetchWithRetry(url, { accept: "text/html,application/xhtml+xml" })).text();
      const publication = parsePublication(cheerio, html, url, field);
      completed += 1;
      if (completed % 100 === 0) console.log(`${prefix}: fetched ${completed}/${publicationUrls.length} publication pages`);
      await sleep(120);
      return publication;
    } catch (error) {
      detailFailures.push({ url, error: error.message });
      return null;
    }
  })).filter(Boolean);

  const publicationsByUrl = new Map(publications.map((record) => [record.article_url, record]));
  const relationships = [];
  for (const [url, sourceResearchers] of publicationResearchers) {
    const publication = publicationsByUrl.get(url);
    if (!publication) continue;
    for (const researcher of sourceResearchers) {
      relationships.push({
        researcher_id: researcher.researcher_id,
        researcher_name: researcher.researcher_name,
        publication_id: publication.publication_id,
        relationship_source: "official personal Pure publications feed",
        researcher_match_method: "profile_feed_membership",
        researcher_match_confidence: "high",
        requires_review: false,
        review_reason: "",
      });
    }
  }

  const harvestedAt = new Date().toISOString();
  const quality = {
    generated_at: harvestedAt,
    scope: `${field} publications listed on the official personal Pure profiles of the current official ${field} roster`,
    staff_records: researchers.length,
    reported_profile_publication_sum: discoveries.reduce((sum, row) => sum + (row.expected_count || 0), 0),
    discovered_profile_publication_sum: discoveries.reduce((sum, row) => sum + row.discovered_count, 0),
    unique_publication_urls: publicationUrls.length,
    extracted_unique_publications: publications.length,
    researcher_publication_links: relationships.length,
    profile_feeds_not_reconciled: discoveries.filter((row) => row.count_reconciles === false),
    profile_feeds_without_reported_count: discoveries.filter((row) => row.expected_count === null).map((row) => ({
      researcher_id: row.researcher_id,
      researcher_name: row.researcher_name,
      discovered_count: row.discovered_count,
      profile_url: row.profile_url,
    })),
    discovery_failures: discoveryFailures,
    detail_failures: detailFailures,
    detail_cache_hits: cacheHits,
  };
  const payload = { harvested_at: harvestedAt, records: publications };
  await Promise.all([
    fs.writeFile(path.join(outputDirectory, `${prefix}_current_staff_publications.json`), `${JSON.stringify(payload, null, 2)}\n`, "utf8"),
    fs.writeFile(path.join(outputDirectory, `${prefix}_current_staff_relationships.json`), `${JSON.stringify(relationships, null, 2)}\n`, "utf8"),
    fs.writeFile(path.join(outputDirectory, `${prefix}_current_staff_publication_quality.json`), `${JSON.stringify(quality, null, 2)}\n`, "utf8"),
    writeCsv(path.join(outputDirectory, `${prefix}_current_staff_publications.csv`), [
      "publication_id", "source_record_id", "title", "publication_year", "publication_status", "article_url", "doi",
      "journal_name", "issn", "eissn", "all_issns", "volume", "issue", "pages", "authors", "author_count",
      "publication_type", "open_access", "scopus_url", "field_of_research", "official_source", "source_url",
    ], publications.map((row) => ({ ...row, harvested_at: harvestedAt }))),
    writeCsv(path.join(outputDirectory, `${prefix}_current_staff_relationships.csv`), Object.keys(relationships[0] || {}), relationships),
  ]);
  console.log(JSON.stringify(quality, null, 2));
  return quality;
}
