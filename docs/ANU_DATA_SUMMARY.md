# ANU — Accounting & Finance data summary

Covers the Research School of Accounting (RSA) and the Finance area of the
Research School of Finance, Actuarial Studies & Statistics (RSFAS) — the two
ANU schools within scope. Every number below was computed directly from the
current data files; the command is shown so it can be re-run.

**Updated 15 Sep 2026** after the data-quality pass described in
`docs/DECISIONS.md`'s four "15 Sep 2026" entries (heading-truncation fix,
page-ORCID fallback, ABDC title fallback plus its ISSN-backfill addendum
(FIX E2), curly-quote/year dedup fix, title/journal/year parsing fixes,
near-duplicate merge (FIX G) and author-list-as-title fix (FIX H)).
Numbers below are from `final output/anu/`, written by the shared
`run.py`/`export.py` pipeline — this replaces the standalone
`anu_scraper.py` output paths (`output/anu_*.csv`) this page originally
referenced; those files no longer exist and `anu_publications.csv` now
contains only journal articles (everything else — conference papers,
research reports, book chapters, textbooks — is filtered out at export
rather than kept and tagged).

## One caveat before the numbers

My figures come from each academic's RSA/RSFAS profile page (plus, since 15
Sep, their own ORCID/Crossref/OpenAlex records where a validated ORCID is
available), not a complete institutional output list. Where a researcher has
no validated ORCID, their count is still a floor, not a full count, in the
way it always was. See docs/DECISIONS.md for exactly which 15 staff gained a
validated ORCID this pass and which one page candidate was rejected.

## Headline numbers

- **46 researchers** (33 Accounting, 13 Finance), all with an academic
  level assigned (B: 14, C: 12, D: 9, E: 11).
- **574 publications**, all journal articles (non-journal-article types —
  conference papers, research reports, book chapters, textbooks — are
  filtered out by `export.py`, not included in this file). Down from 610
  after FIX G (32 near-duplicate rows merged) and FIX H (3 rows that
  weren't real journal articles at all — two textbook citations and one
  whose real title, written in lower case on the page itself, couldn't be
  confidently recovered — correctly excluded rather than shipped wrong).
- **495 of those 574 (86.2%) carry a real ABDC rating** — 182 A\*, 267 A, 40
  B, 6 C, and 79 with no ABDC match (either genuinely not on the ABDC list,
  or no journal name to match against). ABDC matching is ISSN-first, falling
  back to an exact normalised-title match when there is no ISSN — see FIX E
  in docs/DECISIONS.md.

```
python -c "import csv; from collections import Counter; print(Counter(r['quality_rank'] for r in csv.DictReader(open('final output/anu/anu_publications.csv', encoding='utf-8-sig'))))"
```

## Coverage, field by field

| Field | Coverage | Note |
|---|---|---|
| title, journal_name | 574/574 (100%) | |
| year | 566/574 (98.6%) | the blanks are cases where no 4-digit year could be confirmed outside the title itself, or (FIX H) an implausible year (<1950 or >current+1) — left blank rather than guessed |
| ABDC quality_rank | 495/574 (86.2%) | ISSN-first, title fallback where there's no ISSN — see FIX E |
| Scimago quartile | 513/574 (89.4%) | 74.4% before FIX E2's ISSN backfill — see below |
| citation percentile (OpenAlex) | 458/574 (79.8%) | tracks DOI coverage — OpenAlex needs a DOI to look a paper up (unaffected by FIX E2, which backfills from the ABDC sheet, not OpenAlex) |
| distinct journals | 179 | |
| DOI | 471/574 (82.1%) | |
| staff with a validated ORCID | 33/46 (71.7%) | 18 from the hand-verified seed (`data/anu_identity.csv`), 15 newly accepted from the researcher's own profile page this pass — see FIX D |
| `anu_journals.csv` rows with an ISSN | 150/198 (75.8%) | 49.3% before FIX E2 |
| `anu_journals.csv` rows with an `impact_factor` (Clarivate JIF) | 127/198 (64.1%) | 42.8% before FIX E2 |
| near-duplicate rows remaining (ratio ≥ 0.85, same researcher) | 0 | 32 removed by FIX G this run |

The run log (`scratch/_anu15/run_output.txt`) reports 604 of 698 journal
articles rated before type-filtering and the FIX F dedup pass — 427 by ISSN,
182 by the new title fallback. `abdc_match` (which of the two matched) is an
internal diagnostic field, not one of the exported columns, so the ISSN/title
split for the final 574-row/495-rated set specifically isn't recoverable from
`anu_publications.csv` alone.

**FIX E2 addendum**: 181 of those 182 title-matched rows had no ISSN of
their own and gained one from the ABDC sheet (the 1 exception: an ABDC
entry with no ISSN on the sheet either). That's what moved Scimago
coverage, journal-ISSN coverage and Clarivate JIF coverage — `quality_rank`
itself is unchanged, since E2 only ever adds an ISSN, never a rating. See
docs/DECISIONS.md's "15 Sep 2026 (addendum)" entry.

## What changed 15 Sep 2026 (see docs/DECISIONS.md for full detail)

- **Heading-truncation bug fixed** (FIX A): three staff who use an h3
  "Publications" heading with h4 sub-headings underneath it — Tracy (Kun)
  Wang, Mark Wilson, Rebecca Tan — went from 0 confident rows on their own
  page to 67, 37 and 17 total rows respectively (page + retrieved).
- **Page ORCIDs** (FIX D): 15 staff gained a validated ORCID read from their
  own profile page (1 candidate rejected — more than one distinct ORCID on
  the page). ORCID coverage rose from 39.1% to 71.7% of staff, which in turn
  unlocked ORCID/Crossref/OpenAlex retrieval for them.
- **ABDC title fallback** (FIX E): rows with no DOI (so no ISSN) can now be
  rated by an exact normalised journal-title match when there's no ISSN hit.
- **Duplicate page/retrieved copies removed** (FIX F): 6 ANU rows where a
  no-DOI page copy of a paper (curly vs straight quotes, or a differing
  year) sat next to the properly-identified ORCID/OpenAlex copy.
- **ABDC ISSN backfill** (FIX E2): a title-matched row with no ISSN of its
  own now picks up the ABDC sheet's own ISSN for that journal, so Clarivate
  (JIF) and Scimago (SJR/quartile) — both ISSN-only joins — can find it
  too. Scimago quartile coverage 74.4% -> 88.5% (of the 610-row set at the
  time; 89.4% of the current 574-row set after FIX G/H); Clarivate JIF
  coverage (of the pre-export ~698-row pool) 448 -> 613.
- **Near-duplicate rows merged** (FIX G): 32 ANU rows removed — a
  page-scraped copy of a paper and its ORCID/Crossref/OpenAlex copy,
  differing by a word or an SSRN-preprint-vs-real DOI, that the exact
  dedup rule (FIX F) couldn't see. 0 remain.
- **Author-list-as-title fixed** (FIX H): 3 rows that weren't real,
  confidently-parsed journal articles — two textbook citations and one
  whose real title (written lower case on the page) couldn't be
  confidently recovered — correctly excluded rather than shipped with a
  wrong title. 1 more row's year was corrected from a clearly wrong 1942
  (a page-range end mistaken for the year) to the real 2024.

## What's not done, and why

- **researchportalplus.anu.edu.au (Pure portal) is still out of scope** —
  it sits behind Cloudflare bot detection the team decided not to try to
  defeat. This remains the main ceiling on ANU coverage: a staff member with
  no validated ORCID and a sparse or missing profile-page Publications
  section is still under-counted.
- **6 researchers still have no publications at all**: Bonnie Allan, Ian
  McPhee, Jean You, Keturah Whitford, Pat Barrett, Yue Cai — down from 9
  before this pass. Each was checked: no Publications section on their page,
  no seed or validated page ORCID to retrieve from elsewhere.
- **10 rows still trip a data-quality heuristic** (down from 12 —
  title looks like a whole citation, a bare year inside the title, etc.) —
  listed individually in `scratch/_anu16/measure_after.txt` and
  `REPORT.md` rather than chased one by one, per this pass's own
  instruction not to keep adding narrow rules.
- **3 near-duplicate candidates in UNSW's own committed data remain
  ambiguous** rather than clearly resolved by FIX G's rule (not applied to
  UNSW's files this pass either way) — see docs/DECISIONS.md's FIX G/H
  addendum.
- **Wai-Man (Raymond) Liu's retrieved rows include several medical/health
  journals** (via his own validated ORCID — the ORCID record's name matches
  him exactly, but its own works list appears to include entries outside
  accounting/finance). `screen.py`'s existing 25% discipline-share threshold
  does not flag him (his share is 37.5%). Not removed — flagged for a
  team/client decision. See docs/DECISIONS.md.
