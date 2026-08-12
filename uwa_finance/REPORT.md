# UWA Finance collection report

## Result

The proof of concept produced:

- 24 current Finance-linked Pure profiles, matching the 24 reported by Pure;
- 23 profiles mapped to a standard A-E academic level;
- 7 profiles flagged for a client inclusion or mapping decision;
- 1,242 Finance organisation outputs, matching the 1,242 reported by Pure;
- 758 publications matched to an ABDC 2025-v2 rating;
- 625 researcher-publication links;
- 697 historical organisation outputs with no current-profile match.

## Collection method

1. Read every current profile URL from the official Finance profiles RSS feed.
2. Visit each public profile page and extract the Finance appointment, Pure person classification, email, ORCID, Scopus profile and recent-output references.
3. Read every organisation-output URL from all pages of the official Finance publications RSS feed.
4. Visit each public publication page and extract title, year, DOI, journal, ISSN, authors, volume, issue, pages and access information.
5. Match journals against ABDC 2025-v2 using ISSN/eISSN first and normalized journal title as fallback.
6. Link current researchers to publications by exact Pure profile URL, with normalized names used only as a fallback.

## Key challenges

The main technical challenge is list-page access. Pure's normal full-list HTML routes can trigger Cloudflare verification, while its public RSS routes remain available and reproducible. The RSS feeds therefore provide complete discovery without attempting to bypass the site's security controls.

The main data challenge remains scope. Pure includes six teaching-only profiles in the Finance organisation, and the `Head of Department` title does not independently map to an Australian A-E level. The client should decide whether teaching-only profiles belong in the research ranking population and confirm the underlying academic level for that leadership title.

Forty-eight publications are listed by the official Finance RSS feed but do not expose a Finance organisation link on their detail pages. They are retained and visibly flagged because silently deleting official feed records would make the 1,242-record total irreconcilable.

The organisation output collection is historical, whereas the profile roster is current. This explains why 697 outputs do not link to a current Finance profile. These records should not be interpreted as failed scraping or automatically discarded; the client must choose between an organisation-history dataset and complete career records for current researchers.

## Recommended next actions

1. Confirm whether the seven flagged profiles should be included.
2. Confirm the underlying A-E level for `Head of Department`.
3. Review the 48 affiliation-visibility exceptions.
4. Confirm ABDC 2025-v2 versus the older 2022 list.
5. Decide whether the target is organisation outputs, complete current-researcher careers, or both; these are different datasets.
