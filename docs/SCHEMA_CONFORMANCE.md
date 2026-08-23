# Schema conformance — five universities, one page

Read-only survey of every publication/staff file actually committed
somewhere in the repo, against the Scope of Work §3.5.4 data dictionary.
State of the repo as read 24 Aug 2026 (`git fetch` immediately before).
Factual only — this records what exists and what's filled, not a judgement
on any of it. Where something can't be assessed from the committed data
alone, that's said plainly rather than guessed at.

Files covered: `uq_publications.csv`, `unimelb_publications.csv`,
`uwa_publications.csv` (all on `main`), `anu_publications.csv` (this
branch), `unsw/output/unsw_publications.csv` (`zarin-branch`, not yet on
`main`). Monash's and Sydney's scraper branches (`sprint1-monash-scraper`,
`saeed/usyd-openalex-pipeline`) exist but weren't included — the first
predates this table's scope decision, the second only appeared during
this session and hasn't been read yet.

## §3.5.4 Publication entity: `title, doi, year, article_url, source, citation_percentile`

All five files carry all six base fields, plus more of their own. Column
matrix — a name in a cell means that file has a column by that name for
that concept; blank means the file has no equivalent column at all:

| Concept | uq | unimelb | uwa | anu | unsw |
|---|---|---|---|---|---|
| researcher link | `name` | `name` | `name` | `researcher_name` | `researcher_name` |
| title | `title` | `title` | `title` | `title` | `title` |
| journal | `journal_name` | `journal_name` | `journal_name` | `journal_name` | `journal_name` |
| year | `year` | `year` | `year` | `year` | `year` |
| doi | `doi` | `doi` | `doi` | `doi` | `doi` |
| article_url | `article_url` | `article_url` | `article_url` | `article_url` | `article_url` |
| source | `source` | `source` | `source` | `source` | `source` |
| citation_percentile | `citation_percentile` | `citation_percentile` | `citation_percentile` | `citation_percentile` | `citation_percentile` |
| author_count | `author_count` | `author_count` | `author_count` | `author_count` | `author_count` |
| quality_rank | `quality_rank` | `quality_rank` | `quality_rank` | `quality_rank` | — |
| sjr_quartile | `sjr_quartile` | `sjr_quartile` | `sjr_quartile` | `sjr_quartile` | — |
| issn | — | `issn` | `issn` | `issn` | — |
| university | — | `university` | `university` | `university` | `university` |
| field_of_research | — | `field_of_research` | `field_of_research` | `field_of_research` | `field_of_research` |
| unique record ID | `espace_id` | `espace_id` | `espace_id` | — | — |
| forthcoming flag | — | — | — | `forthcoming` | — |

**researcher link column name — 3 of 5 use `name`, 2 use `researcher_name`.**
uq/unimelb/uwa all use `name`; anu/unsw both use `researcher_name`. Neither
file has been renamed to match the other — see anu-scraper's
`docs/DECISIONS.md` for the reasoning on that side.

**`quality_rank`/`sjr_quartile` blank-vs-"none" convention differs.**
uq/unimelb/uwa leave the cell blank when a journal has no ABDC rating.
anu records the literal string `"none"` instead, per the client's 12 Aug
instruction that an unranked outlet is a finding, not a gap — both are
legitimate readings of "unrated," but a naive fill-rate count on `anu`
will show a higher number filled than the other three for the same
underlying fact.

**`author_count` is universal but not in the base §3.5.4 list** — it's a
12 Aug client addition (with `doi`) layered on top of the original
dictionary, present in all five files.

### Fill rates, per publication row

| Column | uq (662) | unimelb (269) | uwa (696) | anu (296) | unsw (2000) |
|---|---|---|---|---|---|
| title | 100% | 100% | 100% | 100% | 100% |
| journal_name | 100% | 94% | 77% | 97% | 99% |
| year | 100% | 100% | 100% | 97% | 100% |
| doi | 93% | 87% | 70% | 25% | 55% |
| article_url | 93% | 100% | 100% | 33% | 69% |
| author_count | 100% | 100% | 100% | 100% | 100% |
| quality_rank (any value incl. "none") | 87% | 64% | 72% | 97% | — (no column) |
| sjr_quartile | 92% | 0% | 0% | 82% | — (no column) |
| citation_percentile | 91% | 0% | 0% | 24% | 0% |
| issn | — (no column) | 88% | 76% | 24% | — (no column) |

Not a ranking — each number reflects a different source's own reach, not
effort. uq's citation_percentile/sjr_quartile coverage in the 90s reflects
having run the OpenAlex/Scimago join already; unimelb/uwa's 0% on those
same two columns means the column exists but that join hasn't been run on
those files yet, not that it failed. anu's DOI/article_url/issn numbers
are the lowest of the five because its source (individual profile pages)
doesn't reliably publish either — see anu-scraper's `docs/DECISIONS.md`
methodology-gap entry for the fuller picture, which also affects how
anu's citation_percentile and quality_rank numbers should be read
alongside the other four.

## §3.5.4 Researcher entity: `name, job_title, academic_level, field_of_research, profile_url, university`

Only three staff files exist as committed data: `staff.csv` (`main`, UQ
only — 43 rows, not updated when unimelb/uwa's publication files merged),
`unsw_staff.csv` (`zarin-branch`), and `anu_staff.csv` (this branch).
UniMelb and UWA's own staff-level files weren't located in the committed
tree — their researcher attributes (`university`, `field_of_research`)
live only as columns on the publication rows themselves.

| Column | uq (staff.csv) | unsw | anu |
|---|---|---|---|
| name | `name` | `name` | `name` |
| job_title | `job_title` | `job_title` | `job_title` |
| academic_level | `academic_level` | `academic_level` | `academic_level` |
| field_of_research | `field_of_research` | `field_of_research` | `field_of_research` |
| profile_url | `profile_url` | `profile_url` | `profile_url` |
| university | `university` | `university` | `university` |
| unique record ID | `espace_id` | — | — |
| extra | — | `school` | `research_portal_url`, `less_research_intensive` |

All three agree on every §3.5.4 field name exactly — no mismatch here,
unlike the publications file.

## §3.5.4 Journal entity: `journal_name, issn, quality_rank, impact_factor, impact_factor_5yr`

Only `main`'s `journals.csv` (207 journals — appears to already include
UniMelb/UWA journals alongside UQ's, unlike `staff.csv`, which didn't get
the same update) and this branch's `anu_journals.csv` exist as a genuine
separate Journal-entity table. UniMelb, UWA and UNSW's data has no
equivalent — their quality/quartile figures live only as columns flattened
onto each publication row, with no per-journal table backing them.

## §3.5.4 Harvest entity: `source, last_run, latest_year`

`main`'s `harvest.csv` (1 row, UQ only), `zarin-branch`'s
`unsw/output/harvest.csv` (4 rows — staff profile, ABDC, OpenAlex, Scimago,
all UNSW), and this branch's `harvest.csv` (3 rows — ABDC, OpenAlex,
Scimago, all ANU) all exist and share the exact same column names. None
has ever been merged with another — each was generated independently and
sits only on its own branch/location.

## Filename collisions at the repo root

`harvest.csv`, `harvest.json`, `journals.csv`, `journals.json`,
`staff.csv`, `staff.json`, `people.csv`, `people.json`, `publications.json`
all exist, with different content, at the repo root on **both** `main`
and `sprint1-monash-scraper` — the latter is an unmerged branch, so this
isn't visible as a conflict yet, but it will surface the moment that
branch is merged. `anu-scraper` only collides on `harvest.csv`/
`harvest.json` (unprefixed by design — see `docs/DECISIONS.md`); its
other deliverable files are `anu_`-prefixed and don't collide with
anything currently on `main`.

## Where this can't be assessed further

- **UniMelb and UWA's own scraper source** (`alex-branch-test` /
  `alex/unified-publication-csvs`) wasn't read in depth for this table —
  only the merged output on `main`. Their `abdc_match_method`/
  `abdc_match_status` columns suggest their own ABDC-matching logic,
  independent of `rankings/abdc.py`, but confirming that needs reading
  their scraper code directly, not just its output shape.
- **`sprint1-monash-scraper`** wasn't re-read for this table — the 22 Aug
  audit already covered it in detail and nothing suggested it had changed
  since.
- **`saeed/usyd-openalex-pipeline`** appeared during this session and
  hasn't been read at all — not reflected above.
