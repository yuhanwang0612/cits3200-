# UWA Accounting collection report

## Result

The proof of concept produced:

- 20 current Accounting profiles, matching the 20 reported by Pure;
- 19 profiles mapped to a standard A-E academic level;
- 7 profiles flagged for a client inclusion or mapping decision;
- 64 Accounting organisation outputs, matching the 64 reported by Pure;
- 23 publications matched to an ABDC 2025-v2 rating;
- 71 researcher-publication links;
- 3 publications with no current-profile match.

## Collection method

1. Read every current profile URL from the official Accounting profiles RSS feed.
2. Visit each public profile page and extract the Accounting appointment, Pure person classification, email, ORCID, Scopus profile and recent-output references.
3. Read every organisation-output URL from all pages of the official Accounting publications RSS feed.
4. Visit each public publication page and extract title, year, DOI, journal, ISSN, authors, volume, issue, pages and access information.
5. Match journals against ABDC 2025-v2 using ISSN/eISSN first and normalized journal title as fallback.
6. Link current researchers to publications by exact Pure profile URL, with normalized names used only as a fallback.

## Key challenges

The main technical challenge is list-page access. Pure's normal full-list HTML routes can trigger Cloudflare verification, while its public RSS routes remain available and reproducible. The RSS feeds therefore provide complete discovery without attempting to bypass the site's security controls.

The main data challenge remains scope. Pure includes teaching-only profiles in the Accounting organisation, and one `Assistant Professor` title does not map cleanly to the Australian A-E titles used elsewhere in this prototype. The client should decide whether teaching-only profiles belong in the research ranking population and confirm the Assistant Professor mapping.

Three publications are listed by the official Accounting RSS feed but do not expose an Accounting organisation link on their detail pages. They are retained and visibly flagged because silently deleting official feed records would make the 64-record total irreconcilable.

## Recommended next actions

1. Confirm whether the seven flagged profiles should be included.
2. Confirm the A-E mapping for `Assistant Professor`.
3. Review the three affiliation-visibility exceptions.
4. Confirm ABDC 2025-v2 versus the older 2022 list.
5. Decide whether the target is organisation outputs, complete current-researcher careers, or both; these are different datasets.
