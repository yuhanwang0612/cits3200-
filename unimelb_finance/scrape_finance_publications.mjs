import fs from "node:fs/promises";
import path from "node:path";

const COLLECTION_ID = "c9dc98ca-3fac-599b-8dee-557a4fb3f5b9";
const API_ROOT = "https://minerva-access.unimelb.edu.au/server/api";
const COLLECTION_URL = `${API_ROOT}/core/collections/${COLLECTION_ID}`;
const SEARCH_URL = `${API_ROOT}/discover/search/objects`;
const OUTPUT_DIR = path.resolve(process.argv[2] || "output");
const PAGE_SIZE = 20;

const sleep = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));
const clean = (value) => String(value || "").replace(/\s+/g, " ").trim();
const csvCell = (value) => `"${String(value ?? "").replaceAll('"', '""')}"`;

async function fetchJson(url, maxAttempts = 3) {
  let lastError;
  for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
    try {
      const response = await fetch(url, {
        headers: {
          Accept: "application/json",
          "User-Agent": "CITS3200-Team20/1.0 (academic research; contact via UWA project client)",
        },
        signal: AbortSignal.timeout(60_000),
      });
      if (!response.ok) {
        throw new Error(`${response.status} ${response.statusText}: ${url}`);
      }
      return await response.json();
    } catch (error) {
      lastError = error;
      if (attempt < maxAttempts) await sleep(1_000 * attempt);
    }
  }
  throw lastError;
}

function values(metadata, key) {
  return (metadata?.[key] || []).map((entry) => clean(entry.value)).filter(Boolean);
}

function first(metadata, key) {
  return values(metadata, key)[0] || "";
}

function parseItem(wrapper) {
  const item = wrapper?._embedded?.indexableObject;
  if (!item || item.type !== "item") return null;
  const metadata = item.metadata || {};

  const issuedDate = first(metadata, "dc.date.issued");
  const doi = first(metadata, "dc.identifier.doi").replace(/^https?:\/\/(?:dx\.)?doi\.org\//i, "");
  const itemUrl = item.handle ? `https://hdl.handle.net/${item.handle}` : "";
  const openAccessUrl = first(metadata, "melbourne.openaccess.url");

  return {
    item_uuid: item.uuid || item.id || "",
    handle: item.handle || "",
    item_url: itemUrl,
    title: first(metadata, "dc.title") || clean(item.name),
    authors: values(metadata, "dc.contributor.author"),
    melbourne_authors: values(metadata, "melbourne.contributor.author"),
    internal_author_ids: values(metadata, "melbourne.internal.authorids"),
    issued_date: issuedDate,
    publication_year: issuedDate.match(/\b(18|19|20)\d{2}\b/)?.[0] || "",
    article_url: doi ? `https://doi.org/${doi}` : openAccessUrl || itemUrl,
    available_date: first(metadata, "dc.date.available"),
    item_type: first(metadata, "dc.type"),
    doi,
    issn: first(metadata, "dc.identifier.issn"),
    eissn: first(metadata, "dc.identifier.eissn"),
    journal: first(metadata, "melbourne.source.title"),
    volume: first(metadata, "melbourne.source.volume"),
    issue: first(metadata, "melbourne.source.issue"),
    pages: first(metadata, "melbourne.source.pages"),
    publisher: first(metadata, "dc.publisher"),
    departments: values(metadata, "melbourne.affiliation.department"),
    faculties: values(metadata, "melbourne.affiliation.faculty"),
    abstract: first(metadata, "dc.description.abstract"),
    citation_text: first(metadata, "dc.identifier.citation"),
    license: first(metadata, "dc.rights.license"),
    open_access_status: first(metadata, "melbourne.openaccess.status"),
    open_access_url: openAccessUrl,
    last_modified: item.lastModified || "",
  };
}

await fs.mkdir(OUTPUT_DIR, { recursive: true });

const collection = await fetchJson(COLLECTION_URL);
const records = [];
let page = 0;
let totalPages = 1;

do {
  const url = new URL(SEARCH_URL);
  url.searchParams.set("scope", COLLECTION_ID);
  url.searchParams.set("page", String(page));
  url.searchParams.set("size", String(PAGE_SIZE));
  url.searchParams.set("sort", "dc.date.available,DESC");

  const result = await fetchJson(url);
  const searchResult = result?._embedded?.searchResult;
  const objects = searchResult?._embedded?.objects || [];
  totalPages = searchResult?.page?.totalPages || 1;
  records.push(...objects.map(parseItem).filter(Boolean));
  console.log(`Fetched page ${page + 1}/${totalPages} (${records.length} records so far).`);

  page += 1;
  if (page < totalPages) await sleep(250);
} while (page < totalPages);

const uniqueRecords = [...new Map(records.map((record) => [record.item_uuid, record])).values()];
if (uniqueRecords.length !== collection.archivedItemsCount) {
  console.warn(
    `Warning: collection reports ${collection.archivedItemsCount} archived items, but ${uniqueRecords.length} were extracted.`,
  );
}

const harvestedAt = new Date().toISOString();
const payload = {
  collection_id: COLLECTION_ID,
  collection_name: collection.name,
  collection_handle: collection.handle,
  collection_url: `https://minerva-access.unimelb.edu.au/collections/${COLLECTION_ID}`,
  api_url: COLLECTION_URL,
  harvested_at: harvestedAt,
  reported_record_count: collection.archivedItemsCount,
  extracted_record_count: uniqueRecords.length,
  records: uniqueRecords,
};

await fs.writeFile(
  path.join(OUTPUT_DIR, "unimelb_finance_publications.json"),
  `${JSON.stringify(payload, null, 2)}\n`,
  "utf8",
);

const columns = [
  "item_uuid",
  "handle",
  "item_url",
  "title",
  "authors",
  "melbourne_authors",
  "internal_author_ids",
  "issued_date",
  "publication_year",
  "article_url",
  "available_date",
  "item_type",
  "doi",
  "issn",
  "eissn",
  "journal",
  "volume",
  "issue",
  "pages",
  "publisher",
  "departments",
  "faculties",
  "citation_text",
  "license",
  "open_access_status",
  "open_access_url",
  "last_modified",
];

const listColumns = new Set([
  "authors",
  "melbourne_authors",
  "internal_author_ids",
  "departments",
  "faculties",
]);
const csvRows = [
  columns.map(csvCell).join(","),
  ...uniqueRecords.map((record) =>
    columns
      .map((column) => csvCell(listColumns.has(column) ? record[column].join("; ") : record[column]))
      .join(","),
  ),
];
await fs.writeFile(path.join(OUTPUT_DIR, "unimelb_finance_publications.csv"), `${csvRows.join("\n")}\n`, "utf8");

console.log(
  `Extracted ${uniqueRecords.length}/${collection.archivedItemsCount} records from ${collection.name}.`,
);
console.log(`Output: ${OUTPUT_DIR}`);
