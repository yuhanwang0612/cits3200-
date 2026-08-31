# UWA and UniMelb client-data pipeline

The four original collectors remain available. The following additional stages produce the current-researcher and OpenAlex-integrated client export:

```text
UWA module:
  node scrape_current_staff_publications.mjs
  node enrich_openalex_v2.mjs

UniMelb module:
  node scrape_current_staff_publications.mjs
  node enrich_openalex.mjs

Repository root:
  node build_client_csv.mjs
  node enrich_clarivate.mjs
  node build_client_csv.mjs
  node validate_client_csv.mjs
```

Both root commands use `cleaned_data/` by default. You can pass a directory as
the first argument to build or validate a different location. Building the
client export only combines existing local source outputs; it does not scrape
or contact any website.

`enrich_clarivate.mjs` is the exception: it reads `cleaned_data/journals.csv`
and contacts the licensed Clarivate Web of Science Journals API. Put the key in
the git-ignored `.env` file as `CLARIVATE_API_KEY=...`. The script searches by
complete ISSN/eISSN, verifies the identifier again on the returned journal
detail record, downloads the newest available JCR report, and writes JIF and
five-year JIF to `clarivate_data/`. Run `build_client_csv.mjs` again to
propagate accepted metrics into the final journal, publication, and flattened
relationship tables. Title-only candidates are never joined automatically.

Set `FORCE_REFRESH_DETAILS=1` only when UWA publication detail pages must be downloaded again. Otherwise the collector refreshes each personal RSS feed and safely reuses cached detail records.

OpenAlex responses are cached under each module's `output/openalex_v2/cache/`. OpenAlex-only records are never accepted through name matching; a persistent ORCID from an official university source is required.

The validator checks foreign keys, duplicate researcher/publication relationships, duplicate publication DOI/title-year identities, normalized DOI/ISSN formats, citation counts, required title/year fields in the high-confidence export, and evidence on every OpenAlex-only row.

## Repository layout

- `uwa_accounting/`, `uwa_finance/`, `unimelb_accounting/`, and
  `unimelb_finance/`: source-specific collectors plus their latest local source
  and enrichment outputs.
- `shared/pipeline/`: shared retry, parsing, current-staff collection, and
  OpenAlex enrichment logic.
- `build_client_csv.mjs`: combines the four modules, deduplicates records, joins
  ABDC and available Clarivate data, and writes the client tables.
- `enrich_clarivate.mjs`: cached, rate-limited, exact-ISSN Clarivate enrichment.
- `clarivate_data/`: extracted journal metrics, coverage report, and review
  queue. Raw API-response cache is excluded from Git.
- `validate_client_csv.mjs`: validates the final client tables.
- `cleaned_data/`: the current client-facing CSV/JSON delivery.

Old web demonstrations and the superseded UWA OpenAlex v1 pipeline are not part
of the current data pipeline.
