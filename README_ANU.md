# ANU Accounting & Finance Scraper

Part of CITS3200 Team 20's Researcher Productivity tool. Covers the **ANU** slice
of the data collection (accounting and finance academics).

## What it collects

Academic staff in **accounting and finance** from the two ANU College of Business
& Economics schools that hold them:

- Research School of Accounting (RSA) — `rsa.anu.edu.au`
- Research School of Finance, Actuarial Studies & Statistics (RSFAS) — `rsfas.anu.edu.au` (Finance-area staff only; Statistics/Actuarial are filtered out)

…and their publications, read from each person's public profile page.

## Why profile pages, not the Pure research portal

`researchportalplus.anu.edu.au` (Elsevier Pure) is the cleaner, structured source
but is behind bot-detection that would need active circumvention to get past.
We deliberately don't try to defeat that — it conflicts with the unit's ethics
outcome and the project's own robots.txt commitment. The RSA/RSFAS profile
pages are static, public HTML, so a plain `requests` call works without any
of that.

## Run it

The shared pipeline is the current way to produce `final output/anu/`:

```bash
python run.py --uni anu --ror 019wvm592
```

This is the current, authoritative source for ANU's slice of the data —
`final output/anu/anu_staff.csv`, `anu_publications.csv`, `anu_journals.csv`
and `anu_harvest.csv` (each with a matching `.json`), produced by
`export.py` from the raw scrape plus ORCID/Crossref/OpenAlex retrieval and
ABDC/Scimago/Clarivate enrichment. Actual current column headers, read
directly from the files (updated 22 Sep 2026 — verify again after any
future pipeline change rather than trusting this table blind):

| File | Columns |
|---|---|
| `anu_staff.csv` | `name, job_title, academic_level, university, field_of_research, source_id, orcid, profile_url` |
| `anu_publications.csv` | `name, orcid, source_id, journal_name, title, year, author_count, authors, doi, article_url, link, quality_rank, sjr_quartile, citation_percentile, cited_by_count, fwci, oa_status, oa_url, publication_status, source` |

`quality_rank`/`sjr_quartile` are the authoritative ABDC/Scimago join,
computed once in the shared pipeline — not the self-reported hint the
standalone scraper captures (see below). There is no
`research_portal_url`, `abdc_self_reported` or `coauthors` column in this
file — those are standalone-scraper-only fields (`coauthors` is exported
here as `authors` instead); see the next section for where they still
exist.

## `anu_scraper.py` run standalone

`anu_scraper.py` can still be run on its own for just the staff/publication
scrape — no ORCID/Crossref/OpenAlex retrieval, no ABDC/Scimago/Clarivate
enrichment:

```bash
pip install requests beautifulsoup4
python anu_scraper.py
```

This is a **separate, secondary output path** from the shared pipeline
above — a different directory, a different (older) schema, useful mainly
for debugging the scraper itself in isolation. Writes to `./output/`:

| File | Contents |
|---|---|
| `anu_staff.csv` / `.json` | One row per academic — `name, job_title, academic_level, field_of_research, profile_url, university, research_portal_url, less_research_intensive` |
| `anu_publications.csv` / `.json` | One row per parsed publication — `researcher_name, researcher_profile_url, title, journal_name, year, doi, issn, article_url, abdc_self_reported, coauthors, author_count, author_count_confidence, publication_type, forthcoming, university, field_of_research, source, citation_percentile, raw` |
| `anu_unparsed_publications.csv` | Publications the parser wasn't confident about — reviewed by hand rather than trusted |
| `anu_no_publications.csv` | Academics with no inline Publications section — a known coverage gap, logged not dropped |
| `anu_review_emeritus_no_output.csv` | Emeritus staff with zero output — a review list, not an auto-exclusion |

As of 22 Sep 2026, `./output/` holds none of these files — the copies that
were there were a stale run from an earlier schema (296 publication rows,
against the shared pipeline's current 520) and were deleted as dead weight
rather than left as a trap for anyone who opened them expecting current
data. Running `anu_scraper.py` again writes fresh copies here; it does not
touch `final output/anu/` at all, which only `export.py` (via `run.py`)
writes.

Field names in this standalone output match the **Scope of Work data
dictionary (section 3.5.4)** on purpose, so — if fed through the rest of
the pipeline rather than read on its own — it loads into the shared
database with no reshaping.

## Approach to parsing

The Publications section on a profile page ends only at a heading of the
same or higher level as the "Publications" heading itself — a lower-level
sub-heading (or a short bold label paragraph like "Selected working
papers:") starts a labelled sub-section instead, and a sub-section whose
label matches a media/interview/working-paper/grant/etc. pattern is skipped
entirely (see FIX A in `docs/DECISIONS.md`, 15 Sep 2026). An ORCID published
on the profile page itself (`anu_scraper.extract_orcids`) is also read from
the same page fetch and, once validated (checksum, exact name match against
the public ORCID record), used to fill in a missing seed ORCID — see FIX D.

Publications are written as free-text prose by each academic individually, so
formatting varies a lot — between the two schools and between individuals on
the same school. The parser recognises several distinct citation shapes
(italicised journal name; explicit quoted title; author-list-then-year-then-title;
year-then-author-list-then-title; parenthesised vs. bare co-author clauses,
and combinations of these) and extracts title / journal / year / DOI / article
URL / co-authors / ABDC rating from each.

Where a citation doesn't cleanly match a known shape, the parser does not
guess — it logs the entry to `anu_unparsed_publications.csv` for manual
review instead. A general safety net also catches entries that technically
"parsed" but produced a suspicious result (a title that's really just an
author list, unbalanced parentheses, or a title under 15 characters) and
demotes those to the review file too, rather than asserting a wrong answer
with false confidence.

Article URLs and DOIs are read from the actual `<a href>` links in each
publication paragraph rather than pattern-matched from visible text, since
titles are frequently hyperlinked with no visible URL shown at all.

## Known limitations (for the Risk Register / Skills & Resources Audit)

- Publication text is hand-written per academic, so formatting genuinely
  varies. The parser is heuristic; entries it's unsure about are logged for
  review, not silently trusted.
- Some academics only link out to the (blocked) Pure portal and have no
  inline publication list at all — captured in `anu_no_publications.csv`,
  uncollectable via this route.
- Inline ABDC ratings are **self-reported** by each academic and captured as
  a hint only. The authoritative ABDC join happens later in the pipeline
  against the official list.
- The category filter keeps only staff tagged "Academic" (excludes
  Professional staff, Research student, Visitor/Honorary) — on the view that
  the Scope of Work's FR1 targets "academics" specifically, not students.
  Worth confirming with the team if students should be included.
- The remaining unparsed entries are, on inspection, mostly genuine —
  working papers and conference talks with no journal or year to extract —
  rather than parser failures.

## Politeness / compliance

Sends a normal browser User-Agent, checks each host's robots.txt once before
crawling it, and sleeps briefly between requests. No attempt is made to
access the Pure portal or bypass any access control.
