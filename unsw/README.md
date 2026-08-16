# UNSW Accounting & Finance Scraper

Part of CITS3200 Team 20's Researcher Productivity tool. Covers the **UNSW** slice
of the data collection (accounting and finance academics).

## What it collects

UNSW splits these disciplines across **two** schools, so both are collected:

- School of Accounting, Auditing and Taxation → `Accounting`
- School of Banking and Finance → `Finance`

…and, in a later stage, their publications from each academic's staff profile page.

## How it works, and why

The Business School "Our people" directory at `unsw.edu.au/business/our-people`
is a client-side search. The school filter, sort order and pagination all live in
the URL *hash*, which the server never sees, and the results are written into the
page by JavaScript after load. A plain `requests` call returns the page shell with
no staff in it, so the **listing** is read with Selenium — the same approach Yuhan
used for Monash.

Individual staff profiles at `unsw.edu.au/staff/<slug>` **are** server-rendered, so
those are read with plain `requests`. Better still, UNSW publishes the fields we
need as meta tags, which removes all the guesswork from parsing:

```html
<meta name="profile-full-name"          content="Dr Nicole Ang">
<meta name="profile-school"             content="School of Accounting, Auditing and Taxation">
<meta name="profile-university-role"    content="Senior Lecturer">
<meta name="profile-faculty"            content="Business School">
```

### Why we do not filter by school in the listing URL

This was the cause of the first version returning zero staff, and it is worth
recording because the failure is silent.

The site's school filter uses **display labels**, not the school's real name —
`Banking & Finance`, not `School of Banking and Finance`. When the encoded value
in the hash isn't an exact match, the page does one of two things:

- returns **"No results"** — which looks identical to the page not having loaded; or
- **drops the filter without saying so** and returns all 354 Business School staff —
  which looks like success and would have quietly put economists and marketers into
  our accounting dataset.

Neither is safe to build on. So we page the whole Business School and read the
authoritative school off each profile page instead. It costs more requests, but it
cannot silently return the wrong set.

### Why we do not use the search API directly

The directory is powered by Funnelback at `unsw-search.funnelback.squiz.cloud`.
Querying it directly would be faster, but that host's `robots.txt` disallows `/s/`,
which is the endpoint in question, so it is off limits. `www.unsw.edu.au/robots.txt`
places no restriction on `/staff/`, which is the route we use.

We do not attempt to bypass any access control or bot protection.

## Run it

```bash
python -m pip install -r requirements.txt
python unsw_scraper.py --headless
```

Useful flags:

| Flag | Effect |
|---|---|
| `--limit 20` | only check the first 20 profiles — use this to test quickly |
| `--delay 1` | seconds between profile fetches |
| `--no-cache` | refetch profiles instead of reusing `output/profile_cache/` |
| `--timeout 60` | wait longer for the listing to render |
| `--journals-only` | write only journal articles — see below |

Profile pages are cached in `output/profile_cache/`, so the full crawl is a one-off
cost and every later run is effectively instant. The cache is not committed.

Writes to `./output/`:

| File | Contents |
|---|---|
| `unsw_staff.csv` / `.json` | One row per academic — name, job_title, academic_level (A–E), field_of_research, profile_url, university, research_portal_url, school |
| `unsw_publications.csv` | One row per publication — title, journal_name, year, publication_type, doi, article_url, coauthors, n_authors, volume, pages, publisher, plus blank `abdc_self_reported` and `citation_percentile` columns to be filled downstream |
| `unsw_unparsed_publications.csv` | Entries we could not parse, with the raw text — see below |
| `unsw_no_publications.csv` | Academics whose profile lists nothing at all |

Field names match the **Scope of Work data dictionary (section 3.5.4)** and the ANU
scraper's output, so this loads into the shared database with no reshaping.

## Publications

Publications are on the same profile page, inside the Publications tab, and they are
**server-rendered** — so they cost no extra request and no browser. Most entries carry
structured markup from UNSW's research gateway feed, which is why the fields come out
clean rather than being pulled apart with regular expressions:

```html
<div class="publication-item">
  <span class="publication-category">Journal articles</span>
  <span class="rg-author">Li H;  Liu L;  Masulis R;  Zein J</span>
  <span class="rg-title">'Does common ownership raise antitrust concerns?'</span>
  <i    class="rg-source-title">Journal of Corporate Finance</i>
  <span class="rg-volume">100</span>
  <a href="http://dx.doi.org/10.1016/j.jcorpfin.2026.103037">…</a>
</div>
```

Three things are deliberate:

- **Nothing is guessed at.** A small number of entries are a bare paragraph of free
  text with no structure. Those go to `unsw_unparsed_publications.csv` with the raw
  citation rather than being parsed heuristically. Dropping them silently would
  understate someone's output; mis-parsing them would be worse.
- **`publication_type` is kept, not filtered.** The client asked on 12 August for
  journals only, and that filter is applied in the shared merge step rather than
  here. Two reasons: re-scraping is expensive and discarding data we already hold
  is irreversible, and the eight scrapers use different type vocabularies — UNSW
  says "Journal articles" — so filtering in each scraper would make "journal
  article" quietly mean eight different things, against the client's stated
  priority of standardisation. `--journals-only` applies it locally for checking
  UNSW on its own.
- **`n_authors` is counted from the page's own author list**, not by splitting the
  joined string afterwards, so a name containing a semicolon cannot inflate it.
  It is left blank rather than 0 when no authors are listed — "we don't know" and
  "zero authors" are different claims. The largest genuine value in the data is
  341, on the *Non-Standard Errors* paper, which really does have that many
  authors; it is not a parsing failure.
- **Some entries genuinely have no year** — mostly SSRN preprints, where UNSW's own
  page shows no date. The year is left blank rather than inferred from the DOI.

Two data-quality problems on UNSW's side are handled explicitly: a number of entries
link to a bare `http://dx.doi.org` with no identifier after it (discarded, since it
resolves nowhere), and a few are listed twice, identical in every field (deduplicated
on title + year + type + DOI, so a title that legitimately appears as both a
conference paper and a later book is kept as two records).

## Tests

```bash
python -m pytest test_unsw_scraper.py -v
```

38 tests, all offline against fixtures defined in the test file — nothing touches
unsw.edu.au, so the suite runs in about a second and is safe in CI.

They are not there for coverage. Each one pins a rule that was actually wrong at
some point, or that is subtle enough to be "simplified" back into a bug later:

- `Associate Professor` must not be read as `Associate Lecturer` — ladder ordering
- `Head of School` is not a rank, so the level comes from the name prefix instead
- `Emeritus Scientia Professor Roger Simnett` — honorifics stack
- a bare `http://dx.doi.org` link is discarded, not written out as a URL
- the same paper under a JSTOR DOI and a Wiley DOI is **one** publication
- the same title as a 2015 conference paper and a 2019 book is **two**
- free-text entries go to the unparsed log rather than being guessed at
- `TARGET_SCHOOLS` holds `School of Banking and Finance`, not the directory's
  display label `Banking & Finance` — confusing the two is what made the first
  version of this scraper return zero staff

## Progress

- [x] Stage 1 — staff directory collection
- [x] Stage 2 — publications from staff profile pages
- [x] Stage 3 — unparsed / no-publication logging
- [ ] Stage 4 — join to the ABDC list and OpenAlex citation percentiles

## Known limitations so far

- Selenium is required for the listing, which makes it the part most likely to break
  if UNSW changes its front end. If it does, `output/debug_listing.html` is written
  automatically so the failure can be diagnosed from what the page actually returned
  rather than guessed at.
- We read every Business School profile to find the ~2 schools we want. That is
  wasteful in requests but robust; the cache means we only pay it once.
- Academic level is derived from the job title. Titles that don't match the ladder
  are recorded with a blank level rather than being guessed at.
- Education-focused and teaching-focused roles are marked `Exclude`, matching the
  reference system's published methodology and FR4.
- A researcher whose profile page no longer resolves is treated as no longer current
  (FR1), following the client's guidance on Stephen Gray at UQ.

## Politeness / compliance

Checks `robots.txt` once per host before crawling it, reports the declared
`Crawl-delay`, sends a normal browser User-Agent, sleeps between requests, and
caches pages so nothing is fetched twice.
