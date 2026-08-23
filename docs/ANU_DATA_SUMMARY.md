# ANU — Accounting & Finance data summary

For the 26 Aug meeting. Covers the Research School of Accounting (RSA) and
the Finance area of the Research School of Finance, Actuarial Studies &
Statistics (RSFAS) — the two ANU schools within scope. Every number below
was computed directly from the current data files; the command is shown so
it can be re-run.

## One caveat before the numbers

My figures come from each academic's RSA/RSFAS profile page, which lists
a self-curated selection of their publications, not a complete output
list. Three other universities in this project (UQ, Monash, UniMelb) read
from a complete institutional source instead (an eSpace/Minerva
repository, or OpenAlex via ORCID). That difference cuts both ways here:
ANU's publication counts below are a floor, not a full count, and the
proportion rated A\*/A is upward-biased relative to a complete-output
university, since a self-curated list keeps the strongest work and drops
the rest. None of the individual numbers below are wrong for what they
measure — but a direct ANU-vs-another-university comparison on volume or
average quality isn't valid until ANU also moves to a complete-output
source, which is the next piece of work, not something already done.

## Headline numbers

- **44 researchers** (33 Accounting, 11 Finance), all with an academic
  level assigned (B: 14, C: 12, D: 7, E: 11).
- **296 publications**, of which 287 are journal articles. The rest are
  4 industry reports, 3 conference-presentation citations, 1 book chapter
  and 1 textbook — kept and tagged rather than dropped, since they're real
  entries on a researcher's profile, just not journal articles for the
  ranking count.
- **258 of those 296 (87%) now carry a real ABDC rating** — 101 A\*, 139 A,
  14 B, 4 C, and 29 checked against the official ABDC list and confirmed
  not on it (recorded as "none," not left blank, per your 12 Aug
  instruction). This is new since the last time this was reported — ANU
  had no ABDC rating in the data at all before this pass.

```
python -c "import csv; from collections import Counter; print(Counter(r['quality_rank'] for r in csv.DictReader(open('anu_publications.csv', encoding='utf-8'))))"
```

## Coverage, field by field

| Field | Coverage | Note |
|---|---|---|
| title, publication_type, author_count | 296/296 (100%) | |
| journal_name | 287/296 (97%) | the 9 blanks are the non-journal items above |
| year | 288/296 (97%) | |
| ABDC quality_rank | 258/296 (87%) rated, 29/296 confirmed unrated | |
| Scimago quartile | 244/296 (82%) | |
| distinct journals with an ISSN | 103/129 (80%) | from the ABDC/Scimago join |
| DOI | 74/296 (25%) | see "known gap" below |
| citation percentile (OpenAlex) | 72/296 (24%) | tracks the DOI figure — OpenAlex needs a DOI to look a paper up |

## The one real known gap: DOI coverage

ANU's profile pages don't reliably show a DOI or a working link the way
some other universities' repositories do — this is a property of the
source page, not something the scraper is missing. 25% is genuinely lower
than UQ's roughly 90%+ DOI coverage from their institutional repository
export.

Two consequences, both already handled rather than hidden:

- **222 publications have no DOI at all** and are listed in
  `output/anu_doi_manual_lookup.csv` (researcher, title, journal, year,
  and the article URL we do have) for manual follow-up against Informit or
  a similar source, per your 19 Aug instruction. This hasn't been worked
  through yet — it's a to-do list, not a completed check.
- **Citation data (OpenAlex) is only available for the same 25%**, since
  OpenAlex looks papers up by DOI. Closing this gap properly means
  matching each researcher to their OpenAlex author record directly
  (via ORCID, the same method Monash used) rather than matching paper by
  paper — that's real, unbuilt work, not something we're pretending is
  done. It's on the list for after this meeting; see "what's not done" below.

## What's not done, and why

- **Author-level citation harvest (ORCID → OpenAlex).** Would raise
  coverage above the 25% DOI ceiling by looking up each researcher
  directly rather than each paper. Not started this sprint — it's a
  genuinely large piece of work, and the team's other university (Monash)
  has a working version of this approach that's specific to their own
  source pages. We want to agree on one shared way of doing this across
  universities rather than each of us building our own, per the "uniform
  your method" instruction — that conversation is happening at this
  meeting, not pre-empted by me building an ANU-only version in the
  meantime.
- **23 publications are logged for manual review**
  (`output/anu_unparsed_publications.csv`) rather than trusted — mostly
  citations the parser couldn't confidently split into title/journal/year,
  kept rather than guessed at.
- **11 researchers have no inline Publications section** on their profile
  page at all (`output/anu_no_publications.csv`) — a genuine gap in this
  data source, not a parsing failure. Their ANU profile only links out to
  the university's Pure research portal, which is behind bot-detection we
  deliberately don't try to defeat.
- **One Emeritus Professor** on staff (Neil Fargher) — checked against
  your 19 Aug rule to exclude an Emeritus Professor with no output; he has
  recorded publications, so no exclusion applies.

## Specific things worth your check

- **101 A\* and 139 A ratings** is a high proportion of a 296-publication
  set — worth a sanity spot-check against a couple of researchers you know
  well, since a systematic ABDC mismatch would be a serious finding, and
  this is the first time this join has run for ANU.
- **The 29 "none" ratings** are journals ANU researchers publish in that
  are genuinely not on the current ABDC list (e.g. practitioner journals,
  SSRN, some finance-specialist outlets) — worth confirming that's the
  right treatment (kept and counted, rated "none") rather than something
  that should be excluded outright.
- **The 3 reclassified conference papers** (Alex Wang, presented at the
  same accounting-education conference under two venues) — confirms the
  "journals only" rule is now being applied correctly for this shape;
  worth checking whether other researchers have similar
  conference-presentation citations we should double-check.
