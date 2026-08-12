# University of Melbourne Accounting data collector

This module reproduces the University of Melbourne Finance proof of concept for the Department of Accounting. It collects the current departmental staff directory, the Accounting collection in Minerva Access, applies the official ABDC 2025-v2 journal list, and creates provisional researcher-publication links.

## Scope and source choice

The Accounting research landing page is an overview page and is not a complete staff roster. The collector therefore uses the official **Department of Accounting** tag filter (`queries_tags_query=4895951`), which returned 50 staff records at harvest time. The subject-expertise filter for “accounting” returned only four records and was not used as the roster. Candidate, accepted and excluded counts are recorded in the raw staff JSON. When the page omits its department cell, the value is explicitly marked as inferred from the official department filter.

Publication records come from the Minerva collection **Accounting - Research Publications**. The collection endpoint reported 167 archived items, while the repository search API returned 168 unique records. The pipeline preserves all 168 records and reports the discrepancy instead of claiming complete reconciliation.

## Run

```bash
npm install
npm run collect:staff
npm run collect:publications
npm run build:data
npm run validate
# Or run the full data refresh:
npm run refresh
```

The scripts require Node.js 20 or later. Outputs are written to `output/`.

## Main outputs

- `unimelb_accounting_staff.csv/json`: raw current department roster.
- `unimelb_accounting_publications.csv/json`: raw Minerva collection records.
- `unimelb_accounting_researchers.csv/json`: normalized researcher table with academic-title and A-E mapping.
- `unimelb_accounting_publications_ranked.csv/json`: publication table with ABDC matching results.
- `unimelb_accounting_researcher_publications.csv/json`: provisional researcher-publication links.
- `unimelb_accounting_researcher_summary.csv/json`: publication and ABDC counts by current researcher.
- `unimelb_accounting_data_quality.json`: counts, reconciliation status and warnings.

## Important limitations

- The client must decide whether teaching-only, professional, honorary, visitor, postdoctoral and similar appointments belong in the target population.
- A-E levels are mapped only where an ordinary academic title can be identified; non-standard appointments remain unmapped rather than being guessed.
- Minerva collection membership is not proof of every current researcher's complete publication history.
- Researcher-publication links use normalized full names and require manual checking, especially where names are abbreviated or shared.
- Staff-page navigation retries up to three times with incremental backoff. Minerva API requests use the same retry policy.
- ABDC matching uses ISSN/eISSN first and normalized journal title second. Unmatched publications are retained.
- The ABDC version is `2025-v2-270526`; confirm with the client whether this or the older 2022 list is required.

## Official sources

- Accounting research page: https://fbe.unimelb.edu.au/accounting/our-research
- Department staff directory: https://fbe.unimelb.edu.au/about/academic-staff?queries_tags_query=4895951
- Minerva Accounting collection: https://minerva-access.unimelb.edu.au/collections/10c7b2a9-76da-5115-b275-96e65d024912
- ABDC 2025-v2 list: https://abdc.edu.au/wp-content/uploads/2026/05/ABDC-JQL-2025-v2-270526.xlsx
