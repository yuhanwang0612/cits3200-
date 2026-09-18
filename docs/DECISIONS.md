# ANU slice — decisions log

One entry per non-trivial call, in the order I made them. Written so I can
explain any of this cold, with no laptop open.

## Flatten quality_rank/sjr_quartile onto anu_publications.csv, from anu_journals.csv

**What:** `anu_publications.csv` carries `quality_rank` and `sjr_quartile` on
every row, matching `uq_/unimelb_/uwa_publications.csv` on `main`. They are
not computed independently — `build_deliverable.py` reads `anu_journals.csv`
(the Journal-entity table Zarin's `journals.py` builds) and copies the two
columns across, keyed on `journal_name`. `anu_journals.csv` also exists in
its own right, matching the Journal entity in Scope 3.5.4.

**Why:** Two things had to both be true and initially looked like they
couldn't be. Scope 3.5.4 models Journal as its own entity, and Zarin's
package (rankings/) is built that way on purpose — her README explains why
repeating a rating on every publication row risks the same journal
disagreeing with itself across rows. But `main` had already moved: by the
time I checked, all three universities merged so far (UQ, UniMelb, UWA)
flatten these two columns onto every publication row, and a per-researcher
A*/A/B/other count is exactly the kind of thing that's simplest to compute
by reading a publications file directly. Doing both from one source removes
the actual risk (the two views disagreeing) without giving up either.

**Alternative considered:** journal-table-only, no flattening — the
original call, correct until `main`'s current state changed the trade-off.
Rejected once I could see the other two universities' data live: no
flattening would have made ANU the only file that couldn't be read directly
for the client's minimum-product metric.

**What could go wrong:** if the team later decides the flattened columns
shouldn't exist at all (going back to journal-table-only), removing them
from a file people have already started reading is more disruptive than
adding them was. Recommend, if it's needed, that a flattened view for
Excel export gets built once downstream rather than duplicated per
university — raising this at the 26 Aug meeting.

## Dropped my own hand-rolled Scimago matcher

**What:** removed `_normalize_journal_key`, `find_scimago_csvs`,
`load_scimago_rankings`, and the `scimago_sjr`/`scimago_quartile` fields
they filled, along with `anu_unmatched_journals.csv`, from `anu_scraper.py`.

**Why:** it matched on a normalised title only — no ISSN, no cross-check
against ABDC. `rankings/scimago.py` (Zarin's package) does both, and
`journals.py` cross-checks ABDC and Scimago against each other specifically
to catch a case where one source matched a truncated/subtitled variant of
the wrong journal. Running both matchers side by side risked the same
journal getting rated differently depending on which one happened to run,
for no reason worth defending. My version was written before her package
existed; once it did, keeping mine was duplicating a teammate's better
work, which the project's own rule says not to do.

**What could go wrong:** nothing I can see — the old code is gone, not
disabled, so there's no path back to it accidentally running again.

## university / field_of_research / forthcoming columns

**What:** added to `Publication`, populated from the `Researcher` object
already in scope when a citation is parsed.

**Why (university):** two independent reasons converged. A teammate's
script concatenating every university's publications file needs it to
attribute a merged row to ANU at all. Separately — and this is the one I'd
have missed without checking — `rankings/harvest.py`'s `university_in()`
reads this column straight out of the publication rows to decide which
university to record a harvest entry for; with no column, it silently
records nothing for ANU, no error. Verified live: after adding the column,
running the pipeline produced 3 real `harvest.csv` rows for "Australian
National University" (ABDC JQL 2025, OpenAlex, Scimago) — before the fix
this would have been zero rows with no error, since a missing column
returns `None` and the caller just skips writing anything.

**Why (forthcoming):** the client's 19 Aug rule is to count a working
paper as forthcoming only when explicitly labelled, never inferred from a
missing journal or year. Stored as a literal (case-insensitive) match on
"forthcoming"/"in press" in the citation text, so whoever applies the
counting rule downstream doesn't have to re-parse raw text to do it.

## researcher_name vs. name — left open, not resolved unilaterally

**What:** `anu_publications.csv` still uses `researcher_name` (matching
Zarin's UNSW file). Sean's `uq_publications.csv` uses `name`. I did not
rename mine to match his.

**Why not decide it myself:** two of the three files currently on `main`
(UniMelb, UWA — via Alex's PR) also use `name`, matching Sean's, which is
new information since this was first raised. That's now 3-to-1 against my
convention, which changes the trade-off enough that I don't think it's
mine to call. Recommendation if asked: rename mine to `name` to match the
majority, but this needs the team's sign-off since renaming later, after
someone has built against either name, is exactly the kind of thing that's
easy now and annoying later.

## Senior Fellow → academic_level C; less_research_intensive flag

**What:** `LEVEL_LADDER` now maps a bare "Senior Fellow" title to level C
(same as Senior Research Fellow), and a new `less_research_intensive`
boolean on `Researcher` is `True` only for the plain "Senior Fellow" form.

**Why:** client's 19 Aug rule, verbatim: the two titles are the same
academic level, but a Senior Fellow is less research-intensive — carry a
flag rather than flatten the distinction. Before this fix, "Senior Fellow"
alone matched nothing in the level ladder at all (it contains neither
"lecturer" nor "research fellow") and silently got `academic_level = None`
— a real, previously unnoticed gap, not just a missing flag.

**Edge case found on live data:** Pat Barrett's title is "Distinguished
Honorary Professor & Senior Fellow - ANCAAR" — a combined title. His
`academic_level` comes out as E (from "Distinguished", his primary title,
checked first in the ladder) and `less_research_intensive` is
independently True (from the "Senior Fellow" component). The flag doesn't
override or get overridden by the level — that's deliberate, since a real
combined title needs to carry both facts, not have one silently win.

## Emeritus staff with zero output → review file, not auto-excluded

**What:** `anu_review_emeritus_no_output.csv` lists any researcher whose
job title contains "emeritus" and who has zero publications (confident or
unparsed).

**Why:** client's 19 Aug rule: exclude an Emeritus Professor with no
recorded output, but she also said manual judgement may be required.
"No recorded output" here can only mean "nothing this parser found," which
isn't the same claim as "genuinely has none" — a profile-page scrape can
miss things a Google Scholar or ORCID check would catch. Flagging for a
human rather than auto-excluding matches her own framing of the rule.

**Why keyed on the word "emeritus" and not `academic_level == "E"`:** that
level is shared with plain "Professor" and "Distinguished Professor",
neither of which this rule is about.

**Result on live data:** exactly one Emeritus-titled researcher (Neil
Fargher), who has recorded output — the review file is empty, not because
the check never fires, but because the one person it could apply to
already has publications.

## Three journal_name parser bugs — fixed, not just cleaned

Full detail is in the commit message for `668b3bd`; the short version:

1. **Trailing volume/page numbers** glued onto an otherwise-correct
   journal name by the source page's own italics markup (7 distinct
   journals, not the 4 originally spotted). Fixed generally — pops
   trailing numeric-shaped comma segments (volume, page range, or both)
   rather than special-casing the specific strings found.
2. **"Australia"/"USA" as journal_name`** on 3 Alex Wang rows — these are
   conference-paper citations, not journal articles at all. Reclassified
   as `publication_type = "conference_paper"` with `journal_name` blank,
   which is what the client's "journals only" rule actually calls for,
   rather than just clearing the fake journal name and leaving a
   miscategorised journal article behind.
3. **"Moshirian" (a coauthor's surname) as journal_name** on 2 Hua Deng
   rows — a parser bug where a single-initial coauthor name ("F.
   Moshirian") was mis-split into two tokens, and the bare surname passed
   every safety check. Both affected publications are genuinely
   unpublished working papers with no journal at all; after the fix they
   correctly move from falsely-confident (wrong journal name, silently
   trusted) to honestly-unparsed (blank journal, flagged for review) —
   that's the parser's own stated design working as intended, not a
   regression in the confident-publication count.

**Why the confident-publication count reads 297 → 296, not 297 → 295:**
two independent things, not one. Fixing the Moshirian bug (above) moves
exactly 2 rows out of the confident set (297 → 295) — that part is
intentional, it's the bug fix. Separately, Eunice Khoo's live RSA profile
gained a publication that did not exist in the original 18 Aug baseline
scrape at all: "Superstitious CEO and corporate misconduct: Evidence from
China," *Journal of Accounting, Auditing & Finance*, marked Forthcoming.
Confirmed by diffing the two committed snapshots by researcher+title (her
old list had 9 entries, none matching this title) and by fetching her live
profile directly, where it's present now. That's the source data changing
between scrape dates, not a code change, and it adds 1 row back into the
confident set (295 → 296). Net: 297 − 2 + 1 = 296. Both halves verified,
neither accidental.

## anu_journals.csv's issn column verified against Sean's Clarivate lookup format

**What:** checked, no change needed. `anu_journals.csv`'s `issn` column
already carries the exact format `jcr_lookup(issn)` (Sean's Clarivate JCR
notebook, `UQ.ipynb` on `main`) expects — a hyphenated `NNNN-NNNN` string,
e.g. `0810-5391`.

**Why it's already right:** `anu_journals.csv`'s ISSNs come from Zarin's
`journal_match.normalise_issn()`, which always outputs the hyphenated
form regardless of input shape. Sean's own ABDC-matching code in the same
notebook matches eSpace's ISSNs against the ABDC list by direct string
equality with no hyphen stripping, which only works if both sides are
already hyphenated — so his own pipeline already assumes this format, and
mine already produces it. No conversion needed on either side.

**The numbers:** 103 of 129 distinct ANU journals carry a well-formed
ISSN (checked against the standard `\d{4}-\d{3}[\dX]` pattern — no
malformed values found), covering 260 of 296 publications.

## Methodology gap: profile-page source is selected, not complete — cross-university comparison isn't valid yet

**The numbers:** of the 258 ANU publications with an ABDC rating, 240
(93%) are A\* or A (101 A\*, 139 A, 14 B, 4 C). The join itself is clean —
verified 40 ISSN matches, 60 exact-title matches, 1 prefix match, 0 fuzzy,
against `anu_journals.csv` directly — so this isn't a matching artefact.

**Why it isn't a bug:** it's the source. I scrape each academic's RSA/
RSFAS profile page, which lists a self-curated set of publications — by
its own heading, "selected" or "significant" work, not a complete output
list. UQ reads the eSpace institutional repository API, Monash reads
OpenAlex via each researcher's ORCID, and UniMelb reads Minerva — all
three are complete-output sources with no individual curation step.
A profile page a researcher maintains to represent themselves well will
systematically keep the high-impact work and drop the rest, for
completely ordinary, non-malicious reasons (space, relevance, self-
presentation) — that selection pressure pushes in exactly one direction on
both axes at once: fewer publications counted (understated volume) and a
higher proportion of top-tier journals among the ones that remain
(overstated quality ratio).

**What this invalidates:** the client's own stated goal is "to classify
publications... and make comparisons across universities." Right now,
neither an ANU researcher's raw publication count nor their A\*/A
proportion is comparable to a UQ, Monash, or UniMelb researcher's —
not because ANU academics publish less or better, but because the two
sides of the comparison are drawing from structurally different kinds of
source. This applies to any aggregate or cross-university view built on
top of the current data (a university-level productivity ranking, an
average-quality comparison), not to any single ANU researcher's own
figures in isolation, which are accurate as far as the source goes.

**The fix:** move ANU onto a complete-output source, the same way Monash
did — an ORCID → OpenAlex author-level harvest, matching each researcher
to their OpenAlex author record and pulling their full output, not just
what their profile page chose to list. This is exactly the item logged
below as cut from this sprint. It was framed there as a coverage
improvement (raises citation-data reach past the ~25% DOI ceiling); it
is better understood as the fix for this methodology gap — it's what
would make the client's core cross-university comparison actually valid
for ANU, not an optional enhancement on top of an already-valid number.
Ranking it top of the list for the next round of work, not just "queued."

## Cut from this sprint: ORCID → OpenAlex author-level harvest

**What:** not built. `anu_publications.csv` still reaches citation data
only for the ~25% of rows OpenAlex can match by DOI, and — per the entry
above — the underlying publication list itself is a curated selection,
not a complete output list, until this is built.

**Why:** the team's own priority read (from the 22 Aug repo audit) ranked
ANU's missing ABDC rating above this at the time — that's now fixed, this
sprint, which changes the ranking: this is next, not "eventually." Yuhan's
ORCID/OpenAlex logic on `sprint1-monash-scraper` is written specifically
against Monash's Pure-portal profile pages (that's where the ORCID gets
extracted from), so it isn't a drop-in module for ANU's data as it stands.
Building an ANU-specific equivalent from scratch is a multi-day job on its
own. Blocked on a team decision — shared module vs. per-university build —
being raised 26 Aug, not started in the meantime.

## DOI fallback: manual-lookup list, not an Informit scraper

**What:** `output/anu_doi_manual_lookup.csv` lists all 222 publications
with no DOI after the OpenAlex pass (researcher, title, journal, year,
article URL) for a human to check against Informit or similar.

**Why not automate it:** the client's own 19 Aug instruction is "check
alternative sources... where a link can't be automatically located,
manually locate it" — this is explicitly a manual step, not a scraping
target, and Informit's own bot-blocking policy is unverified, which is the
same ethics/robots.txt constraint already applied to ANU's Pure portal.

## anu_journals.csv prefixed; harvest.csv not

**What:** `anu_journals.csv` carries the `anu_` prefix; `harvest.csv` does
not.

**Why:** `harvest.csv`'s own design (Zarin's `harvest.py`) is a single
shared file, upserted per (university, source) — Sean's UQ row and my ANU
rows are meant to coexist in one file, not sit in two separate per-
university ones. `journals.csv`, by contrast, has no such upsert design and
reconciling two universities' journal ratings in one shared file risks
becoming exactly the kind of matching-logic work that's Zarin's territory,
not mine to improvise. Note: nobody has actually exercised combining two
universities' `harvest.csv` rows yet — `main`'s current `harvest.csv` still
only has UQ's row, even though Alex's UniMelb/UWA data merged after it. How
that combination actually happens (manual CSV merge vs. re-running
`harvest.record()` against a shared file) is an open question for whoever
does that merge, not something I've resolved here.

## Two renames staged, neither applied to this branch

**What:** `researcher_name` → `name` (matching uq_/unimelb_/uwa_
publications.csv's 3-to-1 convention) and `harvest.csv` → `anu_harvest.csv`
(avoiding the collision documented above) both exist as commits on a local
scratch branch, `scratch/staged-renames`, built on top of this branch's
tip. Neither is applied here.

**Why staged rather than decided:** both are genuinely the team's call, not
mine — the naming one because three other people's files already exist
under one convention, the harvest one because `anu-scraper` doesn't
currently collide with anything on `main` (only with the unmerged Monash
branch), so renaming it pre-emptively isn't obviously correct either. The
point of staging is that once either question is actually answered, it's
one `git merge`/cherry-pick, not a rebuild.

**What's still open even if the harvest rename is applied:**
`rankings/harvest.py` hardcodes its own output filename (`FILENAME =
"harvest"`), so every future pipeline run would still produce
`output/harvest.csv` and need a manual rename to `anu_harvest.csv` at the
repo root — fixing that means either extending `build_deliverable.py` to
do the rename automatically, or asking Zarin to make the filename
configurable, and that choice hasn't been made.

**The scratch branch is local only** — not pushed to `origin`, so it's
not visible to the team yet and isn't cluttering anyone's `git fetch`.

## ANU adapter for the shared pipeline

**What was built:** `base_scrapers/anu.py`, plugging ANU into the same
`run.py --uni <uni>` interface UQ, UNSW, UniMelb and UWA already use. It
does not scrape anything itself — it imports `anu_scraper.py` (repo root,
unchanged) and calls its `SOURCES`, `scrape_directory()` and
`scrape_profile()`, then maps `Researcher`/`Publication` onto the shared
`core.schema` shape (`blank_pub`, `TYPES`) the way the other four adapters
do. Two small seed files, `data/anu_identity.csv` (23 rows) and
`data/anu_doi_backfill.csv` (123 rows), and `tests/test_anu_adapter.py`
(offline, 25 tests) came with it.

**Why reuse `anu_scraper.py` instead of rewriting the scrape logic in the
adapter:** that file's directory/profile parsing was already written and
hand-checked against ANU's live pages in earlier work — the citation-shape
heuristics alone (see its module docstring and inline comments) represent
weeks of confirmed edge cases. Rewriting it inside `base_scrapers/anu.py`
to match the other adapters' file layout would either duplicate all of
that or silently drop coverage it already has. `run.py` only needs a
module with `UNIVERSITY`, `ROR` and `collect()`; there's no requirement
that the scraping itself live in that same file, so the adapter is a thin
mapping layer in front of the existing, reviewed scraper instead.

**Why not the Pure research portal:** researchportalplus.anu.edu.au sits
behind bot detection the team decided not to try to defeat, on both
robots.txt and ethical grounds. This was true of the original
`anu_scraper.py` and remains true here — the adapter never requests
anything from that host.

**The two seed files, and where they came from:** both were generated by a
one-off script from data already hand-verified earlier, not re-derived or
guessed at import time. `anu_identity.csv` (name, orcid,
openalex_author_id) is every row of the root `anu_staff.csv` that already
carries an ORCID or an OpenAlex author id — 23 of 44 staff. `anu_
doi_backfill.csv` (researcher_name, title, year, doi) is every row of the
root `anu_publications.csv` where `doi_source == "crossref_title_match"`
— 123 of 296 publications, an earlier Crossref title-match pass. The
adapter joins on these by exact string match only (name for identity,
name + normalised title for the DOI backfill); an ambiguous key (more than
one candidate row) is left blank and counted, never guessed.

**The level fallback:** `core.titles.rank()`/`level()` cover the common
academic ladder, but some ANU job titles are administrative
("Director, Research School of Accounting") and carry no rank word at all
— `core.titles` correctly returns `None` for these. `anu_scraper.py`
already works out an `academic_level` for exactly this case, using its own
ladder plus a name-prefix fallback (see its `LEVEL_LADDER` and
`extract_title_prefix`). The adapter uses `core.titles` first and falls
back to that pre-computed value only when `core.titles` has nothing,
recording which path won in a `level_source` field
(`"core.titles"` / `"anu_scraper fallback"` / `None`) rather than silently
picking one.

**Unparsed publications are excluded, not guessed:** `scrape_profile()`
already separates confidently-parsed citations from ones it could not
parse with confidence (`anu_unparsed_publications.csv`, kept for review
outside the pipeline). The adapter only maps the confident list into
`pubs`; the unparsed count is reported, never included.

**A name-cleaning quirk worth knowing:** `anu_scraper.scrape_directory()`
already strips a leading honorific off `Researcher.name` before returning
it (its own `clean_name()`), so by the time the adapter sees a name the
prefix is already gone. `name` and `name_clean` in the adapter's staff
record are therefore the same string; `core.titles.split_prefix` still
runs on it for interface consistency with the other adapters, but on ANU
it will normally find nothing left to strip.

**What was refused:**
- Fetching researchportalplus.anu.edu.au, or attempting to work around its
  bot protection — out of scope per the team's standing decision.
- Attaching an ORCID, OpenAlex author id, or backfilled DOI by a fuzzy or
  "closest" match — every join is exact-string; ambiguous cases are left
  blank and counted.
- Modifying `anu_scraper.py`, `run.py`, `export.py`, `screen.py`, or any
  other shared module or adapter to accommodate ANU-specific behaviour —
  anything that looked like it needed a shared-module change was reported
  instead of patched in place.
- Guessing at, or silently dropping, a low-confidence publication —
  `scrape_profile()`'s own confidence split is respected as-is.

## 15 Sep 2026 — data-quality pass: heading truncation, page ORCIDs, ABDC title fallback, dedup, parsing fixes

Six defects the reviewer verified by hand on 15 Sep, fixed, re-run for ANU
only (`python run.py --uni anu --ror 019wvm592`, no `--refresh`). Full
before/after numbers, the run log, every intermediate check script and its
output are all under `scratch/_anu15/` (gitignored). Headline: 455 -> 610
publication rows, 9 -> 6 staff with zero publications, ORCID coverage 39.1%
-> 71.7%, ABDC coverage 68.6% -> 85.6%, 6 -> 0 duplicate rows.

### FIX A — `anu_scraper.extract_publications_block` heading-level bug

**What was wrong:** the function stopped at the FIRST `h2`/`h3`/`h4` after
the "Publications" heading, regardless of level. On a page where
"Publications" is an `h3` with `h4` sub-headings underneath it (e.g. "Recent
Media Interview" then "Refereed Journal Publications"), the very first `h4`
ended the whole section — throwing away everything under it. Confirmed on
three staff: Tracy (Kun) Wang (~61 entries -> 0), Mark Wilson (~29 -> 0),
Rebecca Tan (~19 -> 0).

**What changed:** the section now ends only at a heading of the SAME OR
HIGHER level as the Publications heading itself. A lower-level heading (or a
short bold paragraph ending in ":", e.g. "Selected working papers:") starts
a labelled sub-section instead, recorded in a new `"section"` key on the
block dict (existing keys unchanged). A sub-section whose label
case-insensitively matches media/interview/newspaper/press/forum/blog/
podcast/working paper/work in progress/under review/presentation/seminar/
grant/award is skipped entirely — its content was never a journal article.

**Result:** Tracy (Kun) Wang 0 -> 50 confident page rows (67 total after
retrieval), Mark Wilson 0 -> 29 (37 total), Rebecca Tan 0 confident + 19
total blocks found -> 14 confident + 5 unparsed (17 total after retrieval) —
matches the reviewer's "~19" estimate.

### FIX B — not-yet-published entries excluded

**What was wrong:** R&R / under-review / submitted / working-paper entries
came through as journal articles (Nhan Le's R&R at Journal of Money, Credit
& Banking; Kun Li's "Working Papers" sub-section).

**What changed:** a block whose text contains "R&R", "revise and resubmit",
"under review", "submitted to" or "working paper" (case-insensitive) is
excluded and counted (`anu_scraper.NOT_YET_PUBLISHED_COUNTS`), never treated
as a journal article. "forthcoming" and "in press" are deliberately NOT on
this list — those are accepted, published papers awaiting pagination.

### FIX C — title/journal/year parsing

**What was wrong, and the fix, one at a time:**
- *"Privatization, Distortions, and Productivity" -> journal "and
  Productivity"*: the comma-flow splitter (`_split_comma_flow_title_journal`)
  always took the last comma segment as the journal. Added a conjunction
  guard (journal starting with and/or/&/but/nor/"as well as" is rejected —
  `JOURNAL_CONJUNCTION_RE`) and a known-journal preference: before falling
  back to the last segment, join the last k=3 then k=2 segments and check
  whether the result normalises to an exact ABDC title (reusing
  `enrichment.abdc.normalise_title`, loaded once via
  `enrichment.abdc.known_titles()`). This specific example turned out to
  also be a FIX A/B case (Kun Li's own "Working Papers" sub-section) and no
  longer reaches the parser at all; the conjunction guard and known-journal
  preference still guard the general class of bug.
- *"Journal of Money, Credit & Banking" -> "Journal of Money" / "Credit and
  Banking"*: same known-journal preference fixes this directly (this
  specific row is also a FIX B case — an R&R — and is now excluded anyway).
- *Sonali Walpola's 2021 paper getting year 1987 from "(1987-2016)" inside
  the title*: the year was previously taken from the first 4-digit year
  found anywhere in the raw text, before the title was even known. Year
  extraction now runs AFTER the title is finalised, finds the title's own
  span in the source text, and skips any year match inside that span. If
  every year found is inside the title, year is left blank and counted —
  never guessed. Verified: her row now gets year 2021.
- *Journal-name junk* ("Accounting and Finance, forthcoming (ABDC: A)",
  "Accounting and Finance 62: 2467-2496. (ABDC: A)"): new
  `anu_scraper._strip_journal_junk`, applied after the existing
  comma-based trailing-numeric-segment stripper, repeatedly strips a
  trailing self-reported ABDC tag, a "forthcoming"/"in press" marker, an
  impact-factor note, a bare volume/page run, or a URL — since these stack.

**What was deliberately not chased further:** 12 ANU rows still trip a STEP
8 heuristic after all of the above (listed in `scratch/_anu15/after.txt`) —
mostly a bare year still missing because no non-title year exists anywhere
in the citation, or a whole citation-as-title case (Susanna Ho's "Ho, S,
Choi, S., and Yang, F. (forthcoming) '...'" — the citation genuinely has no
other title given on the page). Per this pass's own instruction, these are
reported rather than chased with more one-off rules.

### FIX D — page ORCIDs

**What was wrong:** the adapter only read ORCIDs from `data/anu_identity.csv`
(18 of 46 staff had one), even though 16 staff publish their own ORCID
directly on their RSA/RSFAS profile page.

**What changed:** `anu_scraper.extract_orcids(html)` finds every distinct
`orcid.org/dddd-dddd-dddd-dddX` URL anywhere in the raw page HTML (href or
visible text). To avoid a second HTTP request per profile, `scrape_profile`
now stashes what it finds in `anu_scraper.PROFILE_ORCIDS` (keyed by
researcher name) as a side effect of the ONE page fetch it already makes —
`scrape_profile`'s own return shape is unchanged. `base_scrapers/anu.py`
then runs a second pass (`_apply_page_orcid_fallback`, after
`scrape_profile` has run for everyone) that accepts a page candidate only
when ALL of: exactly one distinct ORCID on the page; a valid ISO 7064
mod 11-2 checksum; and the public ORCID record
(`https://pub.orcid.org/v3.0/<id>/person`, via `core.http.cached_get` +
`core.config.ORCID_HEADERS`) has a family name matching the staff member's
surname (accent/hyphen/case-insensitive) and a given name whose first
letter matches the first given name or the bracketed preferred name (e.g.
"Tracy (Kun) Wang" accepts a record starting with T or K). The seed always
wins over a page candidate; a disagreement is reported, not silently
overridden. Every decision (accepted or rejected, with reason) is logged to
`scratch/_anu15/orcid_decisions.csv`.

**Result this run:** 15 accepted, 1 rejected (more than one distinct ORCID
on the page), 0 seed/page conflicts. Staff with a validated ORCID: 18 -> 33
(39.1% -> 71.7%).

**A finding worth the team's attention, not a bug:** Wai-Man (Raymond)
Liu's page ORCID (`0000-0002-2111-9487`) passed every FIX D check — the
public record's own name is genuinely "Wai-Man Liu", an exact match. But
that ORCID's own `/works` list includes a number of health/medical journals
(BMJ Supportive & Palliative Care, Psychiatry Research, Journal of
Telemedicine and Telecare, Canadian Journal of Anesthesia, and others) —
either a real interdisciplinary output list, or contamination inside his
own ORCID record from a common-name collision (ORCID accounts can have
works added via a Crossref search-and-add that isn't always double-checked
by the account holder). `screen.py`'s existing discipline-share guard
(reject if ABDC-rated share of judged rows < 25%) does NOT catch this — his
share came out at 37.5%, inside the gap that module's own comments assumed
was empty ("nothing falls between 0% and 60%"). Not removed — his own page
also lists several non-ABDC-rated, non-business journals in its own right
(e.g. PLOS ONE, BMJ Supportive & Palliative Care — these are on his own
profile page, source "ANU staff profile", not retrieved). Flagged for a
team/client decision on both counts; see `scratch/_anu15/raymond_liu_analysis.txt`.

### FIX E — ABDC journal-title fallback (`enrichment/abdc.py`, shared)

**What was wrong:** ABDC rating joined on ISSN only. A page row with no DOI
has no ISSN, so it came out unranked even when its journal is unambiguously
on the ABDC list — e.g. Raymond Liu's 39 publications, 0 ranked, including
rows in the Journal of Financial Economics.

**What changed:** ISSN match still runs first and wins. Only when there is
no ISSN hit, `enrich()` now also tries an exact match on the journal name
after normalisation (`normalise_title`: NFKC, lowercase, "&" -> " and ",
strip a leading "the ", every run of non-alphanumeric -> one space, trim).
Building the title lookup raises `RuntimeError` if two ABDC titles normalise
to the same key with different ratings (none do, on the current 2025 JQL).
Which method matched is recorded on `x["abdc_match"]` ("issn"/"title"/
`None`) — an internal diagnostic field, deliberately not one of the
exported columns.

**Safety check, run before relying on this at all**
(`scratch/_anu15/abdc_fallback_check.py`): for every row in the four
committed publication files that already had a `quality_rank` (ISSN
matched), compare the title-match rating against the stored one. Result: 0
disagreements across 3,041 rows (all four universities) — safe to apply.
This is a live-data re-check of a claim from the 15 Sep review (reviewer's
number: 2,041 rows, all agreeing); the discrepancy (3,041 vs 2,041) is most
likely the committed data having moved since the review happened, earlier
the same day — re-running the check is exactly why it exists.

**Shared-file impact:** this is `enrichment/abdc.py`, used by every
university. Nothing changes for UNSW/UQ/UWA's own `final output/` files
until someone re-runs their pipeline — but on their currently-committed
data, applying the same title-fallback logic would rank 334 more UNSW rows
and change nothing for UQ or UWA (neither has an unmatched-by-ISSN row the
title fallback would catch, per the same check script). ANU's own number
before this pass's re-run would have been 68.6% -> 89.0% from FIX E alone;
the actual re-run (which also applies FIXES A-D) landed at 85.6% instead,
against a larger and different row set — see the Headline number above.

### FIX F — duplicate rule (`export.py`, shared)

**What was wrong:** `build_publications`'s dedup key was
`(name, title.lower().strip(), year)`. Curly vs straight quotes, or a
differing year, defeated it — a no-DOI page copy of a paper survived next
to the properly-identified ORCID/OpenAlex copy that had a DOI. Six ANU rows
were affected; PR #26 said "no duplicates," which was true for the exact-
string case it tested but not for these.

**What changed:** key is now `(name, normalised title)` — NFKC, lowercase,
every run of non-alphanumeric -> one space. DOI-first sort unchanged. A
later row with the same key is dropped if it has no DOI, or the same DOI
(case-insensitive) as an already-kept row. Two rows with the same key but
genuinely different DOIs are both kept (a namesake collision or a reprint,
not a duplicate).

**Safety check, run before applying**
(`scratch/_anu15/dedup_simulate.py` -> `scratch/_anu15/dedup_simulation.txt`):
simulated on the four committed publication files first. Removals: ANU 6,
UNSW 38, UQ 0, UWA 0 — exactly the expected counts, so applied.

**Shared-file impact:** nothing changes for UQ/UWA (0 removals either way).
UNSW's committed data would lose 38 duplicate rows once someone re-runs
their pipeline; not done here — UNSW is Zarin's slice, out of scope for
this pass.

### What was refused / deliberately not done

- **researchportalplus.anu.edu.au** — never requested, same as every prior
  ANU decision on this repo.
- **Applying FIX E or F's effect to UNSW/UQ/UWA's committed data directly**
  — both fixes are in shared files and take effect automatically the next
  time someone re-runs those universities' pipelines; re-running another
  university's pipeline was explicitly out of scope for this pass.
- **Raymond Liu's non-ABDC-rated / possibly-mismatched rows** — not
  removed. Both the ones on his own page (PLOS ONE, BMJ Supportive &
  Palliative Care) and the ones from his validated-but-possibly-contaminated
  ORCID record are listed for the team/client to decide, not silently kept
  or silently dropped.
- **The remaining 12 STEP-8-flagged ANU rows** — not chased with further
  one-off parsing rules; listed in `scratch/_anu15/after.txt` instead.
- **The `merge_publications.py` test failures** (6, pre-existing before this
  pass, confirmed unrelated to ANU) — `merge_publications.py` is not one of
  the files this pass was scoped to touch; left as-is and reported.

## 15 Sep 2026 (addendum) — FIX E2: backfilling an ISSN onto a title-matched row

**What was wrong:** `enrichment/clarivate.py` (Journal Impact Factor) and
`enrichment/scimago.py` (SJR/quartile) both join on ISSN only. FIX E rates a
title-matched row by its journal name, but that row still has no ISSN — so
it still gets no JIF and no SJR, even though the journal itself is known and
its ISSN is sitting right there in the ABDC sheet.

**What changed (`enrichment/abdc.py`, shared):** the title lookup built in
`_build()` now also stores each title's ISSN and ISSNOnline values (already
hyphenated `NNNN-NNNN` in the source spreadsheet — no reformatting needed).
A new `title_issns(normalised_title)` accessor exposes them. In `enrich()`,
when a row is matched by title (`abdc_match == "title"`) AND `x["issns"]` is
empty, `x["issns"]` is set to that ABDC journal's ISSNs and
`x["issn_source"] = "abdc_title"` is recorded (an internal diagnostic field,
like `abdc_match` — not one of the exported columns). A row that already
carries an ISSN (even one that didn't match anything) is never touched —
the rule only fills a genuine gap, never overwrites or appends.

**Safety check, run before applying**
(`scratch/_anu15/abdc_fallback_check.py`'s new `check_e2()`): for every
journal in the four committed `*_journals.csv` files that already has both
an ISSN and a `quality_rank`, and that also matches an ABDC title, confirm
the journal's own ISSN(s) and the ABDC title's own ISSN(s) overlap. Result:
**640 of 640 overlap, 0 failures** — applied. (The brief quoted 643 rows
from the same-day review; my own run of the identical check got 640 — see
"Discrepancies noticed" in `scratch/_anu15/REPORT.md`, same pattern as the
2,041-vs-3,041 discrepancy noted for FIX E's own check earlier the same
day — most likely the committed data moving between checks, which is
exactly why the check is re-run rather than trusted from memory.)

**Result on ANU** (`python run.py --uni anu --ror 019wvm592`, no
`--refresh`): 182 rows were title-matched; **181 of those 182 gained an
ISSN** they didn't have before (the 1 exception: an ABDC title entry with
no ISSN of its own on the sheet). Clarivate JIF coverage rose from 448 to
613 of 698 journal articles (293 -> 313 ISSNs queried); Scimago coverage
rose from 455 to 621 of 698. On the final exported/deduplicated 610-row
file: `sjr_quartile` fill 74.4% -> 88.5%; `anu_journals.csv` rows with an
ISSN 49.3% -> 74.6%; rows with an `impact_factor` 42.8% -> 63.2%.
`quality_rank` fill is unchanged at 85.6% — E2 only ever adds an ISSN, it
never changes a rating. No 401/403/429 from Clarivate; the API key itself
is never printed or logged anywhere (see `scratch/_anu15/run_output_e2.txt`
and `core.config.jcr_headers()`, which reads it from the environment only
at call time).

**How the before/after-E2 numbers for sjr_quartile / journal ISSN% /
impact_factor% were actually obtained:** these three metrics didn't exist
in the STEP 8 `after.txt` comparison (measure.py only covers
doi/year/authors/quality_rank fill). Rather than estimate what they would
have been, the ISSN-backfill block in `enrichment/abdc.py` was temporarily
commented out, the pipeline re-run once (69s — almost everything was
already cached from the same day's earlier runs) to get the genuine
before-E2 numbers, then the real code was restored and the pipeline
re-run once more, landing on identical numbers to the original E2 run
(confirming no drift). Full detail: `scratch/_anu15/REPORT.md`.

**Tests added** (`tests/test_abdc_title_fallback.py`, +3): a title-matched
row with no ISSNs gets the ABDC ISSNs; a row that already has ISSNs is left
untouched; an ISSN-matched row's `issns` field is left untouched.
`python -m pytest -q`: 207 passed (the same 6 pre-existing, unrelated
`test_merge_publications.py` failures as before).

**Shared-file impact:** same file as FIX E, so the same caveat applies —
nothing changes for UNSW/UQ/UWA's own `final output/` until someone
re-runs their pipeline. `PR_DESCRIPTION.md` states, as given: applying FIX
E on a UNSW re-run would title-match 334 rows (I confirmed this figure
independently — see below), and FIX E2 would then backfill an ISSN onto
those with none, of which 197 would newly gain an SJR quartile from
Scimago.

I independently re-derived the 334 figure directly from
`final output/unsw/unsw_publications.csv`: 334 currently-unranked rows
normalise to a known ABDC title — exact match. For the 197 figure I could
only reconstruct an approximation, not confirm it exactly: the exported
`unsw_publications.csv` doesn't carry the underlying `issns` list (only
`doi`), so "does this row already have an ISSN" has to be proxied by
"does it have a DOI" — an imperfect proxy, since a DOI doesn't guarantee
Crossref/OpenAlex actually filled an ISSN for it. Using that proxy (no
DOI AND no `sjr_quartile` among the 334) and cross-referencing each
journal's ABDC ISSNs against Scimago's own ISSN list the same way
`enrichment/scimago.py` matches, I get **187**, not 197 — close, and the
gap is consistent with a handful of the 15 title-matched-with-a-DOI rows
genuinely having no ISSN either (Crossref/OpenAlex doesn't always resolve
one even with a DOI) and so also gaining one from E2. Both the 334 and
197/187 figures require an actual UNSW re-run to confirm exactly — neither
this pass nor the review that produced 197 had the underlying per-row
`issns` field to check against directly.

**What was refused / deliberately not done (E2-specific):** nothing beyond
what FIX E already refused — this is a narrow, additive extension of the
same fallback, gated by the same safety-check-before-applying discipline.

## 15 Sep 2026 (addendum) — FIX G/H: near-duplicate rows and citations parsed as a title

Follow-up request on the same branch, working tree, same day. Two more
defects the reviewer found by hand: ~28 near-duplicate ANU row pairs
(same paper, a slightly reworded title from a different source) surviving
the exact-match dedup, and 5 ANU rows whose "title" was really an author
list (two of them straight-up textbook citations, three of them a real
paper with the author list glued onto the front of the title — one of
which also carried a visibly wrong year, 1942).

### FIX G — near-duplicate merge (`export.py`, shared)

**What was wrong:** the exact-match dedup rule (15 Sep, earlier entry)
only catches an identical normalised title. A page-scraped copy and its
ORCID/Crossref/OpenAlex copy of the same paper often differ by a word —
"...value of cash holdings" vs "...value of cash holding", "Earnings
Management in Australian Corporations" vs "...: A Review" — or the page
copy carries an SSRN preprint DOI while the retrieved copy carries the
real, published DOI for literally the same paper. Both look like two
different, unrelated publications to the exact rule, so both survive.

**What changed:** `export.build_publications` now calls
`merge_near_duplicates()` on the output of the exact-match pass. Two rows
are a near-duplicate when: same researcher name; `difflib.SequenceMatcher`
ratio ≥ 0.85 on the two normalised titles; years equal, differ by at most
1, or either is blank; and NOT (each row carries its own distinct real
DOI — see below). Of a merged pair, the surviving row is: the one with a
real (non-SSRN) DOI, if only one has one; otherwise the one from a
retrieval source (ORCID/Crossref/OpenAlex) over a university page source;
otherwise the first-encountered row.

- **SSRN preprint DOIs don't count as identifying a row for this purpose**
  (`_dedup_doi`): a DOI starting with `10.2139/ssrn.` is treated as no DOI
  for the near-duplicate comparison only — the exported `doi` value itself
  is never touched. This is what lets Neil Fargher's and Marvin Wee's
  "The impact of Ball and Brown (1968)..." (page copy: SSRN DOI; ORCID
  copy: `10.1016/j.pacfin.2019.01.006`) and Louise Lu's "The Opioid
  Crisis..." merge correctly, keeping the real DOI.
- **Two rows that each carry their own distinct real DOI are never
  merged**, whatever their titles look like — checked explicitly against
  "Busy directors and firm performance" (2020, Pacific-Basin Finance
  Journal, `10.1016/j.pacfin.2020.101434`) vs (2021, Accounting and
  Finance, `10.1111/acfi.12631`): both DOIs are real and different, both
  rows survive, confirmed via a dedicated test.

**Safety check before applying**
(`scratch/_anu16/neardup_simulate.py` → `scratch/_anu16/
neardup_simulation.txt`): simulated on all four committed publication
files. First pass (ratio 0.85, DOI-only guard): ANU 32 pairs, UNSW 67, UQ
0, UWA 1. Manual review of every pair found 4 clear false positives — two
genuinely different papers, not a duplicate:
1. Dale Boccabella (UNSW): "...Burton has a case - Part 1/2/3" — three
   distinct published notes sharing a long lead-in sentence, ratio ~0.99.
2. Same author: "High Court...suggested considerations - Part 1/2".
3. Gordon Mackenzie (UNSW): "So, you want to get into the SMSF market?...
   Part 2" (2016) vs the same sentence with no "Part 2" (2015) — confirmed
   as two different notes via distinct LexisNexis document keys in the
   `link` field.
4. Same author: "Dealing with goodwill...roll-overs and exemptions: part
   I" vs "...part II" — same series shape, roman numeral.

A bare threshold increase (e.g. to 0.90, as floated as an example in the
brief) would NOT have caught these — all four score above 0.99 similarity,
the difference being just the part marker — while it WOULD have broken 7
of ANU's own 32 genuine matches (the lowest genuine-match ratio in ANU's
own data is 0.8589). Instead, two targeted guards were added to
`is_near_duplicate()`:
- a "Part N" marker (digit or roman numeral) differing between the two
  titles blocks the merge outright, regardless of ratio (`_PART_MARKER_RE`,
  `_differing_part_marker`);
- when NEITHER row has a DOI at all (not even SSRN), a differing
  non-empty `link` value also blocks the merge — this is what catches the
  SMSF Part 2 case, whose two rows have no DOI but genuinely distinct
  source URLs. (Deliberately scoped to the no-DOI case only: a `link` is
  usually doi-derived, e.g. `https://doi.org/<doi>`, so checking it
  unconditionally would have wrongly re-split the SSRN-vs-real-DOI pairs
  the DOI guard above exists to merge — confirmed by a dedicated test.)

Re-run after tightening: **ANU 32 (unchanged — all 32 were already
genuine), UNSW 60, UQ 0, UWA 0**. Isabel Wang's two different papers
("...likelihood of financial misstatements" 2015 vs "...fraud risk
assessments" 2017, ratio 0.786) were checked explicitly and confirmed
never in the merge list, at any point in this process.

**Residual, reported not resolved:** 3 UNSW pairs remain ambiguous rather
than clearly wrong ("Hidden tax Advantages..." vs "Managing the Tax
Advantages..."; "Effect of the debt/equity rules..." vs "Impact of the
new debt/equity rules..."; "Taxing Retirement funding of the self
employed" vs "...of the employed") — none carries a Part-N marker or two
distinct real links, and I can't confirm from title text alone whether
each pair is one practitioner note re-titled or two different ones. None
of these three are in ANU's own data — every one of ANU's 32 simulated
pairs was manually confirmed as a genuine duplicate. UQ (0) and UWA (0)
are both well under the 3-row threshold, so the decision was: apply FIX G
(tightened) to ANU; do not touch UNSW/UQ/UWA's files (per the hard rule —
the simulation only reports); flag the residual UNSW ambiguity here rather
than silently resolve or silently ignore it.

**Result on ANU:** 32 rows removed this run (matching the simulation
exactly). Publication count 610 → (574 after FIX G+H together — see
below).

### FIX H — author list parsed as the title (`anu_scraper.py`)

**What was wrong:** on two related citation shapes, the author list ended
up AS the title:
- Rebecca Tan's two textbook citations — the existing "before the
  italicised journal name" splitter has no concept of a book citation
  shape ("Authors (Year). *Book Title*, Nth Edition, Publisher, City
  (ISBN...)."); once the parenthesised year is consumed as a boundary, the
  leftover author list becomes "the title", and the *italicised* run (the
  BOOK's own title, not a journal) gets treated as a journal — so the row
  passes every existing check and ships as a fake journal article.
- Tracy (Kun) Wang's "Authors YEAR real-title. *Journal*, vol(issue),
  pages." shape (no comma before the year, no "with" clause) isn't one of
  the shapes the existing before/after-italics splitter recognises either,
  so the whole "Authors YEAR real-title" run becomes the title. One
  instance of this ("Wang, K.T., & Wu, Y.** 2024 Corporate social
  responsibility reporting and investment...") also picked up **1942** as
  its year — not from inside the title (FIX C's guard doesn't apply, this
  IS outside the title span) but from the page-range end, "51 (7-8),
  1893-**1942**.", elsewhere in the same citation.

**What changed:** two new module-level regexes in `anu_scraper.py`,
checked right after `title`/`journal`/`coauthors` are tidied, BEFORE the
FIX C year search runs:
- `AUTHOR_LIST_YEAR_PREFIX_RE` — matches a title starting with one or
  more "Surname, Initials" (or "Surname Initials" with no comma — seen on
  a live RSFAS page, "Quan Y." not "Quan, Y.") tokens, optional trailing
  asterisk footnote markers (`Wu, Y.**`), joined by `,`/`&`/`and`, followed
  by a bare 4-digit year, followed by the real title. When it matches: the
  year is taken from THIS match (not the general text search — this is
  what fixes the 1942 bug, since the real year, 2024, is now read
  confidently off the author-list prefix and the page-range number is
  never consulted at all), a leading `(...)` clause left on the front of
  the extracted title (e.g. "(First online 2 January 2021)," — citation
  metadata, not the title) is stripped, and the result re-tidied.
- `FULL_AUTHOR_LIST_RE` — matches a title that is NOTHING BUT an author
  list (optionally ending "et al."/"et. al", both cases) — checked only
  when the year-prefix pattern above didn't match (no year present at
  all). This is the textbook/book shape.
- **Do not guess**: if the year-prefix pattern matches but what's left
  isn't a sensible title (empty, under 15 characters, or doesn't start
  with a capital letter — e.g. "nonfinancial corporate social
  responsibility reporting..." on Tracy Wang's page, written in lower case
  prose style by the page itself), the row is marked unparsed rather than
  guessed at (no auto-capitalisation). Same for the full-author-list case.
- **Year sanity check**: after all year logic (FIX C's and FIX H's), a
  year outside `1950..current_year+1` is discarded and counted rather than
  trusted — a defence against any future stray-number-mistaken-for-a-year
  case this pass didn't specifically find.

**Result, checked directly against the real live pages** (all 5 rows,
full end-to-end `parse_publication` calls, not just regex checks):
- Rebecca Tan's two textbook citations: now `confident=False`,
  `title=None` — excluded from the pipeline entirely (previously shipped
  as fake journal articles with journal = the book's own italicised
  title).
- Tracy Wang's "Tsang, A....nonfinancial corporate social responsibility
  reporting and firm value..." row: now `confident=False` (title would
  start lowercase) — excluded, not guessed at.
- Tracy Wang's "Wang, K.T., & Wu, Y.**...Corporate social responsibility
  reporting and investment..." row: **year fixed, 1942 → 2024**; title
  correctly reads "Corporate social responsibility reporting and
  investment: Evidence from mergers and acquisitions".
- Tracy Wang's "Li, S....Academy fellow independent directors and
  innovation" row: year correctly reads 2022 (not blank, as it was
  before); title correctly reads "Academy fellow independent directors
  and innovation" (the "(First online 2 January 2021)," clause stripped).

### Combined result of FIX G + FIX H, this run

`python run.py --uni anu --ror 019wvm592` (no `--refresh`), 104s, exit 0.
No 403/429/Cloudflare, no exceptions — full log:
`scratch/_anu16/run_output.txt`. `clarivate: 613 of 694 journal articles
have a 2025 JIF (313 ISSNs queried)` — no errors, nothing to flag at the
top of the report.

Publications: 610 → 574 (−36: 32 from FIX G's near-duplicate merge, 3 from
FIX H marking a row unparsed rather than shipping a wrong title, ~1 from
ordinary run-to-run variance in the live Crossref/OpenAlex calls between
this run and the previous one — the same kind of ±1 drift already noted
for Louise Lu earlier the same day). 12 researchers had a changed row
count, all explained by FIX G/H; full detail:
`scratch/_anu16/per_researcher_changes.txt`. Remaining near-duplicate
pairs in the final output (same finder as the simulation): **0**.
Remaining STEP-8-style flagged rows: 12 → 10 (the 2 Tracy Wang
author-list-as-title rows that used to trip "title is a whole quoted
citation"-adjacent heuristics are simply gone now, not fixed-in-place).
`python load.py`: all four universities still **100.0% matched**.

**What was refused / deliberately not done (G/H-specific):**
- The 3 residual ambiguous UNSW near-duplicate candidates (see above) —
  not resolved, not applied to UNSW's files, reported.
- Patching the existing (separately buggy) `AUTHOR_LIST_PATTERN_RE`
  safety net, which turned out not to actually match a comma-separated
  author list the way its own comment implies (its trailing `\s*` can't
  cross a `,` between tokens, so it silently never fires on the exact
  "Surname, Initial., Surname, Initial., ..." shape it looks like it was
  written for) — out of scope for FIX H specifically; FIX H's own new
  regexes don't share this bug, and the old regex is left exactly as
  found rather than patched as a drive-by fix.
- Re-running UNSW/UQ/UWA, or applying FIX G/H's shared-file half
  (`export.py`'s `merge_near_duplicates`) to their committed data — out of
  scope this pass.
