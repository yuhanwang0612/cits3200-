# Test plan

CITS3200 Group 20. Owner: Zarin. Last updated 29 September 2026.

This says what we test, how, and who signs it off, and it maps onto the four
tests in `Project_Acceptance_Tests.docx`.

## How to run everything

```
python -m pytest tests -q                    # unit and integration tests
python -m pytest tests/test_exported_data.py -q     # the exported CSVs, per university
python load.py                               # rebuild site/research.db from final output
python -m pytest tests/test_database_load.py -q     # the load step lost nothing
python -m pytest tests/test_site_api.py -q          # the API against the real data
python -m pytest tests/test_site_browser.py -q      # the pages in a real browser
python validate_data.py all                  # the deep report a person reads
python app.py                                # then the manual checklist below
```

The browser tests need `pip install playwright` and `playwright install
chromium`; they skip themselves when it is not installed. The database tests
and the API tests skip when `site/research.db` has not been built. So a plain
checkout still runs green.

Run one university with `-k`, for example
`python -m pytest tests/test_exported_data.py -q -k unsw`.

`validate_data.py` exits non-zero when a university fails, so it can be the
gate on a data PR. `tests/test_site_api.py` skips itself when the database has
not been built, so it never fails for someone who has only cloned the repo.

## What is automated

| Area | Where | Covers |
|---|---|---|
| Adapters and parsing | `tests/test_*_adapter.py`, `test_*_scraper*.py` | each university's own markup |
| Cleaning and exclusions | `tests/test_publication_exclusions.py`, `test_unsw_review_fixes.py` | HTML in titles, malformed DOIs, reviewed namesake papers |
| Export and dedup | `tests/test_export*.py` | duplicates, near-duplicates, ISSN format, column types |
| Discipline screen | `tests/test_screen.py` | wrong-ORCID researchers |
| Exported tables | `validate_data.py` | structure, values, ISSNs, duplicates, links between tables, coverage floors |
| Exported tables, per university | `tests/test_exported_data.py` | columns, vocabularies, DOI and ISSN format, levels, joins between the three files, duplicates, and the cross-university checks the merge depends on |
| The load into the database | `tests/test_database_load.py` | nothing dropped between CSV and database, no orphan rows, no duplicate researchers or journals |
| Website API and counts | `tests/test_site_api.py` | filters, deep links, counts matching the database, academic levels |
| The pages in a browser | `tests/test_site_browser.py` | filters, combined filters, reset, search, sorting, page size, pager, CSV download, metric re-sort, no script errors |

## What is manual

`tests/test_site_browser.py` drives Chromium, so the checklist below is now
about the things a script cannot judge: how it looks, Firefox, and phone
width. Do it before each client demo and record the result at the bottom.

**Researchers page**

- [ ] The page loads with no console errors (F12, Console tab)
- [ ] University, Field of Research, Level and Rows per page each change the table
- [ ] Two filters together narrow the result rather than replacing it
- [ ] Typing a name filters, and clearing it restores the full list
- [ ] Every column header sorts, and clicking twice reverses it
- [ ] Shift-clicking a second header adds a secondary sort
- [ ] The pager moves between pages and the "Showing x of y" count is right
- [ ] Reset clears every filter
- [ ] Download CSV opens a file with the rows currently shown

**Universities page**

- [ ] All eight universities appear
- [ ] Changing the metric and pressing Update re-sorts the table
- [ ] The columns change to match the chosen metric
- [ ] The university code links through to that university's researchers

**Individual researcher page**

- [ ] Opens from a name on the researchers page
- [ ] The publication count matches the number the researchers page showed
- [ ] The search box and the ABDC-only toggle filter the publication list
- [ ] DOI links open the right paper
- [ ] A researcher with no publications shows the explanatory card, not an empty table

**Home and documentation**

- [ ] The eight cards link to the right filtered lists
- [ ] The numbers on the cards match the universities page
- [ ] The documentation page loads and the data dictionary downloads

**Also check**

- [ ] The site is usable at phone width
- [ ] No sample-data banner appears anywhere

## Mapping to the acceptance tests

| Acceptance test | How it is tested | Status |
|---|---|---|
| A: all 8 universities scraped | `validate_data.py all`, `test_exported_data.py` | Passing |
| B: academic levels mapped | `test_academic_levels_are_A_to_E`, `test_every_researcher_has_an_academic_level` | **Failing**: 32 researchers have no level (Adelaide 14, UniMelb 10, USyd 5, UQ 2, Monash 1) |
| C: website works | `test_site_browser.py` plus the checklist above | **Failing**: 2 of 14 browser tests. A fast name search, and two dropdowns changed in succession, both leave the wrong rows on screen |
| D: no duplicate publications | `test_no_duplicate_publications`, `test_a_doi_is_one_paper` | **Failing**: 1 duplicate (USyd, Peter Wolnizer, "Enabling accountability in museums", 1996) |

## Known open issues

Everything here is a currently failing test, not a guess.

| # | Issue | Owner |
|---|---|---|
| 1 | 32 researchers have no academic level: Adelaide 14, UniMelb 10, USyd 5, UQ 2, Monash 1 | those five |
| 2 | UWA writes publication statuses outside the agreed set, such as "Accepted/In press - 18 Aug 2026", where the schema allows published, forthcoming, working_paper | UWA |
| 3 | Adelaide has 1 publication with no year, ANU has 3 | Adelaide, ANU |
| 4 | USyd lists "Enabling accountability in museums" (1996) twice for Peter Wolnizer | USyd |
| 5 | Roger Simnett appears at both UNSW and Monash on the same ORCID, so he is counted twice in the merged table | UNSW and Monash |
| 6 | Andrew Terry appears at both UNSW and USyd, and Lisa Powell at both Adelaide and Monash | those pairs |
| 7 | The researchers page sends one request per filter change and draws whichever answer arrives last. Confirmed 29 Sep: typing "trotman" produced 7 requests, `?name=trotman` was answered at 18:49:40 and `?name=t` at 18:49:41, and the table kept the 50 rows for "t". The dropdowns do the same: at 19:01:23 `?university=UNSW+Sydney&level=E` was answered and at 19:01:24 `?university=UNSW+Sydney`, the later answer to the earlier request, and the level column then held C, D and E for the full ten seconds the test polls it. It is timing-dependent, so both tests can pass when run alone and fail in a full run. A guard on the request ticket, plus a short debounce on the name box, covers both | front end |
| 8 | Three Monash test files stop `pytest tests` from starting at all: they import selenium, which is not in requirements.txt | Monash |
| 9 | `tests/test_merge_publications.py` fails on Windows on an emoji the console cannot encode. Passes with `PYTHONIOENCODING=utf-8` | whoever owns merge_publications.py |

## Sign-off log

| Date | What was tested | By | Result |
|---|---|---|---|
| 22 Sep | Acceptance C and D, Chrome, whole site against real data | Zarin | C passed; D passed on 5 researchers |
| 29 Sep | All four layers, 192 tests, UNSW output rebuilt first | Zarin | 18 failures, all listed above: 16 data and API, 2 browser. UNSW passes its own 16 data tests |
| | | | |
