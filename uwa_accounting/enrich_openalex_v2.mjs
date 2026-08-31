import path from "node:path";
import { fileURLToPath } from "node:url";
import { enrichWithOpenAlex } from "../shared/pipeline/enrich_openalex.mjs";

await enrichWithOpenAlex({
  moduleDirectory: path.dirname(fileURLToPath(import.meta.url)),
  prefix: "uwa_accounting",
  universityPattern: /western australia/i,
});
