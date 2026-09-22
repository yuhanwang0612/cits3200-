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
