import fs from "node:fs/promises";
import path from "node:path";

const OUTPUT_DIR = path.resolve(process.argv[2] || "output");
const read = (filename) => fs.readFile(path.join(OUTPUT_DIR, filename), "utf8").then(JSON.parse);

const [researchers, publications, links, summaries, quality] = await Promise.all([
  read("unimelb_finance_researchers.json"),
  read("unimelb_finance_publications_ranked.json"),
  read("unimelb_finance_researcher_publications.json"),
  read("unimelb_finance_researcher_summary.json"),
  read("unimelb_finance_data_quality.json"),
]);

const errors = [];
const assert = (condition, message) => {
  if (!condition) errors.push(message);
};

assert(researchers.length > 0, "No researchers were produced.");
assert(publications.length > 0, "No publications were produced.");
assert(
  researchers.every((record) => record.name_display && record.academic_title),
  "A researcher is missing name/academic-title fields.",
);
assert(
  researchers.every((record) => record.academic_level || record.inclusion_review_required),
  "An unmapped academic level was not flagged for review.",
);
assert(
  publications.every((record) => record.title && record.publication_year && record.article_url),
  "A publication is missing title, year or article URL.",
);
assert(
  publications.every((record) => ["matched", "unmatched", "ambiguous"].includes(record.abdc_match_status)),
  "A publication has an invalid ABDC match status.",
);

const linkKeys = links.map((link) => `${link.researcher_id}|${link.publication_id}`);
assert(new Set(linkKeys).size === linkKeys.length, "Duplicate researcher-publication links were produced.");

const countByResearcher = new Map();
for (const link of links) countByResearcher.set(link.researcher_id, (countByResearcher.get(link.researcher_id) || 0) + 1);
for (const summary of summaries) {
  assert(
    Number(summary.publication_count_in_minerva_collection) === (countByResearcher.get(summary.researcher_id) || 0),
    `Publication count does not reconcile for ${summary.researcher_name}.`,
  );
}

assert(quality.staff_records === researchers.length, "Data-quality staff count does not reconcile.");
assert(
  quality.staff_candidate_records === quality.staff_records + quality.staff_excluded_by_department_filter,
  "Staff candidate, accepted and excluded counts do not reconcile.",
);
assert(quality.publication_records === publications.length, "Data-quality publication count does not reconcile.");
assert(quality.minerva_extracted_record_count === publications.length, "Minerva extracted count does not reconcile.");
assert(quality.researcher_publication_links === links.length, "Data-quality link count does not reconcile.");

if (errors.length) {
  console.error(errors.map((error) => `- ${error}`).join("\n"));
  process.exitCode = 1;
} else {
  console.log(`Validation passed: ${researchers.length} researchers, ${publications.length} publications, ${links.length} links.`);
}
