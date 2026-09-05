# Journal rankings

Looks up journal quality ratings and impact measures, and builds a journal-level
table from a publications CSV.

Written for the UNSW pipeline, but deliberately not tied to it — the journal
column is detected rather than hard-coded, so these run on the ANU, UQ and
Monash exports as they stand. If that turns out to be useful to anyone else,
it's here.

Everything has tests. Please run them before changing anything.

## Run it with `pipeline.py`

```bash
python pipeline.py --publications ../output/unsw_publications.csv \
                   --abdc "../ABDC-JQL-2025-v2-270526.xlsx" \
                   --scimago "../scimagojr 2025.csv"
```

One command. It runs the OpenAlex step, then the journal table, and passes the
intermediate filename between them itself.

The steps do have to run in that order, because `openalex.py` is what creates
the `issn` column and the ranking step matches on it. But order was never
really the problem. The problem is that each step writes a **new file with a
suffix**, and a human has to type the right one into the next command:

```
unsw_publications.csv
  -> unsw_publications_with_openalex.csv     (openalex.py)
    -> journals.csv                          (journals.py)
```

Two chances to point at the wrong file, and on this project both have already
been taken: once by handing the raw file to the ranking step, so the ISSNs were
ignored and the match counts came back identical to the run before OpenAlex
existed, and once by running against a copy of the output folder that was a
week old. Neither failed. Both produced a plausible number.

So the filenames are not typed any more. Name the inputs once and the chain
threads itself.

The individual modules still run on their own, and are documented below,
because you need that to debug one step. If you run `journals.py` directly on a
file with no `issn` column it now says so before it starts, rather than
silently matching on titles alone.

Add `--skip-openalex` to rank on titles without touching the network. The match
rate is worse; it is for working offline, not for the numbers we give the
client.

---

## `abdc.py` — ABDC journal ratings

Takes any of our `*_publications.csv` files and adds the ABDC rating for each
journal.

```bash
python -m pip install -r requirements.txt

python abdc.py --publications ../unsw/output/unsw_publications.csv \
               --abdc "../data/ABDC-JQL-2025-v2-270526.xlsx"
```

Writes two files next to the input:

| File | Contents |
|---|---|
| `<name>_with_abdc.csv` | every original column, plus `quality_rank`, `abdc_for_code`, `abdc_matched_title`, `abdc_match_type`, `abdc_list_year` |
| `<name>_abdc_unmatched.csv` | journals it could not rate, most frequent first |

A journal that exists but is **not on the ABDC list** gets `quality_rank = none`,
as the client asked on 12 August — an unranked outlet is a finding, and a blank
cell would read as "we didn't check". Rows with no journal at all (book chapters,
media) are left blank, because there was nothing to rate.

The unmatched file is a **to-do list, not an error log**. Working down it from the
top is the fastest way to raise coverage, because one common journal can account
for dozens of rows.

### You need to download the list yourself

<https://abdc.edu.au/abdc-journal-quality-list/> → "ABDC Journal Quality List
(xls file)". It is free and needs no login, but the ABDC does not grant
redistribution, so the file is gitignored rather than committed.

**Use the 2025 list.** It is the current edition, from a review of 2,600+ titles
by 43 discipline experts. Some of us had been assuming 2022 — ratings differ
between editions, so we all need to be on the same one. One workbook holds
several editions as separate sheets; the module defaults to the most recent and
records which one it used in `abdc_list_year`, so a rating is always traceable to
its edition.

### It works on anyone's CSV

The journal column is found automatically — `journal_name`, `journal`,
`journal_key`, `source_title` and a few others — so it runs on the UNSW, ANU, UQ
and Monash exports without editing. If your column is named something else, pass
`--journal-column`. ISSN is used when present, since it is unambiguous.

### Why matching is exact by default

A wrong A\* is much worse than a blank. It is invisible, it survives review, and
it distorts every ranking built on top of it.

*Journal of Accounting Research* is A\*. *Journal of Accounting Education* is B.
They differ by one word. Any matcher loose enough to catch a truncated title is
loose enough to swap those two.

So matching is exact — on ISSN first, then on a normalised title (case,
accents, `&` vs `and`, punctuation and a leading "The" are all formatting
differences, not different journals). Anything that does not match is reported
rather than guessed at, and every matched row records **how** it matched in
`abdc_match_type`:

| `abdc_match_type` | Meaning |
|---|---|
| `issn` | matched on ISSN — the strongest |
| `title` | exact match after normalisation |
| `title-variant` | our title carried extra: a trailing `(qualifier)`, a subtitle after `:` or `–`, or a redundant trailing "Journal" |
| `abdc-prefix` | ABDC's title carried a subtitle and ours did not — e.g. we write "Auditing", ABDC lists "Auditing: A Journal of Practice and Theory" |
| `fuzzy` | opt-in only, see below |

`abdc-prefix` is the riskiest of the four, so it is guarded: a prefix is only
usable if it is unambiguous. On the 2025 list, 2,499 candidate prefixes are
discarded and 102 survive. The one that matters most: *Journal of Finance: Case
Studies* would otherwise make "Journal of Finance" resolve to a C, silently
downgrading every paper in an A\* journal. That case has a test.

`--fuzzy` exists for working through the unmatched list. It is conservative,
refuses short titles, never overrides a real match, and always tags its results
so they can be filtered out or checked by hand. **Don't use it for the numbers we
give the client** without reviewing what it matched.

## What coverage to expect

Run against UNSW's 2025 data, on the 1,985 journal articles that carry a journal
name:

| | |
|---|---|
| rated by ABDC | **1,519 (77%)** |
| A\* | 632 |
| A | 632 |
| B | 221 |
| C | 59 |

Read those two ways round, because they answer different questions. Per
*publication row* it is 78%, which is the number that matters for ranking
researchers. Per *distinct journal* it is 346 of 566 (61%), which is lower and
looks worse but is not: the unrated ones are mostly outlets that appear once or
twice, so they weigh almost nothing on the researcher side. A handful of A\*
journals carry hundreds of rows each.

The remaining 22% is mostly **correct**, not missing. The largest unmatched
entries are *Weekly Tax Bulletin* (a practitioner newsletter), *Palgrave Studies
in the History of Economic Thought* (a book series), *Australian Superannuation
Law Bulletin*, *SSRN Electronic Journal* and conference proceedings — none of
which the ABDC ranks. Before treating a low percentage as a bug, read the
unmatched file: an unranked outlet is a real finding about someone's output, not
a gap in the join.

---

## `scimago.py` — SJR quartiles and h-index

The second ranking source the client asked for on 12 August.

```bash
python scimago.py --publications ../unsw/output/unsw_publications.csv \
                  --scimago "../data/scimagojr 2025.csv"
```

Adds `sjr`, `sjr_quartile`, `h_index`, `cites_per_doc_2y`, plus the matched title
and match type. **Those first four names are Sean's**, deliberately — UQ and UNSW
merge without renaming anything.

### Download it from <https://www.scimagojr.com/journalrank.php>

Free, no login. Use the "Download data" link, or
`journalrank.php?out=xls` directly.

**Save it exactly as downloaded — do not open and re-save it in Excel.** Despite
the `xls` in the URL it is a semicolon-separated CSV, and Excel will mangle it.

Three things about that file will silently corrupt your numbers if you read it
with default settings, and this module handles all three:

- **The separator is `;`, not `,`** — and the file is full of commas, so a reader
  that guesses wrong gets one giant column and finds nothing.
- **The decimal separator is `,`** — SJR values look like `104,065`, which a naive
  reader turns into a hundred and four thousand. Nothing looks broken; every
  SJR-based ranking is just wrong.
- **ISSNs are packed several to a field** without hyphens: `"00221082, 15406261"`.
  Both are indexed.

Book series and conference proceedings are excluded by default — a book series
carrying a quartile in a journal-ranking column would mislead. `--include-non-journals`
turns that off.

A journal with no quartile keeps a blank, not `Q4`. Absent is not the same as
worst.

---

## `journals.py` — one row per journal, and where our ISSNs come from

```bash
python journals.py --publications ../unsw/output/unsw_publications.csv \
                   --abdc "../data/ABDC-JQL-2025-v2-270526.xlsx" \
                   --scimago "../data/scimagojr 2025.csv"
```

Writes `journals.csv` next to the input — one row per journal instead of the
same rating repeated on every publication row. This is the **Journal entity from
Scope 3.5.4** (`journal_name, issn, quality_rank, impact_factor,
impact_factor_5yr`), and it matches the shape Sean and Yuhan already produce.
The `impact_factor` columns are empty until someone gets Clarivate access, but
they exist so there is somewhere to put it.

### This is how we get ISSNs

Most university sites don't publish ISSNs — UNSW's certainly doesn't — which is
why the scrapers can't capture one. But ABDC and Scimago both carry them, so
every journal we match gets an ISSN for free, with no extra requests to anyone.

On UNSW: **433 of 566 journals now carry an ISSN (77%)**, covering **84% of
publication rows**. That's the join key for Clarivate JIF later.

Two things moved that number. Running `openalex.py` first supplies an ISSN
straight from the publisher for anything with a DOI, and those are used as the
match key rather than the title. And restricting the dataset to journal articles
removed 497 "journals" that were really newspapers and book publishers, which
were never going to match anything. Of the 566 that remain, only **155 match
neither source**.

### Cross-checking the two sources catches bad matches

Where both sources match but their ISSNs disagree, they have matched *different
journals*. Each looks perfectly reasonable on its own; only the clash reveals it.

The real case: **"Journal of Banking and Finance: Law and Practice"** is an
Australian practitioner journal, ABDC A. Trim its subtitle and you get "Journal
of Banking and Finance" — the top-tier finance journal — which Scimago duly
matched, handing it SJR 1.954, Q1 and an h-index of 225.

So when the ISSNs clash, the **weaker match loses**: an exact title match beats
one that needed a subtitle trimmed. Two of those were dropped on the UNSW data.
Where both matched with equal confidence the conflict is real ambiguity — two
different journals called *Economia* — so both are kept and `issn_conflict`
explains what a human needs to decide.

---

## `openalex.py` — citation percentiles, and where the ISSNs come from

Run this **first**, before the ranking joins.

```bash
python openalex.py --publications ../unsw/output/unsw_publications.csv \
                   --mailto you@student.uwa.edu.au
```

Fills `citation_percentile`, the column Scope 3.5.4 defines and the scrapers
leave empty, plus `cited_by_count`, `fwci` and `citation_top_10_percent`.

The percentile compares a paper against others of the same age and field, so
0.98 means cited more than 98% of comparable work. That is fairer than a raw
count, which always favours older papers.

### It also hands us ISSNs

OpenAlex returns the journal's ISSN with every work:

```json
"primary_location": {"source": {"issn_l": "0810-5391",
                                "issn": ["0810-5391", "1467-629X"]}}
```

Since most university sites publish no ISSN, this is the cheapest way to get
one. Run `openalex.py` first and the enriched file carries a real `issn`
column, which `abdc.py`, `scimago.py` and `journals.py` all pick up
automatically and prefer over matching on the journal title.

An ISSN a scraper captured itself is never overwritten, since that came
straight from the publisher.

### Lookup is by DOI only

No DOI, no lookup. Matching a paper by its title is exactly the sort of
approximate match that ends up putting someone else's citation count against a
researcher's name. Publications without a DOI go to the notfound file so the
gap stays visible.

That gap is real: 881 of UNSW's 1,973 journal articles have no DOI, mostly
pre-2000 papers and Australian tax journals that do not mint them.

### --mailto

OpenAlex asks for a contact address and in exchange puts you in a faster pool.
Without it you get rate limited noticeably sooner. It is optional, and it is
never written into any output file, so nothing personal gets committed.

Responses are cached next to the input, so a second run costs nothing.

---

### The ratings also land on the publication rows

`pipeline.py` finishes by writing `quality_rank`, `sjr`, `sjr_quartile` and
`cites_per_doc_2y` back onto every publication row, in place, adding no new
file. On UNSW that is **1,519 rows with an ABDC grade**, 439 in a journal ABDC
does not rate (`none`), and 15 with no journal at all.

Those three are reported separately on purpose. `none` is the client's wording
for an outlet ABDC has not assessed, which is a finding about the outlet rather
than a grade; counting it as one made the first run claim 1,985 of 2,000 rows
were rated when the real figure was 1,544 at the time.

That is a concession to how everyone else exports. UQ, Monash and Adelaide all
carry `quality_rank` on the publication row; keeping it only in `journals.csv`
is arguably the better reading of 3.5.4, but it means UNSW rows arrive in the
team's merge looking unrated.

They come **from `journals.csv`**, not from a fresh ABDC lookup, and that
distinction matters. `journals.py` is where a match gets cross-checked against
the other source's ISSN, so anything that reached the journal table has already
survived that check. Running `abdc.py` over the publications instead would
reintroduce every match the cross-check threw out.

`abdc_self_reported` is dropped on the way through. It was a placeholder that
nothing ever filled, it is not in the data dictionary, and the team's merge
script maps it onto `quality_rank` — so leaving it in would blank out the
ratings that were just added.

---

## `harvest.py` — how current is this data, and where did it come from

The fourth entity in the data dictionary (3.5.4), and the one most of us did not
have. Sean's UQ export already writes it; this matches his file exactly so the
eight universities concatenate:

| university | source | last_run | latest_year |
|---|---|---|---|
| UNSW Sydney | UNSW staff profile | 2026-08-22T09:41:02+00:00 | 2026 |
| UNSW Sydney | ABDC JQL 2025 | 2026-08-22T09:44:18+00:00 | 2025 |
| UNSW Sydney | OpenAlex | 2026-08-22T09:43:55+00:00 | 2026 |
| UNSW Sydney | Scimago | 2026-08-22T09:44:19+00:00 | 2025 |

Nobody runs this by hand. Each script records its own row when it finishes, so
`last_run` is the moment that source actually ran. Rows are keyed on
(university, source) and upserted, which is the point: re-running `openalex.py`
updates the OpenAlex row and leaves the scraper's alone. If it overwrote the
whole file, `last_run` would only ever tell you when you last ran *anything*.

`python harvest.py --show ../output` prints what is currently recorded.

### This is where the client's 19 August instruction lives

For citation figures the date that counts is **the date we scraped**, not the
date Scimago published theirs. `last_run` is that date. `latest_year` is a
different thing: for a publication source it is the newest publication year we
found, and for a ranking list it is the edition we used.

Scimago's export carries no edition field inside the file and the download page
only offers one year at a time, so the edition is read out of the filename.
Keep the year in the name (`scimagojr 2025.csv`) or that column comes out blank
rather than wrong.

---

## `authors.py` — finding what the university website never listed

```bash
python authors.py --staff ../output/unsw_staff.csv \
                  --publications ../output/unsw_publications_with_openalex.csv \
                  --ror 03r8z3t63 \
                  --mailto you@student.uwa.edu.au
```

`openalex.py` asks "here is a DOI, tell me about it". This asks "here is a
researcher, what have they published". Only the second can find a paper the
university's own page never listed, which is what the client asked for on
19 August and what Yuanji asked for again by email.

Writes `<staff>_with_orcid.csv` and `discovered_publications.csv`. The second
uses the scrapers' own column names, so a row can be appended to a publications
CSV unchanged, with `source` set to `OpenAlex` so its provenance stays visible.

### ORCID gives precision, not recall

Checked against the live API on 25 August, using Jason Zein at UNSW:

```
authors?filter=orcid:0000-0001-7701-3721             -> exactly 1 author
works?filter=authorships.author.orcid:0000-...-3721  -> 47 works, all his
```

So an ORCID is unambiguous, and anyone getting several results is probably
using `search=` rather than `filter=orcid:`. But name search is a different
story:

```
authors?search=Jason Zein  -> 3 results
    Jason Zein     ORCID, 45 works, UNSW Sydney     <- the researcher
    Jason El-Zein  no ORCID, 1 work, Illinois EPA   <- a different person
    Jason Zein     no ORCID, 1 work, UNSW Sydney    <- the SAME person again
```

OpenAlex splits one person across several author records and usually only one
carries the ORCID. Filter on the ORCID alone and you have the right human but
you silently lose whatever sits on the duplicates. So this module keeps both:
the ORCID record is the anchor, the others are treated as duplicates of the
same person rather than as strangers.

**We have no ORCIDs to start from.** No university site in this project
publishes one. So ORCID is an output of the first run, not an input to it; pass
`--orcid-column` on a later run to use what the first one found, which is what
the client meant by "ORCID is permanent for a certain researcher".

### Every row says how it was matched

| `author_match_type` | Meaning |
|---|---|
| `orcid` | we already knew their ORCID — the strongest |
| `name+institution` | one candidate at our ROR, name matched exactly |
| `name-variant+institution` | matched on surname and first initial — weaker, tagged so it can be filtered |
| `ambiguous` | several plausible people; **nothing is included** and the candidates are listed for a human |
| `not found` | OpenAlex has nobody by that name at this institution |
| `lookup failed` | the request did not succeed — **not** the same as nobody being there |

Attributing a stranger's paper to one of our researchers is far worse than
missing one. A missing paper understates somebody; a wrong one is invisible,
survives review, and corrupts every ranking built on it. So `ambiguous`
contributes nothing at all, and `--include-ambiguous` exists only for
investigating and should stay off for anything the client sees.

`--ror` is close to mandatory. Without it, every namesake at every institution
in the world is a candidate, so the module says so loudly and carries on.

An author counts as ours if OpenAlex ties them to our ROR **at any point in
their career**, not just in `last_known_institutions`. That field holds only the
institution on their most recent paper, which is often a co-author's or a former
employer's. Filtering on it alone reported 38 of UNSW's 93 researchers as "not
found", including people with a hundred publications on their own staff page.

### OpenAlex now charges against a daily budget

Discovered the hard way on the first full UNSW run, which died at researcher 92:

```
Rate limit exceeded — Insufficient budget. This request costs $0.001
but you only have $0.0007 remaining. Resets at midnight UTC.
```

One pass over 93 researchers is roughly 150 requests and that is enough to spend
the free daily allowance. Two things follow:

- **Budget exhaustion and throttling both arrive as HTTP 429**, and they need
  opposite responses. A throttle is worth waiting out; a spent budget cannot
  succeed until midnight, so the run stops there rather than marking everyone
  remaining as "not found" for a reason that has nothing to do with them.
- **A failed request is never cached.** Only a genuine empty result is. Caching
  a failure would turn one bad afternoon into a permanent wrong answer about a
  real person, and no amount of re-running would fix it.

`lookup failed` is therefore its own `author_match_type`, distinct from `not
found`: one says OpenAlex has nobody, the other says we never got to ask.
Everything already fetched is cached, so re-running after the reset picks up
where it stopped rather than starting again.

**An aborted run writes nothing at all.** It used to write whatever it had,
which meant a run that died on its very first request replaced a complete
204-publication file with an empty one. Nothing is lost by staying quiet: the
responses are cached, so the next run rebuilds those researchers instantly.
Partial output that overwrites complete output is strictly worse than no
output.

---

## `clarivate.py` — Journal Impact Factor

```bash
setx CLARIVATE_API_KEY "..."        # Windows, then open a new terminal
python clarivate.py ../output/journals.csv \
                    --publications ../output/unsw_publications.csv
```

Fills `impact_factor` and `impact_factor_5yr`, the two columns nothing else can
supply. The key is read from the environment and nowhere else: never an
argument, because arguments end up in shell history, and never a file, because
files get committed.

### Three calls per journal, not one

The obvious design is one search. It does not work, and it fails silently:

```
GET /journals?q=0022-1082                 -> {"id": "J_FINANC", "matches": [...]}
GET /journals/J_FINANC                    -> bibliographic detail, no metrics
GET /journals/J_FINANC/reports/year/2025  -> the figures
```

The search endpoint returns **no metrics at all**, and no `issn` field either —
the ISSN comes back inside `matches[].value[]` wrapped in `<em>` highlight
tags. A first version looked for a key named `issn`, found none, rejected every
record as "a different journal", and reported **0 matched of 437 with 0
failures**. It ran for five minutes and wrote nothing but blanks.

Two more traps in the response:

- The five-year field is `jif5Years`, **plural**. `jif5Year` matches nothing.
- `jif` arrives as a string (`"12.2"`), `jif5Years` as a number.
- `journalCitationReports` lists every year back to 1997 and **not always
  newest first**, so take the maximum. Reading the first entry put a 1997
  impact factor on fourteen journals, presented as current.

### Matching is on ISSN only

Never on the journal title. A title match would silently attach the wrong
journal's impact factor, and that is the one error here that nothing
downstream could detect.

| `clarivate_match_type` | Meaning |
|---|---|
| `issn` | matched on the ISSN we asked for |
| `not-found` | Clarivate has no journal with that ISSN |
| `lookup-failed` | the request did not succeed — **not** the same as not being in JCR |

### Use `--limit 5` and `--probe` first

`--limit 5` does five lookups and stops, so a wrong assumption costs ten
seconds instead of ten minutes. `--probe <ISSN>` prints all three responses
and says which numbers it extracted from them.

On UNSW: **346 of 566 journals**, **1,366 of 1,973 publication rows (69%)**.
Spot-checked against known values — Journal of Finance 12.2, Journal of
Accounting Research 10.4, JFE 8.8, Abacus 1.7.

## `validate_data.py` — check the data, not the code

```bash
python validate_data.py ../output/unsw_publications_with_openalex.csv \
    --staff ../output/unsw_staff.csv --journals ../output/journals.csv
```

Exit code 0 means every check passed, 1 means at least one failure, so it can
gate a push. 27 rules plus 7 coverage floors.

The tests in this folder prove each module does what it was written to do. They
cannot tell you the university changed its markup, that a rebuild blanked two
columns, or that a step was skipped. Those are properties of the *output*.

Three severities: **FAIL** the data is wrong and a merge should not run on it,
**WARN** probably fine but worth a look, **INFO** coverage, reported so a drop
between runs is visible.

The coverage floors are the important part. A broken scraper does not crash. It
runs, writes a file, and the file is thinner. On UNSW this caught the pipeline
step being skipped, which left `quality_rank` empty on every row across three
runs that all reported success.

What it found on real data: 27 duplicate rows, an unresolved HTML entity in a
journal name, 50 rows carrying SSRN's ISSN instead of the journal's, and a
`journals.csv` whose `publication_count` no longer added up.

### It agrees with the scraper about what a duplicate is

Two entries under one DOI are the same article if their normalised titles are
at least 90% alike. Below that, the publisher has issued one DOI for several
items, which Economic Record does for book reviews. Measured: real repeats
score 0.99, two different reviews sharing a DOI score 0.46. If the validator
and the scraper disagreed on this, one would be wrong and neither would say so.

## `screen_discovered.py` — filtering OpenAlex author discovery

```bash
python screen_discovered.py ../output/discovered_publications.csv \
                            "../ABDC-JQL-2025-v2-270526.xlsx"
```

`authors.py` finds publications a university website never listed. Some belong
to a different person with the same name, because OpenAlex merges distinct
researchers into one author record and a ROR filter does not always separate
them. This is the duplication weakness the client raised on 26 August.

The test is discipline. Every researcher here is in Accounting or Finance, so
ABDC's list — which *is* a list of business, economics and law journals — is a
usable proxy. A physics journal is not on it.

A researcher with 5+ discovered papers of which under 20% are in a business
journal is treated as a name collision and none of their rows are kept.
Individually implausible rows (before 1970) are dropped per row, not per
researcher: one researcher had 76% of his work in business journals and a
single paper from 1957, so the right answer was to drop that row and keep the
other 78.

On UNSW: 731 discovered, **260 kept, 471 flagged**, and eight researchers
excluded. The clearest was a "Suk Lee" with 186 papers, 2 of them business, the
rest in the Journal of the Korean Physical Society going back to 1958.

Nothing is deleted. Three files: the kept rows, the flagged rows each carrying
a `review_reason`, and a per-researcher summary for a human to overrule.

## `journal_match.py` — the matching all three use

Not run directly. They all import it, so a journal that ABDC matches is
matched the same way by Scimago. If they each had their own matcher, the same
journal could end up rated by one source and unrated by the other for no reason
other than a stray subtitle.

## Tests

```bash
python -m pytest -v
```

**223 tests, fully offline** — the ABDC workbook and Scimago export they run
against are generated in temp directories, the OpenAlex call is stubbed with a
real response shape, so no downloaded file is needed, no key, and nothing hits
the network.
