# University of Melbourne Finance implementation report

Harvest date: 2026-08-10

## Current result

The University of Melbourne Finance data pipeline now produces the fields confirmed in the 5 August client
meeting and exposes unresolved records rather than hiding them.

- 47 current staff-directory records collected.
- 37 staff records mapped automatically to academic levels B-E.
- 10 non-standard roles left unmapped and flagged for review; there were no explicit Level A roles.
- 254 Minerva Finance collection records collected with a title, year and article/repository link.
- 146 publication records matched to an ABDC 2025 rating.
- 136 researcher-publication links produced by normalized full-name matching.
- Per-researcher publication and ABDC-grade counts produced and reconciled by the validator.

The generated `unimelb_finance_data_quality.json` file is the authoritative run summary; counts can change when
the upstream sources change.

## What is complete

1. Collection of current Finance staff candidates and academic titles.
2. Explicit A-E mapping rules for standard titles.
3. Collection of publication title, year and stable article/repository URL.
4. ABDC merge using ISSN/eISSN first and normalized journal title as a fallback.
5. First-pass attribution of Minerva publications to current staff.
6. Per-researcher publication counts and A*/A/B/C counts.
7. Machine-readable JSON/CSV outputs, a review workbook and automated reconciliation checks.

## What still requires client or team validation

1. Confirm the population: research-active only, or also education-focused, postdoctoral, emeritus and other
   appointments.
2. Confirm whether ABDC 2025 or ABDC 2022 is the required ranking version.
3. Manually validate researcher-publication links and replace name matching with persistent IDs where possible.
4. Decide whether the Minerva collection is sufficient or whether OpenAlex is needed to extend career coverage.
5. Complete the separate University of Melbourne Accounting pipeline if the allocated university task includes
   both disciplines.

## Interpretation

This is a suitable Sprint-stage dataset and a valid implementation of the meeting's required structure. It is not
yet evidence that every current researcher and every career publication has been captured correctly. The data
quality report and review flags should remain visible when the data is migrated to SQL or exposed through the web
application.
