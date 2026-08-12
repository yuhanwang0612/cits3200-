# Journal ranking input

`abdc_2025.csv` is a normalized extract of the official **2025 ABDC Journal Quality List**, version
`2025-v2-270526`, current to May 2026.

Official source:
https://abdc.edu.au/wp-content/uploads/2026/05/ABDC-JQL-2025-v2-270526.xlsx

The pipeline matches publications by print ISSN or online ISSN first. Exact normalized journal-title matching is
used only when neither ISSN matches. The ranking version is included in every derived publication record.

The client should confirm whether the 2025 or 2022 list is required before final acceptance testing. A replacement
CSV may be supplied as the second argument to `build_minutes_dataset.mjs` if another version is selected.
