import path from "node:path";
import { fileURLToPath } from "node:url";
import { collectUnimelbCurrentPublications } from "../shared/pipeline/collect_unimelb_current_publications.mjs";

await collectUnimelbCurrentPublications({
  moduleDirectory: path.dirname(fileURLToPath(import.meta.url)),
  prefix: "unimelb_finance",
  field: "Finance",
});
