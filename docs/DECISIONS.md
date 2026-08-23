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
