# Cleaned Data Delivery

Generated from the current cleaned UWA and University of Melbourne Accounting and Finance dataset on 1 September 2026.

## Which file should I open first?

- `client_high_confidence_researcher_publications.csv`: recommended client-facing table. It contains only high-confidence researcher-publication relationships with no unresolved review flag.
- `quality_summary.csv`: one-row summary of dataset coverage and validation counts.

## File guide

### `client_high_confidence_researcher_publications.csv`

The safest flattened dataset for demonstrations, analysis, and initial database import. Each row links one researcher to one publication and includes staff, publication, journal, ABDC, OpenAlex citation, provenance, and matching fields. It contains 3,475 rows.

### `client_researcher_publications.csv`

The complete flattened working dataset. It contains 3,835 researcher-publication relationships, including records that require manual review. Use `requires_review` and `review_reason` before treating these rows as confirmed.

### `staff.csv`

The cleaned official staff roster for UWA and the University of Melbourne Accounting and Finance. It contains 143 researchers. Inclusion and identity uncertainty are retained in explicit review columns.

### `publications.csv`

The deduplicated publication entity table containing 3,434 unique publications. Join it to a researcher-publication table using `publication_id`.

### `journals.csv`

The normalized journal table containing 611 serial sources. It includes ISSN/eISSN and ABDC 2025-v2 matching where available, plus fields reserved for exact-ISSN Clarivate/JCR enrichment. The current `impact_factor` fields are blank because the licensed API enrichment has not yet been run.

### `review_queue.csv`

The 579 records that need human checking, including staff inclusion questions, uncertain identity or name matches, metadata conflicts, and duplicate publication versions. This file is not an error dump; it is the audit trail for unresolved decisions.

### `quality_summary.csv`

A compact one-row summary of row counts, identifier coverage, review totals, deduplication, and exclusions. Suitable for quickly showing the current data status.

### `quality_summary.json`

The detailed machine-readable quality report. It includes the overall totals and separate collection results for UWA Accounting, UWA Finance, UniMelb Accounting, and UniMelb Finance.

## Important interpretation notes

- Official university records take priority; OpenAlex supplements missing identifiers and citation data.
- OpenAlex-only rows are accepted only through high-confidence ORCID-based researcher matching, but they are not university-repository verified.
- Empty `impact_factor` values mean “not collected”, not zero.
- Some staff have no safely attributable publication records. They remain in `staff.csv` rather than being guessed through weak name-only matching.
- For a client demonstration, start with `client_high_confidence_researcher_publications.csv`; use the complete table and review queue only when discussing data quality and limitations.
