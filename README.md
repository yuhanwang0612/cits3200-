# cits3200-

## Setup

Run `git config core.hooksPath .githooks` once after cloning — it strips co-author trailers from commit messages before they're made.

Install the Python dependencies:

```bash
python -m pip install -r requirements.txt
```

## University adapters

Run one university through the shared retrieval, enrichment, screening and
export pipeline:

```bash
python run.py --uni unimelb --ror 01ej9dk98
python run.py --uni uwa --ror 03qn8fb07
```

The UniMelb adapter uses the official Faculty of Business and Economics staff
lists and Minerva Access. The UWA adapter uses the official Pure organisation,
person and publication pages. Both return the shared `core.schema` contract;
Crossref, OpenAlex, ABDC, Clarivate and Scimago remain shared pipeline steps.

Official staff without a verified publication are retained by default, as the
staff directory defines who is in scope. Use `--drop-empty-staff` only when an
explicit downstream export requires it. Generated files are written under
`final output/<uni>/`.
