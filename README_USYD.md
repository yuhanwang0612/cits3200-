# Group 20 — Mohammad/Saeed USyd Sprint 2 contribution

## Purpose

This is a **starting implementation** for the current Sprint 2 plan:

1. identify University of Sydney Accounting and Finance researchers;
2. read ORCID from Sydney Profiles;
3. resolve each researcher in OpenAlex using ORCID;
4. retrieve that author's OpenAlex works;
5. keep journal articles only;
6. write a common-format `usyd_publications.csv`;
7. preserve DOI, ISSN, citation fields and ORCID for later cross-checking/ranking joins.

It does **not** claim that OpenAlex alone is complete. Yuanji asked the team to
cross-check multiple sources. The CSV should therefore be compared against
Sydney Profiles / university records and, where needed, other sources before
being treated as the final USyd dataset.

## Why this fits the current repository

The current `main` snapshot already contains:

- `unimelb_publications.csv`
- `uq_publications.csv`
- `uwa_publications.csv`

Lily has asked for each university's latest publication CSV so her merge script
can combine them. This script produces `usyd_publications.csv` with the common
fields already seen in the more complete UWA/UniMelb CSVs, plus `orcid` and
`openalex_author_id` as additional provenance/identifier columns.

## Codespaces setup

From repository root:

```bash
git switch main
git pull --ff-only origin main
git switch -c saeed/usyd-openalex-pipeline
```

Copy this contribution into the repo (recommended paths):

```text
scripts/usyd_collect.py
tests/test_usyd_collect.py
requirements-usyd.txt
README_USYD.md
```

Install:

```bash
python -m pip install -r requirements-usyd.txt
python -m playwright install --with-deps chromium
pytest -q tests/test_usyd_collect.py
```

## OpenAlex key

OpenAlex currently requires a free API key.

In Codespaces, use **Settings → Secrets and variables → Codespaces** for a
repository/user secret named:

```text
OPENALEX_API_KEY
```

Then restart the Codespace, or export it only for the current shell:

```bash
export OPENALEX_API_KEY="YOUR_KEY"
```

Never commit the key and never put it inside the script.

## Run

```bash
python scripts/usyd_collect.py --out-dir output/usyd
```

Expected outputs:

```text
output/usyd/usyd_staff.csv
output/usyd/usyd_publications.csv
```

## Manual review before PR

Check at least:

- 10 researcher names/profile URLs;
- ORCID belongs to the correct person;
- non-standard staff titles are not silently assigned A-E;
- publication rows are journal articles;
- DOI and journal look correct;
- obviously missing recent papers are noted for cross-source review.

Then copy the validated publication CSV to repo root because that is where the
current merged university CSVs live:

```bash
cp output/usyd/usyd_publications.csv ./usyd_publications.csv
```

## Commit

```bash
git add scripts/usyd_collect.py tests/test_usyd_collect.py requirements-usyd.txt README_USYD.md usyd_publications.csv
git status
git diff --cached --stat
git commit -m "feat: add USyd OpenAlex publication pipeline"
git push -u origin saeed/usyd-openalex-pipeline
```

Open a PR to `main` and **do not merge it yourself** unless the team has agreed
that workflow.

Suggested PR title:

```text
USyd publication pipeline and current CSV output
```

## Important limitations to state in the PR

- OpenAlex is a cross-check/enrichment source, not the sole source of truth.
- Forthcoming papers may require university/manual checking.
- Researchers without ORCID/OpenAlex matches need fallback handling.
- Academic roles outside the standard A-E titles require manual/team rules.
- ABDC/SCImago/JIF joins are still team-level enrichment work.
