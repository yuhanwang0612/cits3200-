# UWA Accounting data collector

This module collects the current UWA Accounting profile roster and organisation research outputs from the UWA Profiles and Research Repository (Elsevier Pure). It enriches each record from its public detail page, applies the official ABDC 2025-v2 journal list, and creates researcher-publication links.

## Why RSS is used

The normal `/persons/` and `/publications/` list pages can trigger Cloudflare verification for automated requests. The same official Pure portal exposes public RSS feeds for both lists. The profile feed returned 20 records. The paginated publication feed returned 50 records on the first page and 14 on the second, exactly matching the 64 outputs reported by the Accounting organisation page. The page limit is calculated dynamically from Pure's reported count rather than capped at a fixed number of pages.

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

- `uwa_accounting_staff.csv/json`: raw Accounting profile roster.
- `uwa_accounting_publications.csv/json`: raw organisation publication records.
- `uwa_accounting_researchers.csv/json`: normalized researcher table and A-E mapping.
- `uwa_accounting_publications_ranked.csv/json`: publication table with ABDC matching.
- `uwa_accounting_researcher_publications.csv/json`: researcher-publication links.
- `uwa_accounting_researcher_summary.csv/json`: counts by current researcher.
- `uwa_accounting_data_quality.json`: reconciliation and quality warnings.

## Matching rules and limitations

- Current staff are defined by membership in the official Accounting profiles RSS feed.
- Standard UWA titles map as Lecturer=B, Senior Lecturer=C, Associate Professor=D and Professor=E. `Assistant Professor` is deliberately left unmapped pending client confirmation.
- Seven profiles require a client inclusion decision, mainly because Pure classifies them as teaching rather than research, or because their title mapping is non-standard.
- Researcher-publication links primarily use exact Pure profile URLs; name normalization is only a fallback.
- Three RSS-listed publications do not visibly show an Accounting organisation link on their detail page. They are retained because the Accounting RSS feed is the authoritative discovery source, but are flagged for review.
- The organisation output list may not equal every current researcher's complete career publication history.
- ABDC matching uses ISSN first and normalized journal title second. Unmatched records are retained.

## Official sources

- Accounting organisation: https://research-repository.uwa.edu.au/en/organisations/accounting/
- Accounting profiles RSS: https://research-repository.uwa.edu.au/en/organisations/accounting/persons/?format=rss
- Accounting publications RSS: https://research-repository.uwa.edu.au/en/organisations/accounting/publications/?format=rss
- ABDC 2025-v2: https://abdc.edu.au/wp-content/uploads/2026/05/ABDC-JQL-2025-v2-270526.xlsx
