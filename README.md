# CITS3200 Team 20 data pipeline

This branch contains the core UWA and University of Melbourne Accounting and
Finance data collectors, shared cleaning/enrichment logic, and the latest
validated client export. It intentionally contains no web demonstration.

## Latest cleaned data

Open `cleaned_data/client_high_confidence_researcher_publications.csv` for the
safest client-facing flattened table. See `cleaned_data/README.md` for a guide
to every delivery file and the limitations of the current dataset.

## Rebuild and validate the local export

These commands use existing source outputs and do not scrape websites:

```bash
pnpm run build:client-csv
pnpm run enrich:clarivate
pnpm run build:client-csv
pnpm run validate:client-csv
```

The Clarivate step is optional. Copy `.env.example` to `.env`, add the
Journals API key, and run `pnpm run enrich:clarivate`. It accepts only exact
ISSN/eISSN matches; title-search results are exported for manual review and do
not populate JIF values. API responses are cached locally and requests are
limited to four per second.

See `CLIENT_DATA_PIPELINE.md` for source refresh commands and the full data
flow.
