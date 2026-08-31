import * as cheerio from "cheerio";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { collectUwaCurrentPublications } from "../shared/pipeline/collect_uwa_current_publications.mjs";

await collectUwaCurrentPublications({
  cheerio,
  moduleDirectory: path.dirname(fileURLToPath(import.meta.url)),
  prefix: "uwa_accounting",
  field: "Accounting",
});
