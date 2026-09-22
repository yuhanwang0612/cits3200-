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

## 18 Sep 2026 — residual cleanup: whole-number columns, seven title repairs, SSRN exclusion, FIX K, FIX L

Three passes the same day, same branch (`jamie-anu-residuals`, off `main` @
`1b9aac1`), each layered on the last, working tree only. Full detail:
`scratch/_anu17/`, `_anu18/`, `_anu19/` (REPORT.md + EXPLAINED.md each).
Headline: ANU publications 574 → 588 (live re-scrape drift, not a fix) →
587 (FIX K, −1) → 565 (FIX L, −22).

### Whole-number export columns (`_whole_numbers()`, `export.py`)

**What:** `author_count`, `year` and `cited_by_count` were exporting to CSV
as `"2.0"`, `"3.0"` instead of `"2"`, `"3"` whenever the column had even one
missing value elsewhere. A new `_whole_numbers(df)`, called inside
`write()` right before `to_csv`, casts any numeric column whose non-null
values are all mathematically whole to pandas' nullable `Int64` type. JSON
output is untouched (it's written from the raw row list before the
DataFrame exists, so it never had this bug).

**Why:** pandas promotes an integer column with any missing value to
`float64` on construction — there's no nullable-integer-by-default option —
so every one of those three columns rendered with a trailing `.0` on every
row the moment a single row anywhere in the file had a gap. `author_count`
is a client-facing count; `"2.0"` reads as a data-quality problem even
though the underlying number was always right.

**Alternative considered:** post-process the CSV text after writing (a
string replace on `r"\.0$"`). Rejected — fragile against any column whose
name or content coincidentally ends the same way, and it doesn't fix the
same bug in-memory before any other code (tests, downstream scripts) reads
the DataFrame.

**What could go wrong:** a column that's genuinely fractional (`sjr`,
`fwci`) must never be coerced — checked explicitly with a test fixture
where one value is whole (`1.0`) and another isn't (`2.5`): the whole check
is "all non-null values are whole", so a single fractional value anywhere
in the column keeps the whole column as `float64`, correctly.

**Result:** `tests/test_export_number_format.py`, 7/7 pass. Confirmed on
the real file: 0 `author_count` values containing a `.` in
`final output/anu/anu_publications.csv` (checked by reading the file with
Python's own `csv` module directly — pandas' `read_csv` re-promotes a
nullable-Int64 column with any gap back to `float64` on *read*, regardless
of how it was written, so checking via pandas gives a misleadingly wrong
answer here).

### Seven title repairs (`base_scrapers/anu.py` FIX I; `export.py` FIX 1)

**What:** four regex-based repair rules for titles mangled by how ANU's own
page text or ORCID's indexed metadata concatenates a title with trailing
citation noise, applied in `base_scrapers/anu.py`'s `_map_publication()`
(`_repair_title()`), plus the same function imported into `export.py` and
applied to any ANU row regardless of source. Fired 7 times in total this
pass: 4 rows with a trailing "...Award" clause glued onto the real title
(Lily Chen), 1 with a trailing "with D. Duffie and Y. Zhu"-style co-author
clause (Antje Berndt), 1 where the real title was recoverable from inside
quote marks in a citation-shaped string (Susanna Ho), and 1 mangled
book-review citation recovered at export time, not at scrape time (Greg
Shailer — see "why a second call site was needed" below).

**Why:** these are systematic shapes, not one-off typos — the same "trailing
clause glued onto a real title" pattern recurs across different staff pages
and different citation styles, so a shared, tested regex is more reliable
than a per-row manual edit and, unlike a manual edit, survives the next
live re-scrape.

**Why a second call site was needed:** the fourth repair (mangled
book-review citation) only ever ran inside `base_scrapers/anu.py`'s own
page-scrape mapping — but the surviving corrupted row (Greg Shailer,
`10.1108/18325911111182330`) has `source == "ORCID"`, meaning it was
retrieved by `info/orcid.py`, carrying whatever text ORCID's own indexed
metadata has for that citation, and `_repair_title()` was never called on
it at all. Root cause was a call-site gap, not a regex failure. Fixed by
importing `_repair_title` into `export.py` (no duplicated regex logic — one
rule set, two call sites) and applying it inside `build_publications()` to
any row belonging to an ANU staff member, scoped via `records` the same
way the SSRN exclusion below is scoped, so it also covers a future
Crossref- or OpenAlex-retrieved copy of the same shape, not just this one
row.

**Alternative considered:** hand-edit the one known corrupted row directly
in the exported CSV. Rejected — the same corruption reappears verbatim on
every re-run, since it lives in ORCID's own metadata, not in this
pipeline's output; a hand-edit doesn't survive a re-export.

**What could go wrong:** an aggressive "strip trailing clause" regex can
eat a real title that happens to end the same way. Each regex is anchored
to a real sentence boundary (`\.\s+`) or an unambiguous structural marker
(quote marks, a name-and-initials pattern) specifically so it can't fire on
an ordinary title — checked directly against Louise Lu's and Kathy Wang's
genuine "...award-winning events" titles (byte-identical before and after)
and against the whole ANU corpus, not just the target rows.

**What was NOT reproduced:** the fourth repair rule (mangled book-review
citation) is implemented and unit-tested against the exact corrupted string
from the original task brief, but by the time of this run Greg Shailer's
live ANU page had already been updated to a clean 10th-edition citation —
a genuine upstream page change since the brief was written, not a bug in
the fix. The rule stays in place for if the same shape recurs.

### Four SSRN working-paper exclusions (`export.py`)

**What:** `_is_anu_unranked_ssrn_preprint()` excludes an ANU row when its
DOI starts with `10.2139/ssrn.` AND it has no journal name AND no ABDC
rank — a working paper that was never actually published anywhere ranked.
4 Greg Shailer rows excluded.

**Why:** an SSRN preprint with no journal and no rating is not a completed,
ranked publication — carrying it in the export alongside real journal
articles overstates his ranked output and understates nothing (it's simply
not a journal article).

**Alternative considered:** exclude every row with an SSRN DOI outright,
regardless of journal/rank. Rejected once checked against the live data:
this dataset also has confirmed cases (Kathy Wang, Chao Gao — see below) of
a row whose DOI happens to be the SSRN preprint copy for a paper that
*is* genuinely published in a real, ranked journal — a blanket SSRN-DOI
exclusion would have wrongly dropped those too.

**What could go wrong, and what did:** the first version of this rule
gated on `x.get("source") == "ANU staff profile"` and matched zero rows —
all four target Greg Shailer rows are actually `source == "ORCID"` (ORCID's
own copy of his page-scraped entries). Re-scoped to check ANU staff
membership via `records` (which carries a real `university` field) instead
of the row's own `source` string, so it's exact regardless of which
retrieval system produced a given row.

**Discrepancy flagged, not forced to match:** the task brief this rule was
built from said 12 other ANU rows carry an SSRN preprint DOI for a paper
published in a real ranked journal. The actual data has exactly **2**
(Kathy Wang, "The opioid crisis, employee health capital, and corporate
information production", *European Accounting Review*, A\*; Chao Gao,
"Investment Performance of Credit Risk Transfer Securities (CRTs)...",
*Journal of Fixed Income*, A) — checked exhaustively, every SSRN-DOI row in
the export is one of these 2 kept or the 4 excluded, no others exist.
Reported per the brief's own framing that this is a client decision, not a
code decision; both kept rows are unchanged.

### FIX K — exact title/year/journal duplicate merge (`export.py`, shared)

**What:** `_is_exact_title_year_journal_dup(a, b)` — true when `name`,
normalised `title`, `year` and `journal_name` are all identical. Checked
first, before the existing fuzzy `is_near_duplicate()`, whose own
different-real-DOI guard is otherwise unchanged. Requires the normalised
title to be at least `_PREFIX_DUP_MIN_TITLE_LEN` (20) characters AND at
least `MIN_PREFIX_WORDS` (3) words.

**Why:** `is_near_duplicate()`'s guard that "two rows each carrying their
own distinct real DOI never merge" is correct in general (it's what keeps
the two genuinely different "Busy directors and firm performance" papers
apart) but wrongly protects the one case where the SAME paper is indexed
twice under two different real DOIs — confirmed on Susanna Ho's "Partial
Least Squares Structural Equation Modeling Approach..." under both
`10.17705/1cais.03823` and `10.17705/1cais.038123` (the second is the first
with an extra digit spliced in; both resolve, at doi.org, to the exact same
page). An identical title AND year AND journal is strong enough evidence to
merge past that guard specifically for this shape.

**Why the minimum length and word count:** without it, the rule also
merged a confirmed real UNSW pair — two separate articles both simply
titled "Discussion" in the same journal and same year. A short, generic
heading repeats legitimately across issues of the same journal; only a
long, specific shared title is real evidence of the same paper. Caught by
`tests/test_export_dedup.py::test_different_dois_are_both_kept` failing
once its fixture's two rows coincidentally shared a placeholder journal
name — a fixture-realism gap, not evidence the rule itself was too broad.

**Alternative considered:** merge on title+year alone, without requiring
the same journal. Rejected — two different papers can genuinely share a
title and year in different journals (e.g. a working paper published twice
in different venues under editorial license, or simple coincidence); the
journal match is what makes this rule safe to apply automatically rather
than only report.

**What could go wrong:** merging a pair where both copies were already
ABDC-ranked mechanically nudges the ranked-percentage figure down by a
rounding-level amount (removing a counted "yes" from both numerator and
denominator of a sub-100% rate always does this) — flagged explicitly
against this task's own "must not fall" condition before applying; user
chose to proceed rather than leave a confirmed duplicate double-counted.
Full precision: `quality_rank` 87.2449% (513/588) → 87.2232% (512/587).

**Result:** 1 pair merged (Susanna Ho's), kept the shorter/canonical DOI
(`10.17705/1cais.03823`) per the task's own instruction for this exact
case. 588 → 587 rows.

### FIX L — prefix-containment duplicate merge (`export.py`, shared)

**What:** `_is_prefix_duplicate(a, b)` — true when: same `name`; the
shorter normalised title is at least 20 characters; the longer normalised
title is a STRICT prefix of the shorter one at a real word boundary (a
trailing space, not a mid-word cut); `journal_name` matches
case-insensitively; `year` matches exactly. `_prefix_dup_winner()` keeps
whichever row has a DOI; if both have one or neither does, the pair is
**not** merged — logged to `SKIPPED_PREFIX_DUPS` and reported instead, on
the reasoning that DOI presence is the only signal available to tell which
copy is the truncated one, and guessing without that signal is worse than
leaving both rows in and flagging them.

**Why:** ORCID sometimes returns a truncated, main-title-only version of a
paper where the ANU staff page (or a different retrieval source) has the
full title including its subtitle — same paper, but the title-similarity
ratio between the two is far below the fuzzy-match threshold (confirmed as
low as ~0.61 on Tracy (Kun) Wang's "Analyst Coverage and Corporate
Innovation" vs the same title plus "...Evidence from Exogenous Changes in
Analyst Coverage" — a short prefix against a much longer string scores low
on a whole-string ratio even though it is an exact textual prefix), and
FIX K needs an identical title, so neither existing rule could see this
shape at all.

**Why the correctness normalises on NFKC (correcting an error in how this
was described elsewhere):** the title-matching relies on
`_normalise_title()`'s existing `unicodedata.normalize("NFKC", ...)` call.
This specific normalisation form is what let Dean Katselas's
"Certiﬁed"/"speciﬁc" (typeset with an "ﬁ" ligature glyph, one Unicode code
point) line up cleanly against the plain "fi" spelling on his other copy —
NFKC's compatibility decomposition maps the ligature to the two ordinary
letters. (Some circulated notes about this fix refer to "NFKD" — the code
uses NFKC; NFKD would additionally decompose accented characters into a
base letter plus a combining mark, which is not what this fix needs or
does. Corrected here for the record.)

**Alternative considered:** a fuzzy-ratio threshold instead of an exact
strict-prefix rule. Rejected — deliberately narrower on purpose, so a short
common opening phrase on two otherwise-different papers can't match (a
20-character floor on the shorter title exists specifically for this), and
so the rule can't be fooled by two titles that are merely similar rather
than one being a literal textual prefix of the other.

**STOP raised and resolved before writing any file:** implementing the
rule exactly as specified and checking it against the real 587-row export
found **22 valid pairs, not 21** — every researcher's count matched the
brief except Dean Katselas, who has 2 genuine pairs, not 1. The second pair
("Independently Certified Industry-specific Disclosures..." / Abacus,
2019) satisfies every clause of the rule exactly the same way the other 21
do; this reads as a genuine oversight in the brief's own prior count, not
over-matching by the implementation. Surfaced to the user with both options
(merge both pairs, or hold the second back with a hardcoded exception) —
user chose to merge both, on the reasoning that a rule-satisfying genuine
duplicate should not be kept in the data just to hit a target number.

**What could go wrong:** same rounding-level ranked-percentage dip as
FIX K, larger here because 22 rows moved instead of 1 — `quality_rank`
87.2232% (512/587) → 86.9027% (491/565); `sjr_quartile` 90.2896% (530/587)
→ 90.0885% (509/565). Not treated as a stop condition — removing confirmed
duplicate rows is the correct outcome, and a coverage percentage moving
because the denominator shrank is not the same thing as underlying journal
ranking coverage getting worse.

**Result:** 22 pairs merged, 587 → 565 rows. 6 further pairs matched on
title/journal but differ by exactly 1 year (the rule requires an exact
match) — left unmerged and reported, exactly as specified. 0 pairs skipped
for lacking a DOI-presence signal. The 4 genuine "Busy directors and firm
performance" rows (Daniliuc ×2, Wee ×2 — different year AND different
journal in each pair) checked explicitly and confirmed untouched.

## 21 Sep 2026 — PR #44 merge reconciliation with main

Full detail: `scratch/_anu20/EXPLAINED.md`. `main` had, independently and
on a different branch, added its own missing-journal export filter and
regenerated ANU's output files on 19 Sep under its own (older, pre-FIX
K/L) rules; this branch (`jamie-anu-residuals`) had the FIX K/L work above.
Merging produced 10 conflicts: `export.py`, one test file, and all eight
ANU output files. Working-tree only — the resolved files were deliberately
left unstaged (`git add` was not run), since staging wasn't asked for and
this task's own hard rule was never to stage anything; anyone continuing
this needs a plain `git add` on the ten resolved files (not `git add -A`)
before it can be committed.

### `export.py` conflict — ordering, not either/or

**What:** kept both sides' logic, in a specific order: title repair runs
first (the dedup key is computed from the title, so repairing after
computing the key would key a row on the mangled form); main's
missing-journal filter runs next (a row with no journal name at all is
dropped before anything else looks at it); the SSRN-preprint exclusion
above runs last.

**Why:** the order changes the result. Running the SSRN filter before the
missing-journal filter, for example, would let a genuinely-missing-journal
row survive if it happened to also look SSRN-shaped.

**Alternative considered:** none seriously — both rules are independently
correct and address different problems (main's: no journal named at all;
this branch's: a working paper with a journal deliberately never named
because it was never published anywhere ranked), so keeping both was never
in question. The only real decision was the order.

**What could go wrong:** none identified — the three filters check
different, non-overlapping conditions, and the ordering rationale is
purely about which check should see a row first, not about correctness of
any individual check.

### Two duplicate-detection edge cases found by conflicting tests

**What:** two small logic gaps, found because three existing tests failed
after the merge rather than by inspection.

1. `_is_prefix_duplicate` now returns `False` immediately when both rows
   carry the identical DOI (`doi_a and doi_a == doi_b`), instead of falling
   through to "can't tell which copy to keep, report instead." Two rows
   with the same DOI are the same paper by definition — there is nothing
   ambiguous to report. The pair is now handed to
   `_same_doi_subtitle_dropped` (inside `is_near_duplicate`), which merges
   it directly. Confirmed real cases: two UNSW papers (one on professional
   scepticism in auditing, one on ambiguity tolerance in accounting), each
   appearing once with a short title and once with the title plus a
   descriptive subtitle, both copies of each sharing one DOI.
2. `_is_exact_title_year_journal_dup` (FIX K) gained the minimum-length/
   word-count guard described above — surfaced by a UNSW "Discussion" pair
   that would otherwise have wrongly merged.

**Why:** both are things the FIX K/L rules got right in general but had
missed a specific case of — the identical-DOI case is not "ambiguous",
it's certain; the generic-heading case needs a length floor the original
rule didn't have.

**Alternative considered:** leave the identical-DOI pair in
`SKIPPED_PREFIX_DUPS` for manual review, since it's technically still "a
pair FIX L's own doi-presence check matched." Rejected — reporting a pair
as ambiguous when it demonstrably is not (same DOI = same paper, full
stop) is misleading noise in that report, and manual review adds nothing a
human could resolve any better than the code already can.

**What could go wrong:** none identified — an identical DOI is the
strongest possible identity signal available in this dataset; there is no
plausible scenario where two rows with the same DOI are genuinely
different papers.

### Which side of each conflicted output file was kept, and why

**What:** publications, journals and harvest (CSV + JSON, six files) were
kept from this branch, not `main`; staff (CSV + JSON, two files) were kept
from `main`, not this branch.

**Why:** `main`'s copy of the publications/journals/harvest files was
regenerated on 19 Sep without this branch's title-repair and prefix-
duplicate rules applied — checked directly: `main`'s publications file has
592 rows against this branch's 565, and 590 of those 592 rows carry a
fractional author count (`"2.0"` not `"2"` — the exact bug `_whole_numbers`
above exists to fix). The bulk of `main`'s extra ~27 rows are the same
paper counted twice (a short title and a longer title-with-subtitle for
the same article) — exactly what FIX L exists to merge. `main`'s staff
file, on the other hand, is built from a maintained override list
(`data/staff_overrides.csv`) with specific, correct job titles (e.g.
Keturah Whitford as "Reader", Bonnie Allan as "Casual Lecturer") that this
branch's copy of the staff file predates and does not have. The
publications and staff tables join by name only, so taking staff from
`main` and everything else from this branch does not mix in any of
`main`'s publications-side problems.

**Alternative considered:** take every file from one side wholesale (either
all-`main` or all-this-branch), for simplicity. Rejected — each side is
better on different tables for a specific, checkable reason (data quality
on publications/journals/harvest; a maintained override list on staff);
picking a single side wholesale would have thrown away a real improvement
on one side or the other.

**What could go wrong / what was accepted as a real loss:** taking this
branch's publications file over `main`'s drops one genuinely new, real
paper that only `main`'s 19 Sep regeneration had picked up — Xiu-Ye
Zhang's "..." in *European Financial Management*
(`10.1111/eufm.70096`), about the effect of major US hurricanes on
management forecasts. Not lost permanently: it reappears automatically the
next time ANU is re-exported, since the underlying source data already has
it. Not manually re-added here, since hand-editing a file that's supposed
to be machine-generated risks being wrong in a way that's hard to catch
later.

**Result:** `final output/anu/anu_publications.csv` — 565 rows (this
branch's version, kept). `anu_staff.csv` — 46 rows (`main`'s version,
kept). `python load.py`: all 8 universities 100% matched, ANU 565/565.
`pytest tests/` (scoped, per the pytest-collision note in the 22 Sep entry
below): 368 collected, 362 passed, 6 failed — the known Windows
cp1252-console failures in `test_merge_publications.py`, unrelated to this
merge.

## 21 Sep 2026 — Wai-Man (Raymond) Liu: 40-row off-field exclusion

Full detail: `scratch/_anu21/EXPLAINED.md`. This resolves the open finding
from `docs/DECISIONS.md`'s own 15 Sep entry (his ORCID record's discipline
share sat inside `screen.py`'s known 0%–60% calibration gap) and from
`scratch/_anu15/raymond_liu_analysis.txt` — this is that team/client call,
made and recorded.

**What:** 40 rows excluded, all genuinely authored by Wai-Man (Raymond)
Liu — an ANU accounting/finance academic who also holds an MChD and
genuinely co-authors clinical medicine and health-policy papers (airway
management, palliative care, psychiatry, surgery, nursing, and similar).
One further row, in health economics with an ABDC rating (A), was
deliberately kept: "Are there longer-term costs of informal care?..."
(`10.1007/s10198-025-01850-y`, *European Journal of Health Economics*) —
health economics is treated as inside scope (it's an economics field, ABDC
rates the journal, and it fits the same research area as his other work).
Applied via the existing `data/publication_exclusions.csv` mechanism — no
new mechanism — with `reason` text saying "off-field", explicitly not
"namesake", for every row.

**Why:** this dataset tracks accounting/finance research output. A genuine
paper on sedation during an endoscopic procedure is outside that scope no
matter how certainly it belongs to this researcher — this is a scope
decision, not a data-quality fix, and the audit trail says so explicitly
so nobody mistakes it for a namesake-collision removal later.

**Evidence:** each of the 40 rows checked against Liu's own ANU staff
profile (`rsfas.anu.edu.au/people/wai-man-raymond-liu`, which states his
MChD and confirms the health-science stream is genuinely his) and against
the author initials on the papers themselves (W-M Liu / Wai-Man Liu,
consistent with his usual authorship pattern) — recorded per-row in
`publication_exclusions.csv`'s `reason` and `evidence_url` fields.

**Alternative considered:** exclude by journal-name keyword alone
("health", "medic-", "clinical", etc.) with no per-row evidence recorded.
Rejected — a keyword screen alone is how the health-economics row above
was *found* as a candidate, not how it was decided; it would have wrongly
excluded a genuinely in-scope paper if the decision stopped at the
keyword. Each row needed its own authorship confirmation, recorded, not
just a pattern match.

**What could go wrong:** the client has not yet confirmed this scope
decision — it is the team's judgement call, recorded and applied, not a
signed-off client instruction. If the client later decides differently
(e.g. that health economics should also be excluded, or that clinical
papers should be kept and merely tagged out-of-scope rather than removed),
every one of the 40 rows is individually reversible from
`publication_exclusions.csv` alone, and the one kept row is individually
identifiable too.

**Discrepancy flagged, not forced to match:** the task brief this
exclusion was built from stated the 40-row set would split 35 rows with a
DOI and 5 without. Measured directly: the real split is 34 with a DOI and
6 without. Work was stopped and the mismatch reported rather than adjusting
the selection rule to force 35/5 — the person who wrote the brief
confirmed afterwards it was an arithmetic slip in the brief itself, not a
problem with the selection logic.

**A related correction, same pass, not part of the 40-row exclusion:**
Lily Chen's row was checked again against her live ANU page after an
earlier note in this project's history recorded her as a probable
namesake. She is a genuine accounting academic who also publishes in
machine learning — both her flagged rows are hers. Nothing of hers was
touched; her earlier "namesake" characterisation should be treated as
incorrect going forward.

**Result:** ANU publications 565 → 525 (40 rows excluded). Wai-Man
(Raymond) Liu rows remaining: 20. ABDC-ranked 491/525 (93.5%). Journals
table 184 → 158 rows (journals with no remaining ANU publication
referencing them were dropped). `python load.py`: all 8 universities 100%
matched, ANU 525/525. No university other than ANU touched.

## 22 Sep 2026 — v22: encoding pin, Third Sector Review, five book chapters, three title repairs, published-correction guard

Full detail: `scratch/_anu22/REPORT.md` and `EXPLAINED.md`. This pass is
committed (`jamie-anu-v22`, commit `7c4b863`) — everything before it in
this log was working-tree-only at the time it was written; this is the
first ANU pass in this log to actually land.

### Encoding pin (`anu_scraper.py`)

**What:** `get()` now sets `r.encoding = "utf-8"` explicitly on every
response before returning it, instead of leaving `resp.text` to rely on
`requests`' own guessed charset.

**Why:** Neil Fargher's row ("...auditorsâ€™ evaluation...") showed the
exact signature of UTF-8 text decoded as cp1252/latin-1 — a curly
apostrophe's three UTF-8 bytes read back as three wrong single-byte
characters. Live-testing every URL this scraper touches shows ANU's server
currently declares `charset=utf-8` correctly, so a fresh scrape would not
reproduce this today — but the code was still trusting a guess it didn't
need to make.

**Alternative considered:** patch only the one known-bad row in the
exported CSV. Rejected — doesn't address the code path that produced it,
so the same corruption class could reappear silently on any future
request where a proxy, edge cache, or transient server response omits or
mis-states the charset header.

**What could go wrong:** none identified — every RSA/RSFAS page this
scraper touches is confirmed UTF-8; pinning it removes a dependency on a
guess without changing what's actually being read.

**Result:** 1 row repaired (Neil Fargher). All eight universities scanned
for the same mojibake class in `title`/`journal_name` — only ANU had any,
now 0 everywhere.

### Third Sector Review (journal-name resolution)

**What:** Sarah Adams's row had `journal_name` set to a UWA repository
website's own name, not a journal. Resolved via OpenAlex (whose own record
carries the original citation string verbatim, naming "Third Sector
Review, vol. 26, no. 1, pp. 108-138") to the real journal, independently
corroborated by the same journal/ISSN already appearing correctly for two
other universities' researchers elsewhere in this project's own data.
`journal_name` updated, ABDC rating "C" applied (exact ABDC-sheet title
match), no Scimago match (the ISSN genuinely isn't in the Scimago file).

**Why:** a repository landing-page name is not a journal name — leaving it
would misrepresent where the paper was actually published and would never
pick up a quality rating it's genuinely entitled to.

**Alternative considered:** none — a DOI that doesn't resolve at Crossref
(this one is an Informit-registered DOI, a different registration agency)
still has exactly one correct answer once looked up properly; there was no
plausible second candidate journal to weigh against this one.

**What could go wrong:** none identified — the resolution is corroborated
by two independent, already-correct rows elsewhere in this project's own
data, not resting on a single source alone.

### Five book chapters excluded (`data/publication_exclusions.csv`)

**What:** four Greg Shailer rows and one Tracy (Kun) Wang row, all book
chapters (encyclopedia entries, a handbook chapter, and a chapter in an
edited volume) wrongly present in a journals-only dataset. Each confirmed
live against Crossref as `type: book-chapter` before exclusion. Excluded
via the same `publication_exclusions.csv` mechanism used throughout this
log — no new mechanism.

**Why:** the client's 12 Aug rule is journals only. A book-chapter DOI with
a blank `quality_rank` is exactly the shape that silently inflates a
researcher's counted output with something the client explicitly asked to
exclude.

**Alternative considered:** leave the Tracy Wang row's `journal_name` as
whatever the true publisher/venue string should be, since Task 2 of that
day's brief was framed as "resolve the journal name." Rejected once
Crossref confirmed it's a book chapter, not a journal article at all —
giving it a correct-looking journal name would still misrepresent it as a
journal article. Routed to this exclusion instead, per that day's own
instruction to do so if this turned out to be the case.

**What could go wrong:** the fifth exclusion (Tracy Wang's) means the row
count landed one lower than the brief's own stated expectation (520, not
521) — reported explicitly rather than left at 521 by keeping a confirmed
book chapter in the data. See `scratch/_anu22/REPORT.md` for the full
reconciliation.

**Result:** ANU publications 525 → 520. Journals table 158 → 154 (four
now-orphaned book/publisher pseudo-journal entries removed, checked first
that no other row still referenced any of them).

### Three title-casing repairs, one refused (Crossref-authoritative titles)

**What:** four Greg Shailer titles stored either entirely lower-case or
entirely upper-case, all with DOIs. Three replaced with the exact title
Crossref has on record for that DOI — no algorithmic title-casing applied
anywhere. The fourth (`10.1142/s0218495894000240`, 1994, *Journal of
Enterprising Culture*) was left unchanged: Crossref's own registered title
for this DOI is itself entirely upper-case — confirmed correct, not a
casing bug.

**Why:** an algorithmic "title case" transform reliably breaks acronyms
and proper nouns (would have mangled "Chinese" or turned a real acronym
into "Firms" — style guesswork); a DOI has exactly one authoritative
answer for what its own title is, so looking it up beats guessing.

**Alternative considered:** apply a title-casing library/heuristic across
every all-caps/all-lowercase title found. Rejected outright by the task's
own instruction, and confirmed why by the fourth row: an automatic
transform would have "corrected" a title that was already correct.

**What could go wrong:** none identified for the three actually changed —
each is a direct, verified copy of Crossref's own field, not a guess.

**Result:** 4 rows matched the all-caps/all-lowercase test across the
whole ANU corpus (not just Greg Shailer's rows — checked directly), 3
fixed, 1 confirmed correct and left alone.

### Published-correction guard (`export.py`, shared — affects every university's next export)

**What:** a new `_is_correction_notice()` check recognises a title ending
in a Web of Science-style `(vol N, pg N[, YYYY])` locator (`vol.`/`pp`
variants allowed, case-insensitive, year optional) and (a) guards both
`_is_prefix_duplicate` and `_is_exact_title_year_journal_dup` so such a
title is never treated as a dropped subtitle, and (b) excludes any row
matching it outright in `build_publications()`, for every university, not
just ANU.

**Why:** a journal's PUBLISHED CORRECTION notice is indexed under the
original article's own title plus that trailing locator — textually a
perfect strict-prefix "subtitle" match, exactly the shape FIX L exists to
merge. But a correction notice is not an independent second publication;
the client's 9 Sep rule already excludes corrigenda/errata outright, so
the correct treatment is exclusion, not a smarter merge. Two confirmed
real cases: Adelaide's Basil Tucker
(`10.1080/00014788.2013.798234`/`.877214`) and UNSW's Fariborz Moshirian
(`10.1016/s0378-4266(02)00467-3`/`(03)00049-9`).

**Alternative considered:** teach `_prefix_dup_winner` to prefer the
non-correction row when merging such a pair, rather than exclude the
correction row outright. Rejected — a merge still leaves the correction
notice's own existence unaccounted for in the export (it just picks a
winner), and the 9 Sep rule is explicit that a correction/erratum should
not be counted as a publication at all, merged or not.

**What could go wrong:** in both of the two confirmed real cases, both
rows already carry their own real DOI, so under the pre-existing
`_prefix_dup_winner` logic neither pair was actually being silently merged
today (both already fell into "no doi-presence signal, skip and report").
The risk this guard closes is the case where only one side of such a pair
carries a DOI — there the old logic would have picked whichever side *has*
a DOI as the winner, which for a correction notice is not necessarily the
original article. Confirmed via two new regression tests in
`tests/test_export_neardup.py` (26 tests total now, all pass, no existing
test's assertion changed).

**What was deliberately not done:** Adelaide's and UNSW's own
already-committed CSVs were not regenerated — that's each team's own
re-export to run on their own schedule, not something to do for them.
Scanned read-only across all eight universities' currently-committed files
for this pattern: adelaide 1, unimelb 1 (a third example beyond the two
named above — Jun Yu, `10.1111/j.1368-423x.2010.00326.x`), unsw 1, ANU 0,
total 3 — matching the brief's own count. These three rows remain
uncorrected in their universities' committed files until each team's next
export.

**Result:** no ANU row count change from this fix specifically (ANU has 0
rows matching the pattern). Fixed in shared code for every university's
next export.

## 24 Sep 2026 — off-field screen becomes a rule; two rows traced through a merge; one title resolved against ANU's own repository

Full detail: `scratch/_anu24/REPORT.md` and `EXPLAINED.md`. Context: `main`
was regenerated by a parallel fresh pipeline run after this branch's 22 Sep
work merged in — ANU went 520 → 532 rows. All 14 newly-returned rows carry
no DOI; 10 are further Wai-Man (Raymond) Liu clinical rows not among the 40
already excluded by name+DOI/title on 21 Sep (that list is a set of
specific rows — it cannot catch a row it has never seen), and 4 are
unrelated genuine Susanna Ho rows (real, ABDC A\*-rated, in scope). Two
rows present in this branch's own 520-row export are absent from the
532-row merge result.

### The off-field screen: a list turned into a rule

**What:** `ANU_OFF_FIELD_JOURNAL_KEYWORDS` (`export.py`) — a visible,
top-level, editable list of 7 keyword stems (`anaesth`, `anesth`,
`palliative`, `rural health`, `nurse practitioner`, `pain medicine`,
`arthroplasty`), checked as a case-insensitive substring of the journal
name only, applied at export to any ANU row via `_is_anu_off_field_journal()`
(scoped through `records`, the same mechanism as the existing SSRN-preprint
exclusion). Excludes exactly the 10 clinical rows the fresh scrape
returned; the original 40-row `publication_exclusions.csv` list is
unchanged and still applies alongside it.

**Why:** the 21 Sep exclusion is a list of specific (name, doi-or-title)
rows — correct for the 40 rows it was built against, but structurally
unable to catch a row it has never seen. A fresh scrape finding more of
Liu's genuinely-authored clinical output was not a hypothetical risk; it
happened, the same week the list was written. A rule that recognises the
*shape* of an off-field row (a clinical journal name) closes this gap for
any future scrape, not just this one.

**Why journal name, not DOI presence or ABDC rank:** checked directly
against Liu's current 30 ANU rows — 2 of his legitimate finance/economics
rows also have no DOI (`"Journal of Money"` / `"Annals of Operations"`,
both real papers with truncated journal names from an unrelated parsing
gap, verified by title). A DOI-presence rule would have wrongly dropped
both. Similarly, several of his clinical rows are unranked, but so are
some of his real finance rows with no ABDC match — rank is not a safe
signal either. Journal name, and only journal name, cleanly separates the
two groups. (The task this rule was built from estimated 3 no-DOI
legitimate finance rows; the real count, measured directly, is 2 — reported
here rather than adjusted to match.)

**Why these specific keywords, and not a broader "clinical" wordlist:**
derived directly from the 10 confirmed clinical journal names in the
current export, not written from a general medical-terminology list. Two
spelling variants were needed for the same specialty — `anaesth` (British:
*Anaesthesia*, *Anaesthesia and Intensive Care*, *Journal of
Anaesthesiology, Clinical Pharmacology* — the stem also covers
"anaesthesiology") and `anesth` (American: *Anesthesia & Analgesia*).
Checked against every other ANU row's journal name: nothing else matches.
Checked explicitly, with a test, that *European Journal of Health
Economics* (Liu's own genuinely in-scope, ABDC A-rated paper) matches
none of the 7 keywords — "Health Economics" does not contain "rural
health" or "pain medicine" as a substring, the two keywords closest to
colliding with it.

**Alternative considered:** exclude any ANU row with no DOI and no ABDC
rank, on the reasoning that a genuinely off-field clinical paper is
unlikely to be ABDC-rated. Rejected outright, and quickly — checked first
and confirmed it would have dropped real accounting/finance work (the two
no-DOI finance rows above), which is a worse failure mode than under-
catching: a visibly-missing rating is reviewable, a silently-dropped real
publication is not.

**What could go wrong:** the keyword list is scoped to ANU only and
derived from one researcher's current clinical output — if a different
ANU researcher (now or in the future) has a legitimate finance/economics
paper in a journal whose name happens to contain one of these 7 stems,
it would be wrongly dropped. Checked against the full current ANU corpus
and found no such collision today; kept visible and editable (a top-level
list with its own comment block, not buried inside a function) specifically
so a future reviewer can extend or narrow it without re-deriving the whole
mechanism.

**Result:** 532 → **522** publications (10 rows). Matches the task's own
expectation exactly. Regression tests added to `tests/test_export_neardup.py`:
a clinical-journal row for an ANU researcher is excluded; the health-economics
row is not; a non-ANU researcher's row in a clinical-sounding journal
survives untouched (confirming the `records`-based scoping).

### Two rows traced through the merge — one recovered, one not

**What happened, traced separately for each:**

1. **Tracy (Kun) Wang, "Corporate Social Responsibility Reporting Reforms
   around the World: Evidence on Firm Value and Externalities"**
   (`10.1086/742862`, *The Journal of Law and Economics*, ABDC A\*, source
   OpenAlex). Checked both committed files directly: present in
   `anu_publications.json` with full enrichment data, absent from
   `anu_publications.csv`. Not an exclusion (`data/publication_exclusions.csv`
   has no matching row) and not a scraper miss — this is a CSV/JSON
   desync, almost certainly introduced when the "Csv fixes" merge
   reconciled this branch's and the parallel fresh-run branch's versions
   of the two files independently rather than regenerating both from one
   source, the way `export.py`'s own `write()` normally guarantees them to
   agree (it writes both from the same in-memory row list). **Recovered**:
   copied the row from the JSON's own data straight into the CSV, in the
   CSV's column order and blank-vs-empty-string convention — not
   hand-authored, reconciling this repo's own already-existing data. This
   also resolves the separate "orphaned `anu_journals.csv` row" question
   below: *The Journal of Law and Economics* was only orphaned because
   the one publication referencing it had been dropped from the CSV.

2. **Chao Gao, "Investment Performance of Credit Risk Transfer Securities
   (CRTs): The Early Evidence"** (*Journal of Fixed Income*; carried an
   SSRN preprint DOI, `10.2139/ssrn.3183505`, in this branch's own 22 Sep
   export — one of the two rows the 18 Sep pass deliberately kept despite
   an SSRN DOI, because the paper is genuinely published in a real, ranked
   journal). Checked both committed files directly: **absent from both**.
   Checked `data/publication_exclusions.csv`: no matching row — not a
   reviewed exclusion. Checked his live ANU profile page directly
   (`rsfas.anu.edu.au/people/chao-gao`): the paper is **still listed
   there today**, word-for-word matching this title and journal. This is
   a genuine loss, not a deliberate removal — but **not re-added by
   hand**. The row's enrichment fields (ABDC rank, Scimago quartile,
   citation percentile, FWCI) aren't recoverable from anything in this
   repo as it stands; copying them from this branch's stale 22 Sep
   snapshot risks shipping values the current pipeline would no longer
   compute the same way (a live re-run may resolve a different, real DOI
   for the same paper rather than the SSRN one, for instance). Recovery
   needs a fresh `run.py --uni anu` pass, which reads and re-enriches the
   real page content, not a manual CSV/JSON edit.

**Why the asymmetry is the correct call, not an inconsistency:** the
difference is not "one row matters more" — it's what evidence is actually
available. Tracy Wang's full, currently-enriched row already exists,
verified, inside this repo's own JSON; restoring it is copying, not
authoring. Chao Gao's does not exist anywhere in this repo in its current
enriched form; only a stale snapshot from a prior pass and a live page
confirming the paper is real, neither of which is the same thing as "the
row the current pipeline would produce." The task's own instruction not
to re-add a row without evidence it belongs is satisfied differently by
design: strong evidence the row is real is not the same as evidence for
what its current field values should be, and only the second is enough to
safely write into a machine-generated file by hand.

### One title resolved against ANU's own institutional repository

**What:** Susanna Ho's row `"THE EFFECTS OF WEB PERSONALIZATION ON
INFLUENCING USERS' SWITCHING DECISIONS TO A NEW WEBSITE"` (no DOI, source
OpenAlex, link to an OpenAlex work ID) — all-caps, and neither Crossref
(no DOI to look up) nor OpenAlex's own record for the same work ID resolve
it, because OpenAlex's title for this work is itself all-caps, apparently
inherited from the original PACIS 2008 conference proceedings' own
title-page typesetting (confirmed: the AISeL page hosting the proceedings
paper itself also stores the title all-caps). Resolved instead against
**ANU's own institutional repository**
(`openresearch-repository.anu.edu.au`), which has this exact paper
recorded in ordinary sentence case: "The effects of web personalization on
influencing users' switching decisions to a new website."

**Why this source and not the conference proceedings' own casing:** a
conference proceedings PDF's title page is commonly typeset in all-caps as
a purely visual convention, not because the authors' real title was
shouted capitals — unlike the confirmed genuinely-all-caps case from 22
Sep (a 1994 journal article whose *own registered Crossref metadata*,
not a proceedings cover page, was all-caps). ANU's own repository record
for a paper by its own staff member is a curated, institution-maintained
record, a stronger signal of the "real" title than a conference PDF's
cover-page typesetting.

**Flagged as a judgement call, not a certainty:** both sources are
independently real and don't agree (AISeL: all-caps; ANU repository:
sentence case) — this is disclosed rather than silently picking one. If
the team later gets access to the paper's own PDF or a Google
Scholar/DOI-bearing record, worth re-checking.

**What could go wrong:** none identified beyond the disclosed ambiguity
above — the applied fix only changes casing, not any other field.

### Verification

- ANU publications: 532 → 522 (off-field rule) → **523** (Tracy Wang row
  restored). ABDC-ranked: 494/532 = 92.9% before this pass's own changes (the
  state this branch inherited) → 495/523 = 94.6% after.
- *European Journal of Health Economics* confirmed present, 1 row, ABDC A
  — asserted directly, and in a regression test.
- `anu_journals.csv`: 163 → 154 (9 now-orphaned clinical journal rows
  removed after the off-field rule ran; *The Journal of Law and Economics*
  confirmed NOT orphaned, correctly kept, once the Tracy Wang row was
  restored).
- Other seven universities: untouched — confirmed via `git status
  --porcelain`, nothing under any other university's `final output/`
  appears.
- `base_scrapers/monash.py` line 27 (`Path` used, never imported) —
  someone else's one-line bug, unrelated to ANU or this pass's own work.
  Fixed locally only, so `pytest` could even collect the test suite;
  explicitly flagged as **not to be committed** — see the change manifest
  in `scratch/_anu24/REPORT.md`. Fixing it also surfaced a second,
  separate pre-existing bug in the same file (`csv` used, never imported,
  `base_scrapers/monash.py:340`) that the first bug had been masking by
  blocking test collection entirely — left unfixed, reported only, since
  neither bug is this pass's to fix.
- `pytest tests/ -q`: baseline on this newer `main`, after only the local
  `Path` fix and before any of this pass's own ANU changes, is 8 failed /
  380 passed — 6 known Windows cp1252 failures in `test_merge_publications.py`
  plus 2 in `test_monash_identity_overrides.py` (the `csv`-import bug
  above), not the 2 in `test_load_identity.py` this task's own brief
  expected (that file no longer fails on this `main` — a Monash identity
  fix landed since). Reported as a baseline discrepancy rather than
  silently assumed; after this pass's own changes, the same 8 fail, plus 3
  new regression tests for the off-field screen pass. No other failures.
- `python load.py`: all eight universities 100% matched, ANU 523/523.
