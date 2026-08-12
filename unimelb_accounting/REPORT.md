# University of Melbourne Accounting collection report

## Result

The proof of concept produced a normalized Accounting dataset with:

- 50 current department-directory records;
- 38 staff records mapped to an A-E academic level;
- 25 records flagged for a client inclusion decision;
- 168 unique Minerva publication records extracted;
- 125 publications matched to an ABDC 2025-v2 rating;
- 118 provisional links between current researchers and publications;
- 66 publications not matched to a current directory record.

## Collection method

1. Request the official University of Melbourne staff directory using the Department of Accounting tag.
2. Parse each server-rendered staff card into name, role, disciplines, email and profile URL.
3. Query the Minerva repository search API within the Accounting collection and paginate until no further results remain.
4. Normalize publication metadata, including year, DOI, journal, ISSN/eISSN, authors and persistent article URL.
5. Match journals against the official ABDC 2025-v2 list using ISSN/eISSN first and normalized title only as a fallback.
6. Link publications to the current staff roster by normalized full name, retaining the source author string and a validation warning.

## Key challenges

The main challenge is not downloading HTML; it is defining and validating the target population. The departmental directory includes non-standard and teaching-focused roles, while Minerva represents repository deposits rather than a guaranteed complete career bibliography. Name-only researcher matching also cannot safely resolve initials, name variants or homonyms without ORCID or another stable identifier.

There is also an unresolved repository count discrepancy: the collection metadata reported 167 archived items, but the scoped search returned 168 unique records. All 168 are retained, and the discrepancy is surfaced in the quality report and workbook. The dataset should therefore be described as a reproducible proof of concept, not a completeness-certified master dataset.

## Recommended next actions

1. Ask the client to confirm which appointment categories should be included.
2. Confirm whether the project should use the ABDC 2025-v2 or 2022 list.
3. Manually review the 25 inclusion flags and the 118 provisional author links.
4. Investigate the 167/168 Minerva count discrepancy before defining a strict completeness acceptance test.
5. Add ORCID, Scopus ID or another stable identifier before automating cross-source author matching at scale.

