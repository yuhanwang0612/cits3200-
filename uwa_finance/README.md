# UWA Finance data collector

This module collects the current UWA Finance profile roster and organisation research outputs from the UWA Profiles and Research Repository (Elsevier Pure). It enriches each record from its public detail page, applies the official ABDC 2025-v2 journal list, and creates researcher-publication links.

## Why RSS is used

The normal `/persons/` and `/publications/` list pages can trigger Cloudflare verification for automated requests. The same official Pure portal exposes public RSS feeds for both lists. The profile feed returned 24 records. The paginated publication feed returned 1,242 distinct records, exactly matching the total reported by the Finance organisation page.

The collector uses RSS only for complete URL discovery. It then visits each individual public profile or publication page to collect structured metadata such as title, role, person type, email, ORCID, Scopus profile, DOI, journal and ISSN.

## Run

```bash
pnpm install
pnpm run collect:staff
pnpm run collect:publications
pnpm run build:data
pnpm run validate
# Or run the full data refresh:
pnpm run refresh
```

Outputs are written to `output/`.

## Main outputs

- `uwa_finance_staff.csv/json`: raw Finance profile roster.
- `uwa_finance_publications.csv/json`: raw organisation publication records.
- `uwa_finance_researchers.csv/json`: normalized researcher table and A-E mapping.
- `uwa_finance_publications_ranked.csv/json`: publication table with ABDC matching.
- `uwa_finance_researcher_publications.csv/json`: researcher-publication links.
- `uwa_finance_researcher_summary.csv/json`: counts by current researcher.
- `uwa_finance_data_quality.json`: reconciliation and quality warnings.

## Matching rules and limitations

- Current staff are defined by membership in the official Finance profiles RSS feed.
- Standard UWA titles map as Lecturer=B, Senior Lecturer=C, Associate Professor=D and Professor=E. `Assistant Professor` is deliberately left unmapped pending client confirmation.
- Seven profiles require a client inclusion decision: six are classified as teaching rather than research, and the `Head of Department` title does not independently establish an A-E level.
- Researcher-publication links primarily use exact Pure profile URLs; name normalization is only a fallback.
- Forty-eight RSS-listed publications do not visibly show a Finance organisation link on their detail page. They are retained because the Finance RSS feed is the authoritative discovery source, but are flagged for review.
- The organisation collection spans historical outputs. Consequently, 697 records do not link to one of the 24 current Finance profiles; this is expected unless the client requests a current-researcher-only career dataset.
- The organisation output list may not equal every current researcher's complete career publication history.
- ABDC matching uses ISSN first and normalized journal title second. Unmatched records are retained.

## Official sources

- Finance organisation: https://research-repository.uwa.edu.au/en/organisations/finance-2/
- Finance profiles RSS: https://research-repository.uwa.edu.au/en/organisations/finance-2/persons/?format=rss
- Finance publications RSS: https://research-repository.uwa.edu.au/en/organisations/finance-2/publications/?format=rss
- ABDC 2025-v2: https://abdc.edu.au/wp-content/uploads/2026/05/ABDC-JQL-2025-v2-270526.xlsx
