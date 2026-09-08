# Python v2 structure and migration boundary

## Active independent implementation

- `base_scrapers/uwa.py`: official UWA Pure staff, personal publication feeds,
  and publication detail pages.
- `base_scrapers/unimelb.py`: official FBE roster and Minerva collection/search
  API, with exact internal-author-ID or ORCID attribution.
- `pipeline/`: validation, review gating, versioned raw runs, team-schema
  exports, and atomic publication.
- `tools/review_workbench/`: local-only review UI and background refresh API.
- `tests/`: parser and pipeline regression tests.
- `data/README.md`: runtime-data contract. Generated contents of `data/` are
  git-ignored.
- `requirements.txt`: Python runtime dependencies.

No file in this list reads the old university output folders, `cleaned_data/`,
`crossref_data/`, `team_exports/`, or any previous CSV/JSON dataset.

## Current data flow

1. Discover the current Accounting and Finance staff from official sources.
2. Retrieve official repository publications.
3. Attribute publications only by official personal-feed membership (UWA) or
   exact Minerva internal author ID/ORCID (UniMelb).
4. Reject partial source runs and validate the raw schema.
5. Store uncertain staff/identity records in SQLite for manual review.
6. Publish approved records as an immutable revision, then atomically replace
   `data/current.json`.
7. Export separate UWA and UniMelb CSVs in the team's agreed 18-column order;
   keep merged duplicate source records in a separate audit JSON.

## Remaining integration work

The independent Python collector is complete, but OpenAlex, Crossref, ABDC,
Scimago and Clarivate enrichment still needs to be connected through the
team's shared modules. The strict snapshot therefore leaves metric fields
empty. Do not substitute values from an older output silently: compare the
record keys, retain provenance, and report match coverage before publishing an
enriched revision.

API credentials belong in a local `.env` file and must never be committed.
