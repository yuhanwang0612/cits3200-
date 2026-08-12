import { chromium } from "playwright";
import fs from "node:fs/promises";
import path from "node:path";

const SOURCE_PAGE = "https://fbe.unimelb.edu.au/accounting/our-research";
// Use the official department filter, not the narrower "Accounting" expertise filter.
const STAFF_PAGE = "https://fbe.unimelb.edu.au/about/academic-staff?queries_tags_query=4895951";
const OUTPUT_DIR = path.resolve(process.argv[2] || "output");

const clean = (value) => String(value || "").replace(/\s+/g, " ").trim();
const csvCell = (value) => `"${String(value ?? "").replaceAll('"', '""')}"`;
const sleep = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

async function navigateWithRetry(page, url, maxAttempts = 3) {
  let lastError;
  for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
    try {
      await page.goto(url, { waitUntil: "domcontentloaded", timeout: 60_000 });
      await page.locator("table tr").first().waitFor({ state: "attached", timeout: 60_000 });
      return;
    } catch (error) {
      lastError = error;
      if (attempt < maxAttempts) await sleep(1_000 * attempt);
    }
  }
  throw new Error(`Failed to load staff page after ${maxAttempts} attempts: ${lastError?.message || lastError}`);
}

function matchesDepartment(value, expectedDepartment) {
  return clean(value).toLowerCase().includes(expectedDepartment.toLowerCase());
}

function academicLevel(name, role) {
  const text = `${name} ${role}`.toLowerCase();
  if (/\bassociate professor\b/.test(text)) {
    return { level: "D", title: "Associate Professor", source: "Associate Professor title" };
  }
  if (/\bprofessor\b/.test(text) && !/\bassociate professor\b/.test(text)) {
    return { level: "E", title: "Professor", source: "Professor title" };
  }
  if (/\bsenior lecturer\b/.test(text)) {
    return { level: "C", title: "Senior Lecturer", source: "Senior Lecturer title" };
  }
  if (/\bassociate lecturer\b/.test(text)) {
    return { level: "A", title: "Associate Lecturer", source: "Associate Lecturer title" };
  }
  if (/\blecturer\b/.test(text)) return { level: "B", title: "Lecturer", source: "Lecturer title" };
  return { level: "", title: "", source: "No A-E title found" };
}

function sourceTitle(name, role, mappedTitle) {
  if (mappedTitle) return mappedTitle;
  if (clean(role)) return clean(role);
  return clean(name).match(/^(Associate Professor|Professor|Dr\.?|Ms|Mr)\b/i)?.[0] || "Not stated";
}

await fs.mkdir(OUTPUT_DIR, { recursive: true });

const launchOptions = { headless: true };
if (process.env.CHROME_PATH) launchOptions.executablePath = process.env.CHROME_PATH;
const browser = await chromium.launch(launchOptions);

try {
  const page = await browser.newPage({
    userAgent: "Mozilla/5.0 (compatible; CITS3200-Team20/1.0; academic research)",
  });

  await navigateWithRetry(page, STAFF_PAGE);

  const rows = await page.locator("table tr").evaluateAll((tableRows) =>
    tableRows
      .map((tr) => {
        const cells = Array.from(tr.querySelectorAll("td"));
        if (!cells.length) return null;

        const heading = tr.querySelector("h5");
        const profile = heading?.querySelector("a[href]") || tr.querySelector('a[href*="findanexpert"]');
        const email = tr.querySelector('a[href^="mailto:"]');
        const phone = tr.querySelector('a[href^="tel:"]');
        const discipline = cells[1]?.querySelector("strong");
        const department = cells[2]?.querySelector("em");

        return {
          name_display: heading?.textContent || "",
          role: Array.from(cells[0]?.querySelectorAll("p") || []).map((p) => p.textContent || "").join("; "),
          disciplines: discipline?.textContent || "",
          interests: Array.from(cells[1]?.querySelectorAll("li") || []).map((li) => li.textContent || ""),
          department: department?.textContent || "",
          email: (email?.getAttribute("href") || "").replace(/^mailto:/, ""),
          phone: phone?.textContent || "",
          profile_url: profile?.href || "",
        };
      })
      .filter(Boolean),
  );

  const candidates = rows
    .map((row) => ({
      ...row,
      name_display: clean(row.name_display),
      role: clean(row.role),
      disciplines: clean(row.disciplines),
      interests: row.interests.map(clean).filter(Boolean),
      department: clean(row.department) || "Department of Accounting",
      department_source: clean(row.department) ? "staff page field" : "official department filter query",
      email: clean(row.email),
      phone: clean(row.phone),
      profile_url: clean(row.profile_url),
    }));
  const excludedRecords = candidates.filter((row) => !matchesDepartment(row.department, "Department of Accounting"));
  const normalized = candidates
    .filter((row) => matchesDepartment(row.department, "Department of Accounting"))
    .map((row) => {
      const mappedLevel = academicLevel(row.name_display, row.role);
      const reviewReason = [];
      if (/education focussed|education-focused|teaching specialist|teaching fellow|\btutor\b/i.test(row.role)) {
        reviewReason.push("teaching/education-focused role");
      }
      if (/business manager|professional staff/i.test(row.role)) {
        reviewReason.push("possibly non-academic role");
      }
      if (/emeritus|honorary|visitor|postdoctoral|enterprise (?:professor|fellow)|practitioner|fellow in/i.test(row.role)) {
        reviewReason.push("appointment category requires client decision");
      }
      if (!row.profile_url.includes("findanexpert.unimelb.edu.au")) {
        reviewReason.push("no Find an Expert profile linked");
      }
      if (!mappedLevel.level) {
        reviewReason.push("academic level could not be mapped automatically");
      }
      return {
        ...row,
        academic_title: sourceTitle(row.name_display, row.role, mappedLevel.title),
        academic_level: mappedLevel.level,
        academic_level_source: mappedLevel.source,
        inclusion_review_required: reviewReason.length > 0,
        inclusion_review_reason: reviewReason.join("; "),
      };
    });

  if (normalized.length === 0) {
    throw new Error("No Department of Accounting records were extracted; the page structure may have changed.");
  }

  const harvestedAt = new Date().toISOString();
  const payload = {
    source_page: SOURCE_PAGE,
    staff_page: STAFF_PAGE,
    harvested_at: harvestedAt,
    candidate_record_count: candidates.length,
    record_count: normalized.length,
    excluded_record_count: excludedRecords.length,
    excluded_records: excludedRecords,
    filter_rule: "Department field contains 'Department of Accounting' (case-insensitive)",
    records: normalized,
  };

  await fs.writeFile(
    path.join(OUTPUT_DIR, "unimelb_accounting_staff.json"),
    `${JSON.stringify(payload, null, 2)}\n`,
    "utf8",
  );

  const columns = [
    "name_display",
    "role",
    "academic_title",
    "academic_level",
    "academic_level_source",
    "disciplines",
    "interests",
    "department",
    "department_source",
    "email",
    "phone",
    "profile_url",
    "inclusion_review_required",
    "inclusion_review_reason",
  ];
  const csvRows = [
    columns.map(csvCell).join(","),
    ...normalized.map((row) =>
      columns.map((column) => csvCell(column === "interests" ? row.interests.join("; ") : row[column])).join(","),
    ),
  ];
  await fs.writeFile(path.join(OUTPUT_DIR, "unimelb_accounting_staff.csv"), `${csvRows.join("\n")}\n`, "utf8");

  const flagged = normalized.filter((row) => row.inclusion_review_required).length;
  console.log(`Extracted ${normalized.length} Accounting staff records (${flagged} flagged for inclusion review).`);
  console.log(`Department filter: ${candidates.length} candidates, ${excludedRecords.length} excluded.`);
  console.log(`Output: ${OUTPUT_DIR}`);
} finally {
  await browser.close();
}
