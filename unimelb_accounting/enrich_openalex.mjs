import path from "node:path";
import { fileURLToPath } from "node:url";
import { enrichWithOpenAlex } from "../shared/pipeline/enrich_openalex.mjs";

await enrichWithOpenAlex({
  moduleDirectory: path.dirname(fileURLToPath(import.meta.url)),
  prefix: "unimelb_accounting",
  universityPattern: /melbourne/i,
});
