# Local review workbench

This is Alex's local review tool. It is not part of the formal client-facing
application and should not be deployed publicly.

Start it from the repository root:

```bash
python tools/review_workbench/server.py
```

Then open `http://127.0.0.1:8765`. The refresh button starts the independent
pipeline in the background. Review decisions are stored in
`data/review.sqlite3`; generated formal data is published atomically through
`data/current.json`.

## What the queue means

Every person listed on the targeted official department pages is retained in
the staff dataset. Appointment labels can be inspected as quality notes but do
not gate inclusion.

- **Minerva identity**: the staff member is already retained, but their
  publications are withheld because no unique repository identity was found.
  Verify the exact Minerva author name and either the internal author ID or
  ORCID from an official source, record the evidence URL, save, and refresh.
  The collector still requires an exact ID match and never attributes papers
  from a name match alone.
- **Publication**: inspect the source evidence and only approve when the
  researcher-publication relationship is verified.

Leaving a Minerva identity unresolved is safe: it produces incomplete coverage,
not guessed publications. If evidence is unclear, defer it and ask the client.
