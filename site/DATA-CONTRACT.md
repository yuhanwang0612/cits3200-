# Site design — what it is, and the data contract

**Owner:** Jamie Taylor (site design, allocated 2 Sep 2026)
**For:** Alex Zhao (home + university pages), Zarin Tasnim (researcher page),
Mohammad Saeed (refresh button), Yuhan Wang (documentation), Sean Du (database)

This folder is the **look and feel plus the data contract** for the site. It is not the finished
site — it is the shell the rest of us build inside, so that four people working separately end up
with one coherent product rather than four different ones.

The client said on 26 Aug she is happy with the previous team's design
(http://54.157.241.216/ — confirmed still live, 3 Sep 2026). This keeps that information
architecture deliberately: same pages, same filters, same table columns. What is different is
that it is client-side, it has a CSV download (the client's stated minimum goal includes
"download as Excel", and I could not find one on the reference site), and it has columns already
in place for Scimago quartile and Journal Impact Factor so nothing needs redesigning when those
joins land.

---

## How to run it

No build step, no dependencies, no internet needed.

    cd site
    python -m http.server 8000

then open http://localhost:8000

Opening `index.html` straight off disk will **not** work — the pages `fetch()` JSON, and browsers
block that on `file://`. Serve the folder.

---

## Files

| File | Owner | Status |
|---|---|---|
| `assets/style.css` | Jamie | Done — the visual system. Add to it; don't write CSS elsewhere. |
| `assets/app.js` | Jamie | Done — shared helpers: load, sort, paginate, escape, CSV export. |
| `index.html` | Alex | **Working example.** Home page with university cards. |
| `universities.html` | Alex | Stub — per-university detail. |
| `researchers.html` | Jamie | **Working example.** Filters + sortable ranked table. |
| `researcher.html` | Zarin | **Working example.** One researcher's publications. |
| `documentation.html` | Yuhan | Stub. |
| `data/*.json` | Sean (real) / Jamie (sample) | **Sample data only.** See below. |
| `gen_sample_data.py` | Jamie | Regenerates the sample data. Delete once real data is wired in. |

`index.html`, `researchers.html` and `researcher.html` are fully working against the sample data.
Copy their structure rather than starting from scratch — the header, footer, filter panel, table
and pager are all already styled.

---

## The data contract

The site is **client-side**. Pages `fetch()` JSON from `data/` and render in the browser. The back
end's only job is to write these three shapes. Nothing else is required of it.

Every file carries a `meta` object. `is_sample_data: true` is what makes the yellow banner honest —
set it to `false` on real exports and delete the banner element from each page.

### 1. `data/universities.json` — home page

```json
{
  "meta": { "generated": "2026-09-03", "is_sample_data": true },
  "universities": [
    {
      "code": "ANU",
      "name": "The Australian National University",
      "researcher_count": 44,
      "publication_count": 296,
      "abdc_ranked_count": 287
    }
  ]
}
```

### 2. `data/researchers.json` — rankings table

One row per researcher. This is a **summary** file — it must stay small enough to load in one
request, so it carries counts, not publications.

```json
{
  "meta": { "generated": "2026-09-03", "is_sample_data": true },
  "researchers": [
    {
      "id": "anu-alan-welsh",
      "name": "Alan Welsh",
      "university": "The Australian National University",
      "university_code": "ANU",
      "field_of_research": "Finance",
      "academic_level": "Professor",
      "level_code": "E",
      "publication_count": 131,
      "abdc_ranked_count": 107,
      "count_a_star": 31,
      "count_a": 52,
      "count_b": 18,
      "count_c": 6,
      "count_unranked": 24
    }
  ]
}
```

`id` must be URL-safe and stable — it is the filename in step 3 and the link target from the
rankings table. `<university_code>-<name slugified>` works and is what the sample data uses.

### 3. `data/publications/<id>.json` — one per researcher

```json
{
  "meta": { "generated": "2026-09-03", "is_sample_data": true },
  "researcher": { "...the same object as in researchers.json..." },
  "publications": [
    {
      "title": "Accounting for financial instruments with characteristics of debt and equity",
      "journal_name": "Accounting and Finance",
      "issn": "0810-5391",
      "year": 2017,
      "quality_rank": "A",
      "scimago_quartile": "Q2",
      "impact_factor": 2.7,
      "cited_by_count": 41,
      "doi": "10.1111/acfi.12280",
      "article_url": "https://doi.org/10.1111/acfi.12280",
      "publication_type": "journal_article"
    }
  ]
}
```

**Per-researcher files, not one big one.** The merged dataset is 13,527 rows; a single
publications file would be several megabytes and the detail page would stall on every visit.
One file per researcher keeps each request small, and it means a researcher whose data is
re-exported doesn't invalidate everyone else's.

### Field notes — read these before writing an exporter

- **Every field maps 1:1 onto a column that already exists in `combined_publications.csv`.**
  No new columns are being asked for. `researcher`, `university`, `field_of_research`,
  `academic_level`, `level_code`, `title`, `year`, `doi`, `article_url`, `journal_name`, `issn`,
  `quality_rank`, `scimago_quartile`, `impact_factor`, `cited_by_count`, `publication_type` all
  exist today.
- **`quality_rank`** is one of `"A*"`, `"A"`, `"B"`, `"C"`, `"none"`, or `null`.
  `"none"` means we checked and the journal is unranked. `null` means we have not checked.
  These are different things and the UI shows them differently — do not collapse them.
- **Missing is `null`, never `""`, never `"N/A"`, never `0`.** The front end renders `null` as an
  em-dash and sorts it to the bottom. A `0` in `impact_factor` is a claim that the journal has an
  impact factor of zero.
- **`impact_factor` and `scimago_quartile` are expected to be `null` for most rows today.**
  As of 3 Sep, `impact_factor` is empty on all 13,527 rows of `combined_publications.csv`. The
  columns are here so that nothing needs rebuilding when the join lands.
- **`publication_type`**: only journal articles should reach the site (client rule, 12 Aug).
  Filtering happens in the exporter, not in the browser. Note that `publication_type` currently
  has 30+ distinct spellings across the eight universities — whoever writes the exporter has to
  normalise, and `null` (all 662 UQ rows) is not the same as "not a journal article".
- **Numbers as JSON numbers, not strings.** `"year": 2017`, not `"2017"`.

---

## Refresh button (Saeed)

The shell has no refresh control yet because its specification is still contradictory. The 12 Aug
minutes say refresh must be split into separate per-operation buttons; the 19 Aug minutes say a
single refresh button, not per-university. Those may be reconcilable — per-operation is not the
same as per-university — but nobody has said so.

The design accommodates either: a single `.btn` in the page toolbar, or a row of them. Once the
spec is settled it drops into the `.toolbar .right` block on any page. It needs one answer first.

---

## House rules

1. **All colour, spacing and typography lives in `assets/style.css`.** If you need something new,
   add a token or a component class there. Inline styles and page-level `<style>` blocks are how
   four pages end up looking like four different sites.
2. **Use the page skeleton exactly: `<header class="site-header"><div class="wrap-wide">`, then
   `<main><div class="wrap">`, then `<footer class="site-footer"><div class="wrap-wide">`.**
   `.wrap` is the centred content column; `.wrap-wide` is full-bleed and is what keeps the brand
   and nav against the screen edges on a wide monitor. The body is a full-height flex column so
   the footer pins to the bottom of the viewport on short pages — a page that skips `<main>` will
   have its footer floating mid-screen.
3. **Escape everything from data with `esc()` before putting it in `innerHTML`.** Publication
   titles contain quotes and angle brackets.
4. **Run any URL from the data through `safeUrl()` before using it as an `href`.** It passes
   http/https and rejects everything else. These URLs come from scraped third-party pages.
5. **Never render a missing value as blank or `undefined`.** Use `val()`, `num()` or `abdcPill()`.
   Note `0` is a real value and renders as `0`; only `null`/`undefined`/`""` render as an em-dash.
6. **Keep pages working when a fetch fails.** `showLoadError()` exists for this, and hide the
   table shell rather than leaving an empty one on screen.
7. **Don't add a CDN or a framework.** No build step means anyone on the team can open the folder
   and it just runs — that matters more than convenience right now, and it keeps the Sprint 2 demo
   from depending on the venue's wifi.

### Sortable tables

Mark a header `class="sortable" data-key="field" data-type="..."` and give it a
`<span class="arrow">↕</span>`. `data-type` is one of:

| type | use for | behaviour |
|---|---|---|
| `text` | names, journals, quartiles | case-insensitive alphabetical |
| `num` | counts, year, JIF, citations | numeric |
| `abdc` | `quality_rank` | **A\* → A → B → C → none.** Sorting this as text puts A before A\*, which is wrong |

`attachSorting(table, cb)` wires click **and** keyboard (Tab to a header, Enter or Space).
Shift+click, or Shift+Enter, adds a secondary sort; the callback receives the full list of sort
specs, primary first, for `multiCompare()`. Missing values always sort to the bottom in both
directions — a blank is not "smallest".
