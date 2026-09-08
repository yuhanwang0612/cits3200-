# cits3200-

## Setup

Run `git config core.hooksPath .githooks` once after cloning — it strips co-author trailers from commit messages before they're made.

Install the shared Python dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

## UWA and UniMelb collection pipeline

The UWA and University of Melbourne collectors can bootstrap from an empty
`data/` directory. They collect Accounting and Finance staff and publications
from official public sources, validate the records, withhold uncertain
identities from the formal publication dataset, and publish a revision
atomically:

```bash
python -m pipeline.runner refresh
```

Use `--use-cache` for a reproducibility check without downloading fresh source
pages. Every successful revision contains separate UWA and UniMelb CSV files in
the team's agreed 18-column order. Unsupported values remain empty rather than
being guessed. Runtime caches, raw runs and review decisions are git-ignored;
see `data/README.md` for the data contract.

For local review of uncertain staff scope and UniMelb author identities:

```bash
python tools/review_workbench/server.py
```

Then open <http://127.0.0.1:8765>. This workbench is a data-preparation tool,
not part of the client-facing website.

Run the regression tests with:

```bash
python -m unittest -v tests/test_base_scrapers.py tests/test_pipeline.py
```

The checked-in files under `final output/` are a dated, strict official-source
snapshot for team review. Citation percentile, ABDC rank and JCR impact factor
are intentionally blank in this snapshot because metric enrichment is not yet
part of this independent Python path.
