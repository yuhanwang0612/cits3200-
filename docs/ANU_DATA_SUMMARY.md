# ANU — Accounting & Finance data summary

Covers the Research School of Accounting (RSA) and the Finance area of the
Research School of Finance, Actuarial Studies & Statistics (RSFAS) — the two
ANU schools within scope. Every number below was computed directly from the
current data files; the command is shown so it can be re-run.

**Updated 5 Oct 2026 (v27).** Numbers below are from `final output/anu/`,
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

- **492 publications**, all journal articles. Non-journal-article types are
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
  - **Two classes are now caught by rule (5 Oct, v27):** book reviews
    (the DOI's Crossref record has a volume, runs to 4 pages or fewer
    and has no abstract; 2 rows) and conference papers that OpenAlex
    files under a journal (a DOI-less, OpenAlex-only row whose OpenAlex
    citation names a proceedings or conference; 2 rows, plus one
    already excluded by name).
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
- **481 of those 492 (97.8%) carry a real ABDC rating** — 173 A\*, 265 A,
  36 B, 7 C, and 11 with no ABDC match (genuinely not on the ABDC list, a
  practitioner periodical awaiting a client decision, or a journal name
  that could not be verified — see docs/DECISIONS.md, 28 Sep).

```
python -c "import csv; from collections import Counter; print(Counter(r['quality_rank'] for r in csv.DictReader(open('final output/anu/anu_publications.csv', encoding='utf-8'))))"
```

## Coverage, field by field

| Field | Coverage | Note |
|---|---|---|
| year | 492/492 (100%) | for a row with a DOI, the DOI record's print (issue) year, else its issued year — the convention the pipeline already followed on 96 of 133 split cases, now applied to every DOI row (v27) |
| ABDC quality_rank | 481/492 (97.8%) | ISSN-first, exact normalised-title fallback where there's no ISSN; never given to an ANU paper dated before the journal's ABDC "Year Inception" (v28) |
| Scimago quartile (`sjr_quartile`) | 463/492 (94.1%) | |
| citation percentile (OpenAlex) | 469/492 (95.3%) | tracks DOI coverage — OpenAlex needs a DOI to look a paper up |
| DOI | 469/492 (95.3%) | all lowercase; every row sharing a DOI carries that DOI record's registered title and year (v27) |
| author_count | 492/492 (100%) | from the DOI record (OpenAlex) wherever the row has a DOI; on a profile row, the length of an explicit author list that always includes the profile owner (v27). author_count equals the number of names in `authors` on every row |
| distinct journals (`anu_journals.csv` rows) | 140 | |
| `anu_journals.csv` rows with an ISSN | 134/140 (95.7%) | |
| `anu_journals.csv` rows with an `impact_factor` (Clarivate JIF) | 118/140 (84.3%) | |
| staff with a validated ORCID | 33/46 (71.7%) — 32 before v27, plus Kathy Wang | against the full roster, not the 40-row export — see above |

**v28 (5 Oct).** Three more fixes, each a rule or a reviewed data row:

- an ABDC rating is withdrawn when an ANU paper predates the journal's ABDC
  "Year Inception" (1 row: Rebecca Tan's 2000 "Flights of fancy" was rated
  as the 2016 *Journal of Financial Reporting*);
- 9 hand-verified DOIs were attached (`data/anu_doi_backfill.csv`);
- `publication_status` is "forthcoming" on 4 rows. Each is explicitly labelled
  forthcoming or in press on the researcher's own profile, and either has
  no DOI or has a DOI record with no volume and no print date. Every other
  row is "published".

The counts are now 481/492 ABDC-ranked (97.8%) and 469 DOIs.

Titles on DOI rows follow the DOI's registered title (v27). Two refinements were made on
5 Oct (v27.1):

- a subtitle Crossref registers separately is added only when it is not the journal's
  running head (not all caps, and not mostly a repeat of the title's own words);
- an all-caps registered title keeps the casing of a mixed-case copy of the same title
  where one exists. Greg Shailer's 1994 title has no such copy, so it stays in capitals.

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

## Is ANU cleaning complete?

Yes, within the agreed sources. Every known defect is either fixed in the
pipeline (and locked by `tests/test_anu_final_data.py` and
`tests/test_anu_final_rules.py`) or listed below as a deliberate,
explained limitation. Five scope questions are waiting on the client;
each is a single reversible setting. Every journal article on the 40
exported researchers' live profiles is in the export or deliberately
excluded.

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
  On 5 Oct the two together exclude 51 rows (41 by the reviewed list,
  10 by the journal-name rule). Full detail: docs/DECISIONS.md's 21 Sep
  and 24 Sep entries.
- **Chao Gao's missing paper is back** ("Investment Performance of Credit
  Risk Transfer Securities (CRTs): The Early Evidence", *Journal of Fixed
  Income*, A). Cause: its DOI in `data/anu_doi_backfill.csv` was an SSRN
  preprint DOI, which the shared clean step retypes as "Preprint" and the
  export then drops. Fixed at the input — the backfill now carries the
  Crossref-verified published DOI (as do three other SSRN rows); see
  docs/DECISIONS.md, 28 Sep.
- **Every journal article on the live profiles is accounted for (5 Oct,
  v27).** All 46 profiles were fetched. The 40 exported researchers list
  404 journal articles: 384 are in the export and 20 are Wai-Man
  (Raymond) Liu's clinical papers. None is missing. The 4 entries the
  parser could not read are covered: Lily Chen's IEEE TKDE 2022 paper
  is recovered from the DOI printed in its own citation; Neil Fargher's
  *Accounting and Finance* 53(1) paper was already exported via OpenAlex;
  Kathy Wang's two 2025 papers come from her ORCID (see below). Per
  researcher: `scratch/_anu27/coverage.csv`.
- **Kathy Wang's identity was fixed (v27).** She publishes as Dongyue
  Wang (ORCID 0000-0002-2170-2293, ANU Lecturer). The seed OpenAlex
  author id belonged to a namesake at the University of Washington. She
  goes from 3 rows to 8, all with DOIs.
- **Accepted limitation: 23 rows have no DOI**, all from profile pages.
  Each was searched on Crossref. 3 passed the strict check (same title,
  journal, year ±1, owner among the authors) on 5 Oct (v27); 9 more were
  verified by hand (author, journal and paper) and attached in v28. The
  rest have no Crossref record (law and tax journals, practitioner
  periodicals, older titles), except two deliberately left DOI-less:
  Isabel Wang's *Advances in Accounting Behavioral Research* chapter
  (Crossref calls it a book chapter, which the type filter would drop)
  and Rebecca Tan's IBER 2005 paper (Crossref dates its retrospective
  deposit 2011). They still count, with their journal's ABDC rating, but
  have no citation metrics.
- **Accepted limitation: 11 rows are unranked**, because their journal is
  not on the ABDC list, is a practitioner periodical or possible book
  series awaiting a client decision, or (Rebecca Tan's 2000 "Flights of
  fancy") shares a name with an ABDC journal founded later (v28).
- **Five client questions remain open** (two practitioner periodicals, a
  possible book series, a centre's own periodical, and the Liu clinical
  scope). Each is one reversible setting.
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
- **28 Sep 2026 (v26)**: author counts on ANU profile rows now come from
  the DOI record (27 → 0 DOIs with disagreeing counts across co-author
  rows); three duplicate profile copies of published rows excluded (Sorin
  Daniliuc, Susanna Ho, Neil Fargher); Sarah Adams's *Third Sector Review*
  row made durable by an ANU repository-journal rule; four single-case
  titles given their proper casing by an ANU guard after the shared DOI
  harmonisation step.
- **5 Oct 2026 (v27)**: Kathy Wang's identity fixed (3 → 8 rows); every
  ANU DOI row aligned to its DOI record (lowercase DOI, registered
  title, print year); a rule for DOI-less profile copies of published
  rows (13 excluded); rules for book reviews (2) and OpenAlex-mislabelled
  conference papers (2, plus the PACIS 2008 row by name); owner-inclusive
  author lists on profile rows; Lily Chen's TKDE paper recovered; 3
  verified DOIs added; a coverage check against every live profile.

Publication count across this history: 574 (15 Sep) → 588 (18 Sep, live
re-scrape) → 587 (FIX K) → 565 (FIX L) → 565 (PR #44 merge, unchanged) →
525 (21 Sep, Raymond Liu 40-row exclusion) → 520 (22 Sep, this branch's own
work) → 532 (22 Sep, merged with a parallel fresh pipeline run that added
10 more Liu clinical rows + 4 unrelated Susanna Ho rows, and lost 2 rows —
see docs/DECISIONS.md) → 522 (24 Sep, the 10 Liu rows excluded by rule) →
523 (24 Sep, the 1 recoverable lost row restored) → 508 (28 Sep, v25 — +1 Chao Gao, −1 Panel, −14 non-journal items, −1
duplicate merged by a verified DOI) → 505 (28 Sep, v26 — −3
duplicate profile copies of published rows) → **492 (5 Oct, v27, current —
+7 recovered (Kathy Wang ×6, Lily Chen ×1), −13 profile copies, −2 book
reviews, −3 conference papers, −1 same-DOI duplicate, −1 profile copy
merged with Kathy Wang's DOI row)**.
