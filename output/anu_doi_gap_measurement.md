# DOI gap — measured, not closed

`anu_doi_manual_lookup.csv` has 222 rows. This measures how many of them
could plausibly resolve to a real DOI automatically, and how long that
takes — nothing here is written back into `anu_publications.csv` or
`anu_doi_manual_lookup.csv`; those files are unchanged.

## Method

Random sample of 25 (`random.seed(23724721)`, `output/doi_gap_measurement.py`).
For each: query CrossRef's `/works?query.bibliographic=<title>` (title +
year only, no DOI involved), accept a match only if the returned title
shares ≥60% of its words with ours (normalised, case/punctuation-
insensitive) and the year is within 1. Both fields recorded, plus wall-
clock time per call. Full sample with matched titles/DOIs/similarity
scores: `output/doi_gap_measurement_sample.csv`.

**Informit was in scope for this too and wasn't attempted.**
`search.informit.org/robots.txt` disallows `/search` outright — checked
before writing any code against it. Consistent with this project's
standing decision not to defeat bot-blocking anywhere, so the number
below is CrossRef only, not the full two-step method originally asked
for.

## Result

**21 of 25 (84%) resolved to a plausible DOI via CrossRef title+year
alone.** Spot-checked all 21 matches by eye — titles line up closely
(the lowest-confidence accepted match was a 0.67 word-overlap case,
"Corporate Credit Risk Premia (internet appendix)" matching "Corporate
Credit Risk Premia" — a genuine title variant, not a false hit). Median
1.25 seconds per lookup; 32.4 seconds total for the sample of 25.

**What this means for the 222:** if the sample rate holds, roughly 186 of
the 222 could plausibly resolve via an automated CrossRef pass — which is
a measurement of *feasibility*, not something built, since the client's
own 19 Aug instruction treats this as a manual step, not something to
automate into the pipeline. That leaves an estimated ~36 that would
genuinely need a human, via Informit or similar, since CrossRef alone
won't find them.

**What I can't honestly give you: a time estimate for the manual half.**
1.25 seconds is a script calling an API — it says nothing about how long
a person takes searching Informit or Google Scholar by hand for a title
that CrossRef couldn't find. I haven't timed an actual manual lookup, so
"~36 rows, roughly N hours" would be an invented number where the "roughly
186 resolvable automatically" figure is a real, measured one. If a
planning number is needed, that means timing a handful of real manual
lookups first, not extrapolating from the CrossRef pass.
