# cits3200-

## Setup

Run `git config core.hooksPath .githooks` once after cloning — it strips co-author trailers from commit messages before they're made.

Install the Python dependencies:

```bash
python -m pip install -r requirements.txt
```

## Architecture

The project has two halves: a **data pipeline** that builds CSVs for each
university, and a **website** that serves those CSVs from a SQLite database.

```
base_scrapers/<uni>.py      staff + publications from the university's own site
        │                   (returns the core/schema.py contract)
info/                       extra publications by ORCID: orcid, crossref, openalex
        │
core/clean.py               one shared clean + exclusion pass for every source
        │
enrichment/                 per-publication data joined by DOI / ISSN:
        │                   openalex, crossref, abdc, clarivate (JIF), scimago
screen.py                   drop papers outside the researcher's discipline
        │
core/schema.py validate     contract check
        │
export.py                   dedupe + write  final output/<uni>/*.csv
        │
load.py                     all final output CSVs  ->  site/research.db
        │
app.py  (+ admin.py)        Flask site and JSON API; admin login, edit, refresh
```

`run.py --uni <name>` runs everything from the adapter through `export.py` for
one university. `load.py` then rebuilds the database from every university's
output.

### Where things live

| Path | What it is |
| --- | --- |
| `base_scrapers/` | One adapter per G8 university. The only university-specific code. |
| `core/` | Shared config, cached HTTP (`cache/http/`), cleaning, the adapter contract, title normalisation. |
| `info/` | Retrieval of *extra* publications linked to a researcher's ORCID. |
| `enrichment/` | Journal and citation fields added to publications that already exist. |
| `screen.py`, `export.py` | Discipline screening, then dedupe and CSV export. The same for every university. |
| `data/` | Reference lists (ABDC, Scimago) and human-reviewed overrides and exclusions. |
| `final output/<uni>/` | Pipeline output: `*_staff.csv`, `*_publications.csv`, `*_journals.csv`, `*_harvest.csv`. |
| `load.py`, `models.py` | CSV to SQLite loader and the table definitions (researcher, journal, publication, harvest). |
| `app.py`, `admin.py`, `site/` | Web server, admin blueprint and the static front end. |
| `refresh_manager.py` | The admin "refresh" button: runs every adapter, then `load.py` into a staging DB, then swaps it in. The old DB is kept in `backups/`. |
| `tests/` | `python -m pytest`. See `docs/TEST_PLAN.md`. |

### Adding a university

Create `base_scrapers/<uni>.py` that defines `ROR` and a
`collect(verbose=True, refresh=False)` function returning `(records, pubs)` in
the shape described in `core/schema.py`. Nothing else in the pipeline needs to
change. Then run `python run.py --uni <uni>` and `python load.py`.

### Fixing bad data

Fix errors in the pipeline, or in the review files in `data/` (overrides and
exclusions), and rerun. Never edit files in `final output/` by hand, because
the next run overwrites them.

### Refreshing data

The admin refresh button calls `run.py --refresh` for each university. This
bypasses the HTTP cache on purpose so the data is current, which is why it is
much slower than a normal `run.py`, which reuses cached responses.

### Configuration

Copy `.env.example` to `.env` and set `OPENALEX_API_KEY`,
`CLARIVATE_API_KEY`, `SECRET_KEY` and `ADMIN_PASSWORD`. On a hosted server,
`SECRET_KEY` must be set. Without it the admin session falls back to an
insecure development key.

## University adapters

Run one university through the shared retrieval, enrichment, screening and
export pipeline:

```bash
python run.py --uni unimelb --ror 01ej9dk98
python run.py --uni uwa --ror 03qn8fb07
python run.py --uni anu --ror 019wvm592
```

The UniMelb adapter uses the official Faculty of Business and Economics staff
lists and Minerva Access. The UWA adapter uses the official Pure organisation,
person and publication pages. The ANU adapter uses the public RSA and RSFAS
staff directories and profile pages, not the Pure-based research portal.
All return the shared `core.schema` contract; Crossref, OpenAlex, ABDC,
Clarivate and Scimago remain shared pipeline steps.

For UniMelb, the official FBE directory is the staff ground truth. The adapter
first resolves exact Minerva internal author IDs across both departmental
collections, then tries exact family-first author searches across Minerva.
Staff still lacking an identifier are matched to OpenAlex only when an exact
name alias is associated with the UniMelb ROR. Ambiguous identities are left
blank and listed in `unimelb_adapter_quality.json`; they are never reported as
confirmed zero-publication researchers.

The UniMelb run also writes `unimelb_identity_review.csv`. For an identity that
a human can verify, copy the selected identifier into
`data/unimelb_identity_overrides.csv`, keep the official profile URL and name,
set `review_decision` to `approved`, and record an evidence URL. The next normal
`python run.py --uni unimelb` run applies that decision automatically. Rows not
explicitly approved are ignored, so merely listing a candidate cannot change
the production dataset.

Staff without a publication are dropped from the export by default. Use
`--keep-empty-staff` to retain every official staff member. To apply the same
rule to existing outputs without re-running the pipeline, use
`python drop_empty_staff.py` (add `--dry-run` to preview). Generated files are
written under `final output/<uni>/`.
