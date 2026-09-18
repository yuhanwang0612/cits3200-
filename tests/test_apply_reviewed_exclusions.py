import csv
import json

from apply_reviewed_exclusions import apply


def _write_csv(path, rows):
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def test_offline_exclusion_keeps_csv_json_and_journals_in_sync(tmp_path):
    out = tmp_path / "unimelb"
    out.mkdir()
    publications = [
        {"name": "Bryan Lim", "title": "Wrong", "doi": "10.1093/jtm/taz034", "journal_name": "Wrong Journal"},
        {"name": "Bryan Lim", "title": "Right", "doi": "10.1000/right", "journal_name": "Right Journal"},
    ]
    journals = [
        {"journal_name": "Wrong Journal", "issn": "1"},
        {"journal_name": "Right Journal", "issn": "2"},
    ]
    _write_csv(out / "unimelb_publications.csv", publications)
    _write_csv(out / "unimelb_journals.csv", journals)
    (out / "unimelb_publications.json").write_text(json.dumps(publications))
    (out / "unimelb_journals.json").write_text(json.dumps(journals))

    apply(out)

    with (out / "unimelb_publications.csv").open(newline="") as handle:
        assert [row["title"] for row in csv.DictReader(handle)] == ["Right"]
    assert [row["title"] for row in json.loads((out / "unimelb_publications.json").read_text())] == ["Right"]
    assert [row["journal_name"] for row in json.loads((out / "unimelb_journals.json").read_text())] == ["Right Journal"]
