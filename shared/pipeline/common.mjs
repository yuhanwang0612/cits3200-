import crypto from "node:crypto";
import fs from "node:fs/promises";
import path from "node:path";

export const clean = (value) => String(value ?? "").replace(/\s+/g, " ").trim();

export function normalizeDoi(value) {
  return clean(value)
    .toLowerCase()
    .replace(/^doi:\s*/, "")
    .replace(/^https?:\/\/(?:dx\.)?doi\.org\//, "")
    .replace(/[?#].*$/, "")
    .replace(/[.,;:)]+$/, "");
}

export function normalizeIssn(value) {
  const compact = clean(value).toUpperCase().replace(/[^0-9X]/g, "");
  return compact.length === 8 ? `${compact.slice(0, 4)}-${compact.slice(4)}` : "";
}

export function extractIssns(...values) {
  const result = [];
  for (const value of values.flat(Infinity)) {
    for (const match of clean(value).matchAll(/\b\d{4}-?\d{3}[\dX]\b/gi)) {
      const issn = normalizeIssn(match[0]);
      if (issn && !result.includes(issn)) result.push(issn);
    }
  }
  return result;
}

export function normalizeText(value) {
  return clean(value)
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/&/g, " and ")
    .replace(/[^a-z0-9]+/g, " ")
    .trim();
}

const PERSON_TITLES = new Set([
  "associate", "doctor", "dr", "emeritus", "honorary", "miss", "mr", "mrs", "ms", "prof", "professor",
]);

export function personKey(value) {
  const tokens = normalizeText(value)
    .replace(/\([^)]*\)/g, " ")
    .split(" ")
    .filter(Boolean)
    .filter((token) => !PERSON_TITLES.has(token));
  return [...new Set(tokens)].sort().join(" ");
}

export function namesAgree(left, right) {
  const a = personKey(left).split(" ").filter(Boolean);
  const b = personKey(right).split(" ").filter(Boolean);
  if (!a.length || !b.length) return false;
  if (a.join(" ") === b.join(" ")) return true;
  const shared = b.filter((token) => a.includes(token));
  const aSurname = normalizeText(left).split(" ").filter(Boolean).at(-1);
  const bSurname = normalizeText(right).split(" ").filter(Boolean).at(-1);
  return shared.length >= 2 && aSurname === bSurname;
}

export function stableId(prefix, value) {
  return `${prefix}-${crypto.createHash("sha256").update(String(value)).digest("hex").slice(0, 16)}`;
}

export function publicationKey(record) {
  const doi = normalizeDoi(record.doi);
  if (doi) return `doi:${doi}`;
  const title = normalizeText(record.title || record.publication_title);
  const year = clean(record.publication_year || record.year);
  return title ? `title-year:${title}|${year}` : `source:${clean(record.publication_id || record.item_uuid || record.article_url)}`;
}

export function csvValue(value) {
  if (value === null || value === undefined) return "";
  const text = Array.isArray(value) ? value.join("; ") : typeof value === "object" ? JSON.stringify(value) : String(value);
  return /[",\r\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
}

export function toCsv(headers, rows) {
  return `${headers.join(",")}\n${rows.map((row) => headers.map((header) => csvValue(row[header])).join(",")).join("\n")}\n`;
}

export async function writeCsv(filename, headers, rows) {
  await fs.mkdir(path.dirname(filename), { recursive: true });
  await fs.writeFile(filename, toCsv(headers, rows), "utf8");
}

export async function fetchWithRetry(url, { accept = "application/json", attempts = 4, delayMs = 500 } = {}) {
  let lastError;
  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    try {
      const response = await fetch(url, {
        headers: {
          Accept: accept,
          "User-Agent": "CITS3200-Team20/2.0 (academic research; respectful automated collection)",
        },
        signal: AbortSignal.timeout(60_000),
      });
      if (!response.ok) {
        const error = new Error(`${response.status} ${response.statusText}: ${url}`);
        error.status = response.status;
        throw error;
      }
      return response;
    } catch (error) {
      lastError = error;
      if (attempt === attempts || (error.status && error.status < 500 && error.status !== 429)) break;
      await sleep(delayMs * 2 ** (attempt - 1));
    }
  }
  throw lastError;
}

export const sleep = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

export async function parallelMap(items, concurrency, worker) {
  const results = new Array(items.length);
  let cursor = 0;
  async function run() {
    while (cursor < items.length) {
      const index = cursor++;
      results[index] = await worker(items[index], index);
    }
  }
  await Promise.all(Array.from({ length: Math.min(concurrency, items.length) }, run));
  return results;
}

export function parseCsv(text) {
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
      } else if (character === '"') quoted = false;
      else cell += character;
    } else if (character === '"') quoted = true;
    else if (character === ",") {
      row.push(cell);
      cell = "";
    } else if (character === "\n") {
      row.push(cell.replace(/\r$/, ""));
      if (row.some((value) => value !== "")) rows.push(row);
      row = [];
      cell = "";
    } else cell += character;
  }
  if (cell || row.length) {
    row.push(cell);
    rows.push(row);
  }
  const [headers = [], ...data] = rows;
  return data.map((values) => Object.fromEntries(headers.map((header, index) => [header, values[index] || ""])));
}
