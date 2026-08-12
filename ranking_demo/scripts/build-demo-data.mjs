import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const projectRoot = path.resolve(here, "../..");
const outputPath = path.resolve(here, "../public/rankings.json");

const sources = [
  { key: "uwa_accounting", university: "University of Western Australia", universityShort: "UWA", discipline: "Accounting", countField: "publication_count_in_pure_collection" },
  { key: "uwa_finance", university: "University of Western Australia", universityShort: "UWA", discipline: "Finance", countField: "publication_count_in_pure_collection" },
  { key: "unimelb_accounting", university: "University of Melbourne", universityShort: "UniMelb", discipline: "Accounting", countField: "publication_count_in_minerva_collection" },
  { key: "unimelb_finance", university: "University of Melbourne", universityShort: "UniMelb", discipline: "Finance", countField: "publication_count_in_minerva_collection" },
];

async function readJson(filename) {
  return JSON.parse(await fs.readFile(filename, "utf8"));
}

const researchers = [];
const coverage = [];

for (const source of sources) {
  const outputDir = path.join(projectRoot, source.key, "output");
  const [summary, roster, quality] = await Promise.all([
    readJson(path.join(outputDir, `${source.key}_researcher_summary.json`)),
    readJson(path.join(outputDir, `${source.key}_researchers.json`)),
    readJson(path.join(outputDir, `${source.key}_data_quality.json`)),
  ]);
  const rosterById = new Map(roster.map((record) => [record.researcher_id, record]));

  for (const row of summary) {
    const profile = rosterById.get(row.researcher_id) || {};
    researchers.push({
      id: row.researcher_id,
      name: row.researcher_name,
      university: source.university,
      universityShort: source.universityShort,
      discipline: source.discipline,
      academicTitle: row.academic_title || profile.academic_title || profile.role || "Not stated",
      academicLevel: row.academic_level || profile.academic_level || "",
      inclusionReviewRequired: Boolean(row.inclusion_review_required),
      publicationCount: Number(row[source.countField] || 0),
      rankedPublicationCount: Number(row.abdc_ranked_publication_count || 0),
      aStarCount: Number(row.abdc_a_star_count || 0),
      aCount: Number(row.abdc_a_count || 0),
      bCount: Number(row.abdc_b_count || 0),
      cCount: Number(row.abdc_c_count || 0),
      unrankedCount: Number(row.unranked_publication_count || 0),
      profileUrl: profile.profile_url || "",
      sourceUrl: profile.source_url || quality.source_page || "",
    });
  }

  coverage.push({
    key: source.key,
    universityShort: source.universityShort,
    discipline: source.discipline,
    researcherCount: Number(quality.staff_records || summary.length),
    organisationOutputCount: Number(quality.publication_records || 0),
    rankedOutputCount: Number(quality.publications_with_abdc_rating || 0),
    researcherPublicationLinks: Number(quality.researcher_publication_links || 0),
    sourceCountReconciles: quality.pure_count_reconciles ?? quality.minerva_count_reconciles ?? null,
    warnings: quality.warnings || [],
  });
}

researchers.sort((left, right) => left.name.localeCompare(right.name));
await fs.mkdir(path.dirname(outputPath), { recursive: true });
await fs.writeFile(outputPath, `${JSON.stringify({
  generatedAt: new Date().toISOString(),
  scoringNote: "Illustrative score only: A*=8, A=4, B=2, C=1 by default. Users can change weights in the demo.",
  researchers,
  coverage,
}, null, 2)}\n`, "utf8");

console.log(`Wrote ${researchers.length} researchers from ${coverage.length} source collections to ${outputPath}`);
