# Runtime data

This directory belongs to the independent Python pipeline. A new installation
may contain only this README: `python -m pipeline.runner refresh` bootstraps all
other files from the official UWA and UniMelb sources.

- `cache/`: disposable HTTP response cache.
- `runs/`: immutable raw, validated collection runs.
- `failed/`: diagnostics and partial evidence from unsuccessful refreshes.
- `published/`: approved dataset revisions.
- `current.json`: atomic pointer to the revision the formal application reads.
- `latest_run.json`: most recent successful raw collection.
- `review.sqlite3`: local manual-review decisions.

Generated runtime files are intentionally git-ignored. Client delivery exports
should be copied from the published revision named by `current.json`. Each
published revision contains `uwa_team_fields.csv`, `unimelb_team_fields.csv`,
and a combined file using the team's agreed 18-column order, alongside richer
internal `staff` and `publications` files. Potential duplicate source records
merged for the team export remain visible in
`team_export_duplicate_candidates.json`.
