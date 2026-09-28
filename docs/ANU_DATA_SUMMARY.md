# ANU — Accounting & Finance data summary

Covers the Research School of Accounting (RSA) and the Finance area of the
Research School of Finance, Actuarial Studies & Statistics (RSFAS) — the two
ANU schools within scope. Every number below was computed directly from the
current data files; the command is shown so it can be re-run.

**Updated 28 Sep 2026.** Numbers below are from `final output/anu/`,
written by the shared `run.py`/`export.py` pipeline. This replaces the
standalone `anu_scraper.py` output path (`output/anu_*.csv`) this page
originally referenced — see README_ANU.md for where that standalone output
still exists and how its schema differs.

## One caveat before the numbers

Figures come from each academic's RSA/RSFAS profile page (plus their own
ORCID/Crossref/OpenAlex records where a validated ORCID is available), not
a complete institutional output list. Where a researcher has no validated
ORCID, their count is a floor, not a full count. `docs/DECISIONS.md` has
the full history of what's changed and why since this page was first
written.

## Staff count — two different numbers, on purpose

`final output/anu/anu_staff.csv` currently has **40 rows**. That is not
the size of the ANU accounting/finance roster — it's the roster **after**
`export.py` drops any staff member with zero publications, which is
`run.py`'s default behaviour (`drop_staff_without_pubs=True` unless
`--keep-empty-staff` is passed). This is existing pipeline behaviour, not
something changed here, and changing it is a separate team question.

The full roster — confirmed from the same pipeline run's own records
before that drop happens (git history: commit `09ba3a1`, "fresh data
run") — is **46 researchers** (33 Accounting, 13 Finance; academic levels
B: 14, C: 12, D: 9, E: 11). The 6 dropped are the same 6 named below under
"6 researchers still have no publications." Nobody joined or left the
roster between that run and this one — the same 46 names, the same 6
zero-publication names, checked directly rather than assumed.

```
python3 -c "
import subprocess, csv, io
from collections import Counter
text = subprocess.run(['git','show','09ba3a1:final output/anu/anu_staff.csv'],
    capture_output=True, text=True, encoding='utf-8').stdout
rows = list(csv.DictReader(io.StringIO(text)))
print(len(rows), Counter(r['field_of_research'] for r in rows), Counter(r['academic_level'] for r in rows))
"
```

Staff-level ORCID coverage is reported below against the full 46-person
roster, not the 40-row export, since a zero-publication researcher can
still have a validated ORCID on record.

## Headline numbers

```
python -c "import csv; print(sum(1 for _ in csv.DictReader(open('final output/anu/anu_publications.csv', encoding='utf-8'))))"
```

- **508 publications**, all journal articles. Non-journal-article types are
  handled two different ways, not one — worth stating precisely rather
  than glossing over:
  - Items the profile parser can recognise as non-journal (a conference
    location in place of a journal, a "Book chapter of …" venue, a textbook
    edition, a citation with no journal at all) are classified as such and
    never reach the export filter. **This does not catch everything:** a
    citation that names a venue in the journal position is taken as a
    journal article whatever the venue is. On 28 Sep, 14 such rows were
    found and excluded — newspaper articles, a press release, research
    reports and book chapters (see docs/DECISIONS.md, 28 Sep).
  - **Book chapters, and any other non-journal item the parser cannot
    recognise, are not filtered out automatically.** They are excluded one
    confirmed row at a time via `data/publication_exclusions.csv` — the
    same reviewed-evidence mechanism used for namesake and off-field
    exclusions, with a DOI or title, reason and evidence URL recorded per
    row.
  - **Off-field clinical papers are now a rule, not a list.** Wai-Man
    (Raymond) Liu is a genuine ANU accounting/finance academic who also,
    genuinely, co-authors clinical medicine papers. 40 of his rows were
    excluded by name+DOI/title on 21 Sep 2026 — a list of specific rows.
    A fresh scrape on 22 Sep found 10 MORE of his clinical rows that
    weren't on that list, because a list can only ever catch a row it has
    already seen. On 24 Sep this became a journal-name keyword screen
    (`ANU_OFF_FIELD_JOURNAL_KEYWORDS` in `export.py`, applied at export to
    every ANU row) so a future fresh scrape can't reintroduce the same
    class of row again. The original 40-row list is unchanged and still
    applied alongside the new rule — see docs/DECISIONS.md's 24 Sep entry.
- **496 of those 508 (97.6%) carry a real ABDC rating** — 182 A\*, 269 A,
  39 B, 6 C, and 12 with no ABDC match (genuinely not on the ABDC list, a
  practitioner periodical awaiting a client decision, or a journal name
  that could not be verified — see docs/DECISIONS.md, 28 Sep).

```
python -c "import csv; from collections import Counter; print(Counter(r['quality_rank'] for r in csv.DictReader(open('final output/anu/anu_publications.csv', encoding='utf-8'))))"
```

## Coverage, field by field

| Field | Coverage | Note |
|---|---|---|
| year | 505/508 (99.4%) | the blanks are cases where no 4-digit year could be confirmed outside the title itself, or an implausible year (<1950 or >current+1) — left blank rather than guessed |
| ABDC quality_rank | 496/508 (97.6%) | ISSN-first, exact normalised-title fallback where there's no ISSN |
| Scimago quartile (`sjr_quartile`) | 477/508 (93.9%) | |
| citation percentile (OpenAlex) | 448/508 (88.2%) | tracks DOI coverage — OpenAlex needs a DOI to look a paper up |
| DOI | 450/508 (88.6%) | |
| distinct journals (`anu_journals.csv` rows) | 142 | |
| `anu_journals.csv` rows with an ISSN | 133/142 (93.7%) | |
| `anu_journals.csv` rows with an `impact_factor` (Clarivate JIF) | 117/142 (82.4%) | |
| staff with a validated ORCID | 33/46 (71.7%) | against the full roster, not the 40-row export — see above |

```
python -c "
import csv
pubs = list(csv.DictReader(open('final output/anu/anu_publications.csv', encoding='utf-8')))
n = len(pubs)
for field in ('year','quality_rank','sjr_quartile','citation_percentile','doi'):
    c = sum(1 for r in pubs if (r[field] or '').strip())
    print(field, c, n, f'{c/n*100:.1f}%')
"
```

## What's not done, and why

- **researchportalplus.anu.edu.au (Pure portal) is still out of scope** —
  it sits behind Cloudflare bot detection the team decided not to try to
  defeat. This remains the main ceiling on ANU coverage: a staff member
  with no validated ORCID and a sparse or missing profile-page
  Publications section is still under-counted.
- **6 researchers still have no publications at all**: Bonnie Allan, Ian
  McPhee, Jean You, Keturah Whitford, Pat Barrett, Yue Cai. Each was
  checked: no Publications section on their page, no seed or validated
  page ORCID to retrieve from elsewhere. These 6 are the reason
  `anu_staff.csv` has 40 rows against a 46-person roster — see "Staff
  count" above.
- **Wai-Man (Raymond) Liu's medical/health rows — a rule now, not a
  list.** 40 rows excluded by name+DOI/title on 21 Sep 2026; a further 10
  caught by a fresh scrape on 22 Sep that the list couldn't (see
  "Headline numbers" above); on 24 Sep the exclusion became a journal-name
  screen so a future scrape can't reintroduce the same class again. One
  row, in health economics with an ABDC rating, remains deliberately
  **kept** — health economics is treated as inside scope, and the screen
  is checked directly, in a test, never to catch it. **The client has not
  yet confirmed this scope decision** — it is the team's own judgement
  call, applied and fully reversible if the client decides differently.
  Full detail: docs/DECISIONS.md's 21 Sep and 24 Sep entries.
- **Chao Gao's missing paper is back** ("Investment Performance of Credit
  Risk Transfer Securities (CRTs): The Early Evidence", *Journal of Fixed
  Income*, A). Cause: its DOI in `data/anu_doi_backfill.csv` was an SSRN
  preprint DOI, which the shared clean step retypes as "Preprint" and the
  export then drops. Fixed at the input — the backfill now carries the
  Crossref-verified published DOI (as do three other SSRN rows); see
  docs/DECISIONS.md, 28 Sep.
- **4 genuine journal articles are still missing** because their profile
  citations are too irregular for the parser (Lily Chen, IEEE TKDE 2022;
  Neil Fargher, *Accounting and Finance* 53(1); Kathy Wang ×2, 2025). Listed
  in `scratch/_anu25/REPORT.md`, not added by hand.
- **Unchanged-code drift.** Re-running the current `main` code without any
  ANU change does not reproduce the 24 Sep export exactly — a shared
  `export.py` step added on 23 Sep rewrites titles/years across co-author
  copies of the same DOI, and live ORCID/OpenAlex/Crossref data has moved
  (e.g. Sarah Adams's *Third Sector Review* row now resolves to a UWA
  repository name and loses its C rating; Tracy (Kun) Wang's *Journal of
  Law and Economics* row lost its DOI). Reported for the team, not fixed
  here — see docs/DECISIONS.md, 28 Sep.
- **3 near-duplicate candidates in UNSW's own committed data remain
  ambiguous** rather than clearly resolved — not applied to UNSW's files
  either way; see docs/DECISIONS.md.
- **A published-correction guard was added to the shared export code on
  22 Sep**, affecting every university's next export, not just ANU's. ANU
  itself has 0 rows matching the pattern today.

## Recent history (see docs/DECISIONS.md for full detail)

- **15 Sep 2026**: heading-truncation fix, page-ORCID fallback, ABDC title
  fallback + ISSN backfill, curly-quote/year dedup fix, near-duplicate
  merge (FIX G), author-list-as-title fix (FIX H).
- **18 Sep 2026**: whole-number export columns (`_whole_numbers()`), seven
  title repairs, four SSRN working-paper exclusions, exact
  title/year/journal duplicate merge (FIX K), prefix-containment duplicate
  merge (FIX L, 22 pairs).
- **21 Sep 2026**: PR #44 merge reconciliation with `main` (two
  duplicate-detection edge cases fixed: identical-DOI pairs, and a
  minimum-length/word-count guard against generic headings like
  "Discussion"); the 40-row Wai-Man (Raymond) Liu off-field exclusion.
- **22 Sep 2026**: a UTF-8 encoding pin at the scraper's HTTP layer (fixed
  a mojibake title); "Third Sector Review" journal-name resolution; five
  book chapters excluded; three Crossref-authoritative title-casing
  repairs; a published-correction guard added to the shared export code.
  A parallel fresh pipeline run, merged the same day, found 10 more of
  Liu's clinical rows the 21 Sep list couldn't catch.
- **24 Sep 2026**: the Liu off-field exclusion turned from a list into a
  journal-name rule (`ANU_OFF_FIELD_JOURNAL_KEYWORDS`, `export.py`, 10
  rows); one merge-lost row recovered from its own JSON data, one
  confirmed genuinely missing and left for a fresh pipeline run rather
  than hand-added; one further Crossref/OpenAlex-unresolvable all-caps
  title resolved against ANU's own institutional repository instead.
- **28 Sep 2026**: Chao Gao's lost paper recovered by replacing SSRN
  backfill DOIs with Crossref-verified published DOIs (4 rows; the 2 Ball
  and Brown rows refused); Susanna Ho's ECIS 2009 "Panel:" row excluded; a
  journal-name completion rule for names cut at a comma or a partial
  italic run, plus a special-issue tail strip (3 rows ranked, 2 verified
  DOIs added); 14 newspaper/press-release/report/book-chapter rows
  excluded; one verified DOI added for Lily Chen; the stale 23 Aug
  unparsed file deleted.

Publication count across this history: 574 (15 Sep) → 588 (18 Sep, live
re-scrape) → 587 (FIX K) → 565 (FIX L) → 565 (PR #44 merge, unchanged) →
525 (21 Sep, Raymond Liu 40-row exclusion) → 520 (22 Sep, this branch's own
work) → 532 (22 Sep, merged with a parallel fresh pipeline run that added
10 more Liu clinical rows + 4 unrelated Susanna Ho rows, and lost 2 rows —
see docs/DECISIONS.md) → 522 (24 Sep, the 10 Liu rows excluded by rule) →
523 (24 Sep, the 1 recoverable lost row restored) → **508 (28 Sep,
current — +1 Chao Gao, −1 Panel, −14 non-journal items, −1 duplicate
merged by a verified DOI)**.
