# University of Melbourne Finance data pipeline

This module implements the University of Melbourne Finance portion of the data requirements confirmed in the
5 August 2026 client meeting:

- researcher name and academic title;
- mapping of academic titles to Australian academic levels A-E;
- publication title, year and article link;
- journal quality ranking merged into publication records; and
- publication counts attributed to individual current researchers.

## Setup

```bash
npm install
npx playwright install chromium
```

If a system Chrome installation is preferred, set `CHROME_PATH` before running the staff collector.

## Scope and source choice

The staff collector uses the official **Department of Finance** tag filter (`queries_tags_query=4895953`), matching the Accounting module's department-based scope. Candidate, accepted and excluded counts are retained in the raw JSON. Missing department cells are explicitly marked as inferred from the official filter.

## Run the pipeline

From this module directory:

```bash
npm run collect:staff
npm run collect:publications
npm run build:data
npm run validate
# Or run the full data refresh:
npm run refresh
```

Optional arguments:

```bash
node build_minutes_dataset.mjs <input-dir> <ranking-csv> <output-dir>
```

The default ranking input is `data/abdc_2025.csv`, normalized from the official 2025 ABDC
Journal Quality List. Matching uses ISSN/eISSN first and normalized journal title only as a fallback.

## Main outputs

- `unimelb_finance_researchers.csv`: current staff with title, A-E level and inclusion-review flags.
- `unimelb_finance_publications_ranked.csv`: publication title, year, article link and ABDC result.
- `unimelb_finance_researcher_publications.csv`: matched researcher-publication records.
- `unimelb_finance_researcher_summary.csv`: counts by researcher and ABDC grade.
- `unimelb_finance_data_quality.json`: reconciliation totals, coverage and explicit warnings.

JSON equivalents are included for later SQL or web-application ingestion.

## Source roles

- The FBE staff directory defines the candidate list of current Finance staff.
- The Minerva DSpace REST API supplies publication metadata from the Finance collection.
- The official ABDC Journal Quality List supplies journal ratings.

## Important limitations

- The client must confirm whether teaching-only, professional, emeritus and postdoctoral appointments belong in
  the researcher population. Uncertain rows are retained and flagged instead of silently removed.
- A-E mapping is automatic only when an explicit academic title appears in the staff directory. Unmapped rows are
  flagged for manual review.
- The Minerva Finance collection is not guaranteed to contain the complete career output of every current staff
  member. It can contain former staff and collaborators and omit publications not deposited in the collection.
- Researcher-publication matching currently uses normalized full names from University of Melbourne metadata.
  The links must be validated; persistent identifiers should replace name matching when available.
- Staff-page navigation retries up to three times with incremental backoff. Minerva API requests use the same retry policy.
- The included rankings use ABDC 2025-v2. Confirm with the client whether the 2025 or 2022 list is required.

These limitations mean that the module satisfies the required data shape and provides a reproducible first-pass
dataset, but it must not be described as a complete or fully verified career-publication database yet.
