import { chromium } from "/Users/plastic/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/index.mjs";
import fs from "node:fs/promises";
import path from "node:path";

const SOURCE_PAGE = "https://fbe.unimelb.edu.au/finance/our-research";
const STAFF_PAGE = "https://fbe.unimelb.edu.au/about/academic-staff?queries_discipline_query=finance";
const CHROME_PATH = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const OUTPUT_DIR = path.resolve(process.argv[2] || "unimelb_finance/output");

const clean = (value) => String(value || "").replace(/\s+/g, " ").trim();
const csvCell = (value) => `"${String(value ?? "").replaceAll('"', '""')}"`;

await fs.mkdir(OUTPUT_DIR, { recursive: true });

const browser = await chromium.launch({
  executablePath: CHROME_PATH,
  headless: true,
});

try {
  const page = await browser.newPage({
    userAgent: "Mozilla/5.0 (compatible; CITS3200-Team20/1.0; academic research)",
  });

  await page.goto(STAFF_PAGE, { waitUntil: "domcontentloaded", timeout: 60_000 });
  await page.locator("table tr").first().waitFor({ state: "attached", timeout: 60_000 });

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

  const normalized = rows
    .map((row) => ({
      ...row,
      name_display: clean(row.name_display),
      role: clean(row.role),
      disciplines: clean(row.disciplines),
      interests: row.interests.map(clean).filter(Boolean),
      department: clean(row.department),
      email: clean(row.email),
      phone: clean(row.phone),
      profile_url: clean(row.profile_url),
    }))
    .filter((row) => row.department.toLowerCase() === "department of finance")
    .map((row) => {
      const reviewReason = [];
      if (/education focussed|education-focused|teaching specialist|\btutor\b/i.test(row.role)) {
        reviewReason.push("teaching/education-focused role");
      }
      if (/business manager|professional staff/i.test(row.role)) {
        reviewReason.push("possibly non-academic role");
      }
      if (/emeritus|honorary|visitor|postdoctoral/i.test(row.role)) {
        reviewReason.push("appointment category requires client decision");
      }
      if (!row.profile_url.includes("findanexpert.unimelb.edu.au")) {
        reviewReason.push("no Find an Expert profile linked");
      }
      return {
        ...row,
        inclusion_review_required: reviewReason.length > 0,
        inclusion_review_reason: reviewReason.join("; "),
      };
    });

  if (normalized.length === 0) {
    throw new Error("No Department of Finance records were extracted; the page structure may have changed.");
  }

  const harvestedAt = new Date().toISOString();
  const payload = {
    source_page: SOURCE_PAGE,
    staff_page: STAFF_PAGE,
    harvested_at: harvestedAt,
    record_count: normalized.length,
    records: normalized,
  };

  await fs.writeFile(
    path.join(OUTPUT_DIR, "unimelb_finance_staff.json"),
    `${JSON.stringify(payload, null, 2)}\n`,
    "utf8",
  );

  const columns = [
    "name_display",
    "role",
    "disciplines",
    "interests",
    "department",
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
  await fs.writeFile(path.join(OUTPUT_DIR, "unimelb_finance_staff.csv"), `${csvRows.join("\n")}\n`, "utf8");

  const flagged = normalized.filter((row) => row.inclusion_review_required).length;
  console.log(`Extracted ${normalized.length} Finance staff records (${flagged} flagged for inclusion review).`);
  console.log(`Output: ${OUTPUT_DIR}`);
} finally {
  await browser.close();
}
