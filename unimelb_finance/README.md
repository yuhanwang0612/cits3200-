# University of Melbourne Finance data extraction

This proof of concept collects two official University of Melbourne datasets:

1. The current Department of Finance staff directory.
2. The Minerva Access collection `Finance - Research Publications`.

## Run

```bash
/Users/plastic/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node unimelb_finance/scrape_finance_staff.mjs
/Users/plastic/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node unimelb_finance/scrape_finance_publications.mjs
```

Outputs are written to `unimelb_finance/output/` in JSON and CSV formats.

## Source roles

- The FBE staff directory is the source for current staff membership, job title, department, contact details, research interests and profile links.
- The public Minerva DSpace REST API is the source for the Finance collection's publication metadata.

## Important limitations

- The staff directory contains teaching-focused, professional, emeritus and postdoctoral roles. The staff output flags likely edge cases but does not exclude them. The client must define the inclusion rule.
- The Minerva collection contains 254 archived items at the time of this proof of concept. It is a department collection, not a guaranteed complete historical publication record for every current staff member.
- The collection can contain work by former staff and collaborators, while newly appointed staff may have earlier work outside the collection.
- Researcher-to-publication matching requires a separate reconciliation stage. ORCID/internal IDs should be preferred over name-only matching.
- The staff page rejects simple HTTP clients, so its scraper uses a real headless browser. Minerva exposes a public REST API and does not require browser automation.
