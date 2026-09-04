# ANU ORCID / OpenAlex author resolution — change manifest and report

## Corrections applied in this follow-up

A first pass at this file overstated how firm the results were in two places, and left one
regenerated file's row count out of the printed `git status` block below (captured before the file
existed). Fixed here:

1. `output/orcid_resolution.py` and `output/anu_orcid_resolution.csv` no longer put an
   `openalex_author_id`/`orcid` in an `ambiguous` or `not_found` row. Previously the
   highest-scoring candidate was written into those columns even when it wasn't a confirmed match
   — including, for rows where every candidate scored zero, whichever candidate the OpenAlex API
   happened to return first (a stable sort over an all-zero score doesn't pick a "best" candidate,
   it just preserves API order). 16 `ambiguous` rows carried an author ID this way, 12 of those an
   ORCID. Both columns are now blank for every `ambiguous`/`not_found` row, and the full candidate
   list — including whichever one scored highest — is preserved in `candidate_alternatives` so a
   human still has it to work from.
2. The 4 `name_institution_unique` rows keep their `match_method` (a single same-name candidate at
   ANU is still the best lead available) but now carry `needs_human_check = TRUE`, not `FALSE`.
   These researchers have zero scraped titles, so there was nothing to test the candidate against
   — one candidate is not the same thing as a confirmed one.
3. A new `candidate_count_capped` column (TRUE where `candidate_count` hits the search's 25-result
   page limit) flags rows where the real candidate count could be higher than what was recorded.
   Only one researcher hits it: "Ding Ding", 25/25.
4. This file's headline and the Alex Wang example below were corrected — see sections 4 and 5.

## Change manifest

**Files created:**
- `output/orcid_resolution.py` — the resolution script.
- `output/anu_orcid_resolution.csv` — 44 data rows (45 with header), 12 columns (added
  `candidate_count_capped` in this follow-up).
- `output/orcid_resolution_cache.json` — raw OpenAlex API responses, gitignored, regenerated
  freely (added to `.gitignore` alongside the existing `openalex_cache.json` entry). Re-running the
  script after this follow-up's fixes reused this cache — no new OpenAlex requests were needed,
  since candidates and titles were already fetched.
- `output/ANU_ORCID_RESOLUTION.md` — this file.

**Files modified:**
- `output/anu_unmatched_journals.csv` — regenerated from the current `output/anu_journals.csv`
  (it was stale, generated ~85 minutes before the journal table that superseded it). Row count:
  **72 → 28** data rows. Every row was re-derived from `anu_journals.csv`'s `abdc_match_type`
  column (empty = unmatched); `normalized_key` recomputed with the same title-normalisation
  function `rankings/journal_match.py` (on `zarin-branch`) uses elsewhere in the project, run via a
  temporary read-only worktree so nothing from that branch was copied into this one. No change to
  `anu_journals.csv` itself. Untouched in this follow-up.
- `.gitignore` — two lines added: `output/MAIN_INTEGRATION_FINDINGS.md` and
  `output/orcid_resolution_cache.json`. Untouched in this follow-up.
- `output/anu_staff.csv` and `anu_staff.csv` (repo root) — two columns added, `orcid` and
  `openalex_author_id`, populated only for the 23 rows whose `match_method` is `orcid_exact`,
  `title_overlap_unique` or `name_institution_unique`, blank for the other 21. Row count unchanged:
  **44 → 44**. No other column touched. This follow-up's fixes only changed columns on
  `ambiguous`/`not_found` rows, which were never written into `anu_staff.csv`, so re-checking after
  the fix shows **zero rows changed** here — confirmed by comparing every row's `orcid`/
  `openalex_author_id` against the regenerated resolution file. Root and `output/` copies confirmed
  byte-identical after re-running `build_deliverable.py`.
- `build_deliverable.py` — already modified before this task started (widened to also copy
  `anu_journals.csv`, `harvest.csv` and `harvest.json` to the repo root); not touched by either
  session, only re-run (again, in this follow-up) to reconfirm the root copies. `anu_journals.csv`,
  `harvest.csv` and `harvest.json` at the root are written by that re-run but their content is
  identical to what's already checked in, so `git status` shows no change to them.

**Files explicitly not touched:**
- `anu_publications.csv` (root and `output/`) — re-verified this follow-up: still 296 rows,
  byte-for-byte the same (`git diff --stat` against `HEAD` is empty). No publication was added,
  removed, or re-counted.
- `anu_journals.csv`, `harvest.csv` — unchanged content, per above.
- `output/MAIN_INTEGRATION_FINDINGS.md` — confirmed untracked with `git status`, left in place,
  covered by a `.gitignore` line so a future `git add -A` can't sweep it into a PR by accident.

**`git status --porcelain` at the end of this session:**
```
 M .gitignore
 M anu_staff.csv
 M build_deliverable.py
 M output/anu_staff.csv
 M output/anu_unmatched_journals.csv
?? output/ANU_ORCID_RESOLUTION.md
?? output/anu_orcid_resolution.csv
?? output/orcid_resolution.py
```
(`output/orcid_resolution_cache.json` and `output/MAIN_INTEGRATION_FINDINGS.md` exist on disk but
don't appear here because both are gitignored. `output/ANU_ORCID_RESOLUTION.md` — this file — is
now captured correctly; the previous version of this manifest was written before this file existed
on disk, so its `git status` snippet couldn't include it.)

**Nothing was committed, staged, pushed, or otherwise touched in git history.** Everything above
sits in the working tree for review.

**Anything surprising:** searching OpenAlex authors by name, even restricted to people who have
ANU somewhere in their affiliation history, can return a very large and mostly irrelevant
candidate list for a short, common name — "Ding Ding" returned 25 candidates (the search page
limit — `candidate_count_capped = TRUE`, so the real number may be higher), most obviously
unrelated: a plant biologist at UC Riverside, a photonics researcher, several people whose display
name isn't even "Ding Ding". This is OpenAlex's own author-search behaviour, not a bug in this
script, and it's exactly the kind of case `ambiguous` exists for.

**Anything refused:** nothing was extrapolated beyond what was measured. The
scraped-vs-OpenAlex-works comparison in section 6 below is reported only for the 23 rows written
into `anu_staff.csv`, not projected onto the 21 that aren't. No candidate is picked by "most title
matches" — see the Alex Wang example, where the candidate with the most raw matches is very
unlikely to be the right person. An earlier version of this file also claimed no candidate was ever
picked by "first result" tie-breaking; that claim was wrong (see "Corrections applied" above) and
is fixed now, not just reworded — the script no longer does that, for any row.

**Where guessing was tempting, and what happened instead:** several researchers had exactly one
OpenAlex candidate but very weak title evidence — a single candidate with scraped titles available
but fewer than two of them found in that candidate's OpenAlex work list. Being the only candidate
is a real signal but not, on its own, confirmation — a same-name person who was never scraped
correctly, or an OpenAlex profile with sparse coverage, look the same from the outside. These were
left `ambiguous` rather than promoted to a match. Separately, the 4 `name_institution_unique` rows
were initially marked as not needing a human check, on the reasoning that a unique name+institution
match was enough — this follow-up reversed that: zero titles tested means zero confirmation, so
those now need a human too.

---

## 1. What an ORCID is, and why this is different from the DOI lookup

An ORCID is a personal ID number for a researcher — like an ISBN, but for a person instead of a
book — that stays the same across every university and journal they ever publish with. The DOI
lookup this project already has (`rankings/openalex.py`) answers "does this *paper* exist in
OpenAlex, and what does OpenAlex know about it?" one paper at a time. This work answers a
different question first: "which *person* in OpenAlex is this researcher?" — because once that's
known, every paper that person has ever published becomes visible in one place, not just the ones
this project already scraped a DOI for.

## 2. The institution used

OpenAlex was queried live for `Australian National University` (`GET /institutions?search=...`),
not typed in from memory. It returned exactly one result: OpenAlex ID `I118347636`, ROR
`019wvm592`, display name "Australian National University", country code `AU`. That single,
unambiguous ID is the filter every author lookup below used.

## 3. The disambiguation rule

For each of the 44 people in `anu_staff.csv`, OpenAlex's author-search endpoint was queried with
their name and `filter=affiliations.institution.id:I118347636` (25 results per page — the
`candidate_count_capped` column flags the one researcher, "Ding Ding", where the true candidate
count may exceed what was recorded) — this matches anyone who has ANU *anywhere* in their
affiliation history, not just their current one, which is deliberately generous (it still finds
someone who has since moved on, or an emeritus professor). Every candidate returned was kept, with
their OpenAlex author ID, ORCID (if OpenAlex has one on file), total works count, citation count,
and last-known institution(s).

Where a researcher had more than one same-name candidate, the tie-break is title overlap: every
one of that researcher's scraped titles in `anu_publications.csv` (296 titles total) was compared
against every title in each candidate's OpenAlex work list. A title counts as "the same paper" if
the word-overlap between the two (case, punctuation and whitespace stripped first) is **0.6 or
higher on a Jaccard measure** — the intersection of the two titles' word sets divided by their
union. That threshold isn't new: it's the exact cutoff `title_similarity()` in
`output/doi_gap_measurement.py` already uses on this branch for the same kind of fuzzy title
matching (CrossRef, not OpenAlex, but the same problem — "is this the same paper, worded
slightly differently").

A researcher was called `title_overlap_unique` only when exactly one candidate reached **two or
more** matched titles and every other candidate reached zero — this is the only bucket confirmed
by actual publication evidence, and the only one whose `openalex_author_id`/`orcid` came from a
real match rather than a lead. `name_institution_unique` covers a weaker case — only one candidate
exists at all, but the researcher has no scraped titles to test it against (the 11 people with no
publications section) — so its `openalex_author_id` is still written (it's the only candidate, and
worth having on file) but `needs_human_check` is always `TRUE`, because "the only candidate" isn't
the same claim as "the confirmed candidate". Everything else — more than one candidate with any
title evidence at all, or a single candidate whose evidence was too thin to confirm — is
`ambiguous`, with `openalex_author_id` and `orcid` both left blank and every candidate (not just
the runners-up) recorded in `candidate_alternatives`. No researcher landed in `orcid_exact`: that
bucket is for an ORCID already known from an authoritative source before this lookup started, and
this project doesn't have one yet.

## 4. The resolution table

| match_method | researchers | openalex_author_id / orcid written? |
|---|---|---|
| title_overlap_unique | 19 | yes — confirmed by publication evidence |
| ambiguous | 16 | no — blank, all candidates in `candidate_alternatives` |
| not_found | 5 | no — nothing to write |
| name_institution_unique | 4 | yes — single-candidate lead, still `needs_human_check = TRUE` |
| orcid_exact | 0 | — |
| **Total** | **44** | |

**19 confirmed by publication evidence, 4 single-candidate matches pending human verification, 18
ORCIDs on file.** Those two groups together (23 of 44, 52%) are what's written into
`anu_staff.csv`; of those 23, 18 carry an ORCID and the other 5 carry only an OpenAlex author ID
(OpenAlex simply has no ORCID recorded for that author). All 21 `ambiguous`/`not_found` rows, plus
all 4 `name_institution_unique` rows, carry `needs_human_check = TRUE` — 25 of 44 researchers still
need a person to look at them before this identifier layer can be treated as settled for that row.

## 5. Two worked examples

**Clean case — Sarah Adams (`title_overlap_unique`).** OpenAlex returned two candidates named
"Sarah Adams" affiliated with ANU at some point: one whose last-known institution is ANU itself,
one whose last-known institution is the University of Virginia's neurosurgery department. Of
Sarah Adams's 3 scraped titles, 2 matched the ANU candidate's OpenAlex work list and 0 matched the
Virginia candidate's:

- *"Understanding Collective Impact in Australia: A new approach to interorganizational
  collaboration"* — an exact match, word-overlap 1.00.
- *"Integrated Reporting: An Opportunity for the Australian Not-for-Profit Sector?"* matched
  OpenAlex's *"Integrated Reporting: An Opportunity for Australia's Not-for-Profit Sector"*
  (word-overlap 0.67 — "the Australian ... Sector" vs "Australia's ... Sector" is the same paper,
  reworded).

Two matches for one candidate, zero for the other: unique and confident. Her `openalex_author_id`
and `orcid` are written.

**Refused case — Alex Wang (`ambiguous`).** Three candidates came back for this name; all three
have ANU somewhere in their affiliation history, which is why the affiliation filter returned them
in the first place. The one with the *most* raw title matches (3 of Alex Wang's 10 scraped titles,
and 148 works in total) has a long list of *last-known* institutions — Qingdao University, Shandong
University of Technology, Shanghai Jiao Tong University, and a dozen more — with no ANU affiliation
among them: whatever tie to ANU brought them into the candidate pool, it isn't where their career
is now, and the title matches read as generic business-jargon overlap ("management controls",
"performance measurement") rather than the same person. Two *other* candidates are genuinely
last-known at ANU, and each matched some titles too (2 and 1 respectively). Picking "the candidate
with the most matches" — the shortcut this task explicitly rules out — would have attached Alex
Wang's publication record to someone whose career doesn't currently look anything like his. With
three plausible candidates and the evidence split across more than one of them, this row is
`ambiguous`: `openalex_author_id` and `orcid` are both blank, and all three candidate IDs are in
`candidate_alternatives` for a human to check against the profile page directly.

## 6. The coverage asymmetry

For the 23 researchers written into `anu_staff.csv` — 19 confirmed, 4 single-candidate leads, and
only those 23, nothing extrapolated to the other 21 — the scraped publications file holds **209**
titles between them. The OpenAlex author records for those same 23 people list **685** works in
total. That is not a claim that OpenAlex has 685 comparable journal articles this project is
missing — the OpenAlex figure includes every work type an author record carries (conference papers,
datasets, preprints, editorials, and work from before or after their ANU appointment) — but it is
the first direct, person-anchored measurement of how much broader an author's real output is than
what a curated staff-profile page lists, for the people this project could identify at all.

## 7. What this does not do

This is an identifier layer, not a harvest. It does not add a single publication to
`anu_publications.csv` — that file is still exactly 296 rows, unchanged (re-verified after this
follow-up too). It does not touch `quality_rank`, `sjr_quartile`, or any other column already
flowing through `build_deliverable.py`. And it does not close the DOI gap measured separately in
`output/anu_doi_gap_measurement.md` — a missing DOI on an existing scraped row and a missing
*publication* OpenAlex knows about but this project never scraped are two different gaps, and this
work only shines a light on the second one.

## 8. What the next step would be

With 19 researchers confirmed and 4 more on a single-candidate lead, the next step is to pull each
resolved researcher's *full* OpenAlex work list (already fetched once, cached in
`output/orcid_resolution_cache.json`) and decide, paper by paper, which of the ~685 works
represent an eligible Accounting/Finance publication this project should actually add — filtering
out work types and years outside scope, and checking each new title doesn't already exist as a
scraped row before appending it. That review should treat the 19 `title_overlap_unique` rows and
the 4 `name_institution_unique` rows differently: the first group's identity is already backed by
matched titles, the second group's is still just a lead until a human confirms the profile. Done
carefully, that would grow `anu_publications.csv` well beyond its current 296 rows; the 21
`ambiguous`/`not_found` researchers would need a human to pick (or rule out) the right OpenAlex
profile first, since nothing here should be added on a guess.
