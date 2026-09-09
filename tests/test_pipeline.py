"""From-zero, approval persistence and rollback tests for the v2 pipeline."""

from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from pipeline.reviews import ReviewStore
from pipeline.runner import read_json, run_refresh
from pipeline.runner import TEAM_FIELDS, deduplicate_team_rows, team_rows
from pipeline.schema import validate


def staff(name, university, discipline, profile, *, review=False, title="Professor"):
    return {
        "name": name,
        "name_clean": name,
        "university": university,
        "discipline": discipline,
        "profile_url": profile,
        "title": title,
        "title_clean": title,
        "inclusion_review_required": review,
        "inclusion_review_reason": "appointment category requires confirmation" if review else None,
    }


def publication(name, university, discipline, identifier):
    return {
        "name": name,
        "university": university,
        "discipline": discipline,
        "title": f"Publication {identifier}",
        "year": "2024",
        "type": "Journal Article",
        "source": "Official repository",
        "publication_id": identifier,
        "researcher_match_confidence": "high",
        "requires_review": False,
    }


def collectors(*, changed=False):
    def uwa(**_options):
        people = [
            staff("Alice", "UWA", "Accounting", "https://example/Alice"),
            staff("Bob", "UWA", "Finance", "https://example/Bob", review=True, title="Visitor" if not changed else "Adjunct"),
        ]
        pubs = [publication("Alice", "UWA", "Accounting", "uwa-1"), publication("Bob", "UWA", "Finance", "uwa-2")]
        return people, pubs, {}

    def unimelb(**_options):
        people = [staff("Carol", "UNIMELB", "Finance", "https://example/Carol")]
        pubs = [publication("Carol", "UNIMELB", "Finance", "melb-1")]
        quality = {"identity_review_queue": [{
            "name": "Unresolved Person", "discipline": "Finance",
            "profile_url": "https://example/unresolved", "identity_confidence": "none", "candidate_count": 0,
        }]}
        return people, pubs, quality

    return {"UWA": uwa, "UNIMELB": unimelb}


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.data = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_empty_directory_bootstrap_and_manual_approval(self):
        result = run_refresh(self.data, collectors=collectors(), refresh=True)
        self.assertTrue(result["run"]["from_zero_capable"])
        self.assertFalse(result["run"]["previous_dataset_read"])
        current = read_json(self.data / "current.json")
        published = self.data / current["directory"]
        self.assertEqual(len(read_json(published / "staff.json")), 3)
        self.assertEqual(len(read_json(published / "publications.json")), 3)
        self.assertEqual(len(read_json(published / "uwa_staff.json")), 2)
        self.assertEqual(len(read_json(published / "unimelb_staff.json")), 1)
        self.assertTrue((self.data / current["uwa_staff_csv"]).exists())
        self.assertTrue((self.data / current["unimelb_staff_csv"]).exists())
        for key in ("uwa_team_csv", "unimelb_team_csv", "combined_team_csv"):
            self.assertTrue((self.data / current[key]).exists())
        with (self.data / current["combined_team_csv"]).open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            self.assertEqual(tuple(reader.fieldnames or ()), TEAM_FIELDS)
            exported = list(reader)
        self.assertEqual(len(exported), 3)
        self.assertEqual({row["source"] for row in exported}, {"pure", "minerva"})

    def test_forthcoming_publication_may_have_no_year(self):
        person = staff("Alice", "UWA", "Accounting", "https://example/Alice")
        item = publication("Alice", "UWA", "Accounting", "forthcoming")
        item["year"] = None
        self.assertEqual(validate([person], [item]), [])

    def test_team_export_deduplicates_title_year_and_keeps_evidence(self):
        person = staff("Alice", "UWA", "Accounting", "https://example/Alice")
        first = publication("Alice", "UWA", "Accounting", "one")
        second = publication("Alice", "UWA", "Accounting", "two")
        first["title"] = second["title"] = "Same title"
        first.update({"doi": None, "link": "https://example/one", "journal": None, "n_authors": 2})
        second.update({"doi": None, "link": "https://example/two", "journal": "Journal", "n_authors": 2})
        exported, duplicates = deduplicate_team_rows(team_rows([person], [first, second]))
        self.assertEqual(len(exported), 1)
        self.assertEqual(exported[0]["journal_name"], "Journal")
        self.assertEqual(len(duplicates), 1)
        self.assertEqual(duplicates[0]["candidate_count"], 2)

    def test_official_roster_person_is_never_gated_by_appointment_category(self):
        run_refresh(self.data, collectors=collectors(), refresh=True)
        store = ReviewStore(self.data / "review.sqlite3")
        try:
            self.assertEqual(store.list(entity_type="staff"), [])
        finally:
            store.close()
        run_refresh(self.data, collectors=collectors(changed=True), refresh=True)
        current = read_json(self.data / "current.json")
        people = read_json(self.data / current["staff_json"])
        self.assertEqual(len(people), 3)
        self.assertIn("Bob", {row["name_clean"] for row in people})

    def test_failed_refresh_does_not_replace_current(self):
        run_refresh(self.data, collectors=collectors(), refresh=True)
        before = json.loads((self.data / "current.json").read_text())

        def failure(**_options):
            raise RuntimeError("upstream unavailable")

        with self.assertRaises(RuntimeError):
            run_refresh(self.data, collectors={"UWA": failure}, refresh=True)
        after = json.loads((self.data / "current.json").read_text())
        self.assertEqual(before, after)

    def test_reported_partial_fetch_does_not_replace_current(self):
        run_refresh(self.data, collectors=collectors(), refresh=True)
        before = json.loads((self.data / "current.json").read_text())

        def incomplete(**_options):
            people = [staff("Alice", "UWA", "Accounting", "https://example/Alice")]
            pubs = [publication("Alice", "UWA", "Accounting", "uwa-1")]
            return people, pubs, {"detail_failures": [{"url": "https://example/pub"}]}

        with self.assertRaisesRegex(ValueError, "source collection was incomplete"):
            run_refresh(self.data, collectors={"UWA": incomplete}, refresh=True, limit_staff=1)
        after = json.loads((self.data / "current.json").read_text())
        self.assertEqual(before, after)
        failed_runs = list((self.data / "failed").iterdir())
        self.assertEqual(len(failed_runs), 1)
        failure = read_json(failed_runs[0] / "failure.json")
        self.assertFalse(failure["current_dataset_was_replaced"])
        self.assertIn("detail failures", failure["error"])
        self.assertTrue((failed_runs[0] / "source_quality.json").exists())

    def test_old_review_candidates_are_hidden_after_new_run(self):
        run_refresh(self.data, collectors=collectors(), refresh=True)

        def clean_uwa(**_options):
            people = [staff("Alice", "UWA", "Accounting", "https://example/Alice")]
            pubs = [publication("Alice", "UWA", "Accounting", "uwa-1")]
            return people, pubs, {}

        run_refresh(self.data, collectors={"UWA": clean_uwa}, refresh=True, limit_staff=1)
        store = ReviewStore(self.data / "review.sqlite3")
        try:
            self.assertEqual(store.list(), [])
            self.assertEqual(store.counts()["total"], 0)
        finally:
            store.close()

    def test_identity_approval_requires_verifiable_evidence_and_persists(self):
        run_refresh(self.data, collectors=collectors(), refresh=True)
        store = ReviewStore(self.data / "review.sqlite3")
        try:
            item = next(row for row in store.list(entity_type="identity"))
            with self.assertRaisesRegex(ValueError, "repository_author_name"):
                store.decide(item["review_key"], "approved", edited=item["candidate"])
            verified = {
                **item["candidate"],
                "repository_author_name": "Person, Unresolved",
                "internal_id": "author-123",
                "orcid": "",
                "evidence_url": "https://example/minerva-record",
            }
            store.decide(item["review_key"], "approved", edited=verified)
            self.assertEqual(store.approved_identity_overrides(), [verified])
            store.connection.execute("UPDATE reviews SET active=0 WHERE review_key=?", (item["review_key"],))
            store.connection.commit()
            self.assertEqual(store.approved_identity_overrides(), [verified])
        finally:
            store.close()

    def test_refresh_passes_approved_identity_overrides_to_collectors(self):
        run_refresh(self.data, collectors=collectors(), refresh=True)
        store = ReviewStore(self.data / "review.sqlite3")
        try:
            item = next(row for row in store.list(entity_type="identity"))
            verified = {
                **item["candidate"], "repository_author_name": "Person, Unresolved",
                "internal_id": "author-123", "orcid": "",
                "evidence_url": "https://example/minerva-record",
            }
            store.decide(item["review_key"], "approved", edited=verified)
        finally:
            store.close()

        seen = []
        def observing_collector(**options):
            seen.extend(options["identity_overrides"])
            people = [staff("Alice", "UWA", "Accounting", "https://example/Alice")]
            pubs = [publication("Alice", "UWA", "Accounting", "one")]
            return people, pubs, {}

        run_refresh(
            self.data, collectors={"UWA": observing_collector}, refresh=True, limit_staff=1
        )
        self.assertEqual(seen, [verified])


if __name__ == "__main__":
    unittest.main()
