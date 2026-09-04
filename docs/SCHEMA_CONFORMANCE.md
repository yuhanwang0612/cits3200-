# Schema conformance — eight universities, one page

Read-only survey of every publication/staff/journal file actually committed
somewhere in the repo, against the Scope of Work §3.5.4 data dictionary.
State of the repo as read 25 Aug 2026 (`git fetch --all --prune` immediately
before). Factual only — this records what exists and what's filled, not a
judgement on any of it. Where something can't be assessed from the committed
data alone, that's said plainly rather than guessed at. Every count in this
version was recomputed directly from the files, using a CSV parser rather
than a line count — several publication files contain quoted fields with
embedded newlines, which throws off a naive line count (UNSW's file is the
clearest example: 2,005 lines, 2,000 actual data rows).

Files covered: `uq_publications.csv`, `unimelb_publications.csv`,
`uwa_publications.csv`, `monash_publications.csv`, `adelaide_publications.csv`,
`usyd_publications.csv` (all on `main`); `anu_publications.csv` (this
branch); `unsw/output/unsw_publications.csv` (`zarin-branch`, not yet on
`main`).

## Group of Eight coverage

| University | Status | Publications file | Staff file | Journal file |
|---|---|---|---|---|
| ANU | this branch (`anu-scraper`), unmerged | `anu_publications.csv` — 296 rows | `anu_staff.csv` — 44 rows | `anu_journals.csv` — 129 rows |
| Adelaide | `main` | `adelaide_publications.csv` — 27,293 rows | `adelaide_staff.csv` — 357 rows | none (quality figures flattened onto publication rows) |
| Monash | `main` | `monash_publications.csv` — 4,273 rows | `monash_staff.csv` — 145 rows | none (flattened) |
| UNSW | `zarin-branch`, unmerged | `unsw/output/unsw_publications.csv` — 2,000 rows | `unsw/output/unsw_staff.csv` — 93 rows | `unsw/output/journals.csv` — 571 rows |
| UQ | `main` | `uq_publications.csv` — 662 rows | `staff.csv` and a duplicate `people.csv` — 43 rows each | `journals.csv` — 208 rows, but not in the §3.5.4 shape (see Journal entity section) |
| UniMelb | `main` | `unimelb_publications.csv` — 269 rows | none on `main` (see note below) | none (flattened) |
| USyd | `main` | `usyd_publications.csv` — header only, **0 data rows** | none | none |
| UWA | `main` | `uwa_publications.csv` — 696 rows | none on `main` (see note below) | none (flattened) |

USyd's file was checked both on `main` and on its own source branch
(`saeed/usyd-openalex-pipeline`) — it's 0 rows in both places, so this isn't
an artefact of a partial merge; no USyd publications have been harvested
into a committed file yet, though the column schema for one is in place.

UniMelb and UWA staff-level data does exist, but not on `main`: the
`alex-branch-test` branch (not merged, and not requested for this pass)
carries `unimelb_accounting_staff.csv`, `unimelb_finance_staff.csv`,
`uwa_accounting_staff.csv` and `uwa_finance_staff.csv`, split by discipline,
under a different pipeline (Node/JS scripts, not the Python one `main` uses).
`main`'s `unimelb_publications.csv`/`uwa_publications.csv` instead trace to a
different branch, `alex/unified-publication-csvs`, which never had a
per-university staff file at all — so the two branches disagree with each
other, and only the one without a staff file made it to `main`.

## §3.5.4 Publication entity: `title, doi, year, article_url, source, citation_percentile`

Column matrix — a name in a cell means that file has a column by that name
for that concept; blank means the file has no equivalent column at all.
USyd is included by column header even though it currently has zero rows.

| Concept | uq | unimelb | uwa | usyd | monash | adelaide | anu | unsw |
|---|---|---|---|---|---|---|---|---|
| researcher link | `name` | `name` | `name` | `name` | `researcher` | `researcher` | `researcher_name` | `researcher_name` |
| title | `title` | `title` | `title` | `title` | `title` | `title` | `title` | `title` |
| journal | `journal_name` | `journal_name` | `journal_name` | `journal_name` | `journal_name` | `journal_name` | `journal_name` | `journal_name` |
| year | `year` | `year` | `year` | `year` | `year` | `year` | `year` | `year` |
| doi | `doi` | `doi` | `doi` | `doi` | `doi` | `doi` | `doi` | `doi` |
| article_url | `article_url` | `article_url` | `article_url` | `article_url` | `article_url` | `article_url` | `article_url` | `article_url` |
| source | `source` | `source` | `source` | `source` | `source` | `source` | `source` | `source` |
| citation_percentile | `citation_percentile` | `citation_percentile` | `citation_percentile` | `citation_percentile` | — | — | `citation_percentile` | `citation_percentile` |
| author_count | `author_count` | `author_count` | `author_count` | `author_count` | `author_count` | `author_count` | `author_count` | `author_count` |
| quality_rank | `quality_rank` | `quality_rank` | `quality_rank` | `quality_rank` | `quality_rank` | `quality_rank` | `quality_rank` | — |
| sjr_quartile | `sjr_quartile` | `sjr_quartile` | `sjr_quartile` | `sjr_quartile` | `scimago_quartile` | `scimago_quartile` | `sjr_quartile` | — |
| issn | — | `issn` | `issn` | `issn` | `issn` | `issn` | `issn` | — |
| university | — | `university` | `university` | `university` | `university` | `university` | `university` | `university` |
| field_of_research | — | `field_of_research` | `field_of_research` | `field_of_research` | `field_of_research` | `field_of_research` | `field_of_research` | `field_of_research` |
| unique record ID | `espace_id` | `espace_id` | `espace_id` | `espace_id` | — | — | — | — |
| forthcoming flag | — | — | — | — | — | — | `forthcoming` | — |

**researcher link column name — 3 of 8 use `name`, 2 use `researcher`, 3 use
`researcher_name`.** uq/unimelb/uwa/usyd all use `name`; monash/adelaide use
`researcher`; anu/unsw use `researcher_name`. These are the four eSpace-style
files (uq, unimelb, uwa, usyd — all carry `espace_id`) versus the four that
don't use eSpace at all.

**`quality_rank`/`sjr_quartile`-equivalent column naming differs between
monash/adelaide and the rest.** Both use `scimago_quartile` where every
other file uses `sjr_quartile` for what is otherwise the same concept.

**`citation_percentile` has no column at all in monash or adelaide**,
unlike the other six files, which all at least have the column (whether or
not it's filled — see fill rates below).

**`author_count` is universal but not in the base §3.5.4 list** — present
in all eight files.

### Fill rates, per publication row

| Column | uq (662) | unimelb (269) | uwa (696) | usyd (0) | monash (4,273) | adelaide (27,293) | anu (296) | unsw (2,000) |
|---|---|---|---|---|---|---|---|---|
| title | 100% | 100% | 100% | n/a | 100% | 100% | 100% | 100% |
| journal_name | 100% | 94% | 77% | n/a | 89% | 87% | 97% | 99% |
| year | 100% | 100% | 100% | n/a | 100% | 100% | 97% | 100% |
| doi | 93% | 87% | 70% | n/a | 78% | 83% | 25% | 55% |
| article_url | 93% | 100% | 100% | n/a | 78% | 83% | 33% | 69% |
| author_count | 100% | 100% | 100% | n/a | 100% | 100% | 100% | 100% |
| quality_rank (any value) | 87% | 64% | 72% | n/a | 20% | 2% | 97% | no column |
| sjr_quartile-equivalent | 92% | 0% | 0% | n/a | 31% | 43% | 82% | no column |
| citation_percentile | 91% | 0% | 0% | n/a | no column | no column | 24% | 0% |
| issn | no column | 88% | 76% | n/a | 72% | 67% | 24% | no column |

Not a ranking — each number reflects a different source's own reach, not
effort. uq's citation_percentile/sjr coverage in the 90s reflects having run
the OpenAlex/Scimago join already; unimelb/uwa/unsw's 0% on citation_percentile
means the column exists but that join hasn't been run on those files yet, not
that it failed. anu's doi/article_url/issn numbers are the lowest of the
eight because its source (individual profile pages) doesn't reliably publish
either. monash and adelaide's quality_rank fill rates (20% and 2%) are the
lowest in the table — those two files carry the column but it's mostly
empty; this is the same "column exists, join not run" pattern as the 0%
cells above it, not a distinct issue, though at this volume it's the
largest block of unfilled quality-rank cells of any file surveyed.

anu's `quality_rank` still records the literal string `"none"` rather than
leaving the cell blank when a journal has no ABDC rating (29 of 296 rows),
per the client's 12 Aug instruction that an unranked outlet is a finding,
not a gap. That convention is unchanged from the 23 Aug version and is the
reason anu's quality_rank fill rate (97%) reads higher than files that leave
the same underlying fact blank.

### Publications per researcher

| University | Publication rows | Distinct researchers (publication file) | Publications per researcher |
|---|---|---|---|
| Adelaide | 27,293 | 202 | 135.1 |
| Monash | 4,273 | 88 | 48.6 |
| UNSW | 2,000 | 83 | 24.1 |
| UWA | 696 | 32 | 21.8 |
| UQ | 662 | 38 | 17.4 |
| ANU | 296 | 33 | 9.0 |
| UniMelb | 269 | 54 | 5.0 |
| USyd | 0 | 0 | n/a |

"Distinct researchers" is a count of distinct values in each file's own
researcher-link column (see the Publication entity matrix above for which
column that is per file) — it is not cross-checked against a staff list
here except where noted in the Adelaide section below. The ratio is not a
measure of scraper quality by itself; a high ratio is exactly what a
complete-output feed for a small number of very senior, very prolific
researchers should look like. Adelaide's ratio is more than double the next
highest (Monash) and 27x ANU's — see below for what a closer look at the
Adelaide file shows.

### Adelaide: is the data scoped to accounting and finance?

Both `adelaide_publications.csv` and `adelaide_staff.csv` carry a
`field_of_research` column. In `adelaide_publications.csv`, all 27,293 rows
read `Accounting & Finance` — every row, no other value present. The same is
true of `adelaide_staff.csv`: all 357 rows read `Accounting & Finance`. The
column exists and is fully filled, but it takes exactly one value across
both files — it does not distinguish any row from any other, so it can't by
itself be used to confirm or rule out discipline scope.

`adelaide_staff.csv`'s 357 rows fully account for every researcher name that
appears in `adelaide_publications.csv`: there are 202 distinct researcher
names in the publications file, and all 202 also appear in the staff file
(0 publication-file researchers are missing from staff). The remaining 155
staff rows have no publications in the file at all.

A random sample of 20 titles from `adelaide_publications.csv` (seeded for
reproducibility) includes, among others: "Search for supersymmetry in final
states with charm jets and missing transverse momentum in 13 TeV pp
collisions" (Journal of High Energy Physics), "Enhanced T-ray signal
classification using wavelet preprocessing" (Medical & Biological
Engineering & Computing), "Effects of surface modified nanosilica on
drilling fluid and formation damage" (Journal of Petroleum Science and
Engineering), "Technical Perspectives on Cyber Diplomacy", "First- and
Second-Order Sensitivities of Steady-State Solutions to Water Distribution
Systems", and "Nutritional Aspects of Single Cell Oils". None of the 20
sampled titles reads as accounting or finance research.

The researchers with the most rows in the file are consistent with this:
the top three by row count are "Prof Paul Jackson" (3,514 rows — a
high-energy physics researcher based on journal titles such as Journal of
High Energy Physics), "Derek Abbott" (1,056 rows — electrical/biomedical
engineering, per the T-ray title above) and "EPrf Kym Anderson" (1,007 rows
— agricultural economics). All three are present in `adelaide_staff.csv`
with `field_of_research` recorded as `Accounting & Finance`.

One further pattern: names in both Adelaide files carry an academic-title
prefix baked directly into the name string (`Prof`, `APrf`, `EPrf`,
`PfDr`) — no other university's staff or publication file in this repo does
this; the other seven keep title and name in separate columns or omit the
title.

Taken together — a `field_of_research` column that is fully filled but
carries only one literal value across both files, a publications-per-staff
ratio 2.75x the next-highest source, and a sampled/top-volume set of titles
and journals outside accounting and finance for the highest-volume
researchers — the data in `adelaide_publications.csv` and
`adelaide_staff.csv` does not read as scoped to accounting and finance. It
reads as a whole-university (or at least multi-faculty) research output
feed with a constant `Accounting & Finance` label applied uniformly,
regardless of each row's actual subject area.

## §3.5.4 Researcher entity: `name, job_title, academic_level, field_of_research, profile_url, university`

Five staff files exist as committed data: `staff.csv` (`main`, UQ only — 43
rows), `people.csv` (`main`, also UQ only — 43 rows, same 43 researchers as
`staff.csv` with three columns renamed: `job_title`→`rank`,
`academic_level`→`level`, `field_of_research`→`discipline`; every name,
espace_id and profile_url matches `staff.csv` exactly, row for row), `
monash_staff.csv` (`main` — 145 rows), `adelaide_staff.csv` (`main` — 357
rows), `unsw/output/unsw_staff.csv` (`zarin-branch` — 93 rows), and
`anu_staff.csv` (this branch — 44 rows). UniMelb and UWA have no
staff-level file on `main` — see the Group of Eight table above for where
their (differently structured) staff data actually lives.

| Column | uq (`staff.csv`) | uq (`people.csv`) | monash | adelaide | unsw | anu |
|---|---|---|---|---|---|---|
| name | `name` | `name` | `name` | `name` | `name` | `name` |
| job_title | `job_title` | `rank` | `job_title` | `job_title` | `job_title` | `job_title` |
| academic_level | `academic_level` | `level` | `academic_level` | `academic_level` | `academic_level` | `academic_level` |
| field_of_research | `field_of_research` | `discipline` | `field_of_research` | `field_of_research` | `field_of_research` | `field_of_research` |
| profile_url | `profile_url` | `profile_url` | `profile_url` | `profile_url` | `profile_url` | `profile_url` |
| university | `university` | `university` | `university` | `university` | `university` | `university` |
| unique record ID | `espace_id` | `espace_id` | — | — | — | — |
| extra | — | — | `level_code`, `h_index`, `pub_count` | `level_code`, `h_index` | `research_portal_url`, `school` | `research_portal_url`, `less_research_intensive` |

`people.csv` is the one mismatch on field names in this entity — it's a
duplicate of `staff.csv`'s own 43 UQ rows under three renamed columns, not a
different university's data.

## §3.5.4 Journal entity: `journal_name, issn, quality_rank, impact_factor, impact_factor_5yr`

`main`'s `journals.csv` (208 rows) does not have a `journal_name` column —
confirmed directly. Its actual columns are: `journal_key`, `journal`,
`journal_canonical`, `publisher`, `issns`, `abdc`. It has no `university`
column either, and its content (accounting/finance journal titles such as
"The International Journal of Accounting" and "SSRN Electronic Journal",
each with an ABDC letter grade) reads as a general journal-quality reference
list rather than one university's own harvested output — it cannot be
attributed to a specific university from its content alone. This is the
file `load.py`/`models.py` read as the Journal entity table; since it lacks
`journal_name`, running `load.py` against it fails immediately with
`KeyError: 'journal_name'`, before the script reaches staff or publication
data at all.

Two files exist that do match the §3.5.4 Journal entity shape and share an
identical column layout with each other: this branch's `anu_journals.csv`
(129 rows: `journal_name`, `journal_canonical`, `issn`, `issn_online`,
`issn_scimago`, `quality_rank`, `abdc_list_year`, `abdc_for_code`, `sjr`,
`sjr_quartile`, `h_index`, `cites_per_doc_2y`, `scimago_categories`,
`impact_factor`, `impact_factor_5yr`, `publication_count`,
`abdc_match_type`, `scimago_match_type`, `issn_conflict`) and
`zarin-branch`'s `unsw/output/journals.csv` (571 rows, same 19 columns).

UniMelb, UWA, USyd, Monash and Adelaide have no equivalent per-journal
table anywhere in the repo — their quality/quartile figures live only as
columns flattened onto each publication row.

## §3.5.4 Harvest entity: `source, last_run, latest_year`

`main`'s `harvest.csv` still has exactly **1 row — University of Queensland
only** (`UQ eSpace`, last run 2026-08-18), unchanged since 23 Aug despite
Monash, Adelaide and USyd all gaining publication data on `main` in that
time. Neither of those three merges added a harvest-entity row for their
own university.

`zarin-branch`'s `unsw/output/harvest.csv` has 4 rows (staff profile, ABDC,
OpenAlex, Scimago — all UNSW), and this branch's `harvest.csv` has 3 rows
(ABDC, OpenAlex, Scimago — all ANU). Both share the same column names as
`main`'s file. None of the three has ever been merged with another — each
sits only on its own branch/location.

## Filename collisions at the repo root

Two branches that the 23 Aug version flagged as future collision risks are
now resolved, because both have already been merged into `main` in
substance: `sprint1-monash-scraper`'s tree is now the same
`monash_publications.csv`/`monash_staff.csv` (plus `adelaide_*`) naming
that's on `main`, and `saeed/usyd-openalex-pipeline`'s tree — including its
own empty `usyd_publications.csv` — matches what's on `main` too. Neither
branch currently holds a generic root-level filename that isn't also
already on `main` under the same name, so there's no live collision left
to resolve from either. `Sean-Branch` is in the same position with respect
to UQ's files.

What's left at the repo root on `main`, unprefixed, is UQ's own output:
`harvest.csv`, `harvest.json`, `staff.csv`, `staff.json`, `people.csv`,
`people.json`, `publications.json` (the JSON twin of `uq_publications.csv`,
left under its old generic name after the CSV was renamed) — plus
`journals.csv`/`journals.json`, which (per the Journal entity section
above) don't appear to be UQ-specific at all. None of these currently
collides with anything, since every other university's file is prefixed
(`monash_`, `adelaide_`, `unimelb_`, `uwa_`, `usyd_`, `uq_`) or namespaced
under a subdirectory (`unsw/output/`).

`anu-scraper` only collides on `harvest.csv`/`harvest.json` (unprefixed by
design — see `docs/DECISIONS.md`); its other deliverable files are
`anu_`-prefixed and don't collide with anything currently on `main`.

## Where this can't be assessed further

- **UniMelb and UWA's own scraper source.** `alex-branch-test` carries a
  materially different, discipline-split pipeline (`unimelb_accounting_*`,
  `unimelb_finance_*`, `uwa_accounting_*`, `uwa_finance_*`, all Node/JS)
  from the one that actually reached `main` (`alex/unified-publication-csvs`,
  Python-based, no staff file). Reading `alex-branch-test`'s output files in
  depth — row counts, discipline scoping, whether its numbers agree with
  `main`'s `unimelb_publications.csv`/`uwa_publications.csv` — wasn't done
  for this table; only the shape of its file tree was checked.
- **A new branch, `unsw_publication`,** appeared during the `git fetch` for
  this session (a forced update). It carries a root-level
  `unsw_publications.csv`, structured differently from `zarin-branch`'s
  `unsw/output/` layout used elsewhere in this document. It wasn't read in
  depth or included in any figure above, since this pass was scoped to
  `zarin-branch` for UNSW.
- **`research.db`** is committed on `main` despite `.gitignore` marking it
  as regenerated by `load.py`; given `load.py` currently fails on
  `journals.csv` before it would ever write that file, whatever's in the
  committed `research.db` was necessarily produced by an earlier, working
  version of the load pipeline, not the one presently in the repo. Its
  contents weren't compared against the current CSVs for this table.
