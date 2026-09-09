# UWA and UniMelb strict snapshot

These CSVs were published by the independent Python collection pipeline from
the successful source run `20260908T105933Z-01e58342`.

- `uwa/uwa_staff.csv`: all 45 people on the targeted official UWA department
  rosters, including people with no matched publication.
- `unimelb/unimelb_staff.csv`: all 99 people on the targeted official UniMelb
  department rosters, including unresolved Minerva identities.
- `uwa/uwa_team_fields.csv`: 1,390 verified UWA researcher-publication rows.
- `unimelb/unimelb_team_fields.csv`: 264 approved UniMelb
  researcher-publication rows.
- `uwa_unimelb_quality.json`: counts, review status and field-completeness audit
  for the published revision.

Both CSVs use the agreed 18 fields:

`name, job_title, academic_level, field_of_research, profile_url, university,
orcid, title, doi, author_count, year, article_url, source,
citation_percentile, journal_name, issn, quality_rank, impact_factor`.

This is a strict official-source snapshot, not a claim that every real-world
publication has been found. No person on an official target roster is excluded.
Unverified UniMelb publication relationships are withheld, while those people
remain visible in the staff CSV. Missing values are left empty rather than
inferred. The OpenAlex/ABDC/Clarivate metric columns are present for schema
compatibility but have not yet been enriched in the independent Python path.
