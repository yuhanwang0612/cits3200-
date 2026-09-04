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

```bash
pip install requests beautifulsoup4
python anu_scraper.py
```

Writes to `./output/`:

| File | Contents |
|---|---|
| `anu_staff.csv` / `.json` | One row per academic — name, job_title, academic_level (A–E), field_of_research, profile_url, university, research_portal_url |
| `anu_publications.csv` / `.json` | One row per parsed publication — title, journal_name, year, doi, article_url, abdc_self_reported, coauthors, source, citation_percentile (blank, filled later) |
| `anu_unparsed_publications.csv` | Publications the parser wasn't confident about — reviewed by hand rather than trusted |
| `anu_no_publications.csv` | Academics with no inline Publications section — a known coverage gap, logged not dropped |

Field names match the **Scope of Work data dictionary (section 3.5.4)** on purpose,
so this output loads into the shared database with no reshaping.

## Approach to parsing

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
