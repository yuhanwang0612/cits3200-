"""Regression tests for the UWA and UniMelb base adapters."""

from __future__ import annotations

import importlib.util
import inspect
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_adapter(name):
    path = ROOT / "base_scrapers" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"base_scrapers_{name}", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


uwa = load_adapter("uwa")
unimelb = load_adapter("unimelb")

STAFF_REQUIRED = {"name", "name_clean", "university", "discipline", "profile_url"}
PUB_REQUIRED = {"name", "title", "year", "type", "source"}
TYPES = {
    "Journal Article", "Preprint", "Conference Paper", "Book", "Book Chapter",
    "Thesis", "Research Report", "Working Paper", "Data Collection",
    "Newspaper Article", "Other",
}


class ParsingTests(unittest.TestCase):
    def test_adapters_use_team_university_names(self):
        self.assertEqual(unimelb.UNIVERSITY, "University of Melbourne")
        self.assertEqual(uwa.UNIVERSITY, "University of Western Australia")

    def test_adapters_accept_shared_refresh_option(self):
        self.assertIn("refresh", inspect.signature(unimelb.collect).parameters)
        self.assertIn("refresh", inspect.signature(uwa.collect).parameters)

    def test_uwa_staff_profile(self):
        html = """
        <div class="page-section-header"><h1>Professor Jane Example</h1></div>
        <div class="rendering_personorganisationlistrendererportal"><ul><li>
          <span class="job-title">Professor</span>
          <a rel="Organisation" href="/en/organisations/accounting/"><span>Accounting</span></a>
        </li></ul></div>
        <a href="https://orcid.org/0000-0002-1825-0097">ORCID</a>
        <p>View all 12 research outputs</p>
        """
        row = uwa._parse_staff_profile(
            html,
            "https://research-repository.uwa.edu.au/en/persons/jane-example/",
            "Accounting",
            "Teaching and Research",
        )
        self.assertEqual(row["name_clean"], "Jane Example")
        self.assertEqual(row["level_code"], "E")
        self.assertEqual(row["orcid"], "0000-0002-1825-0097")
        self.assertEqual(row["reported_publication_count"], 12)
        self.assertTrue(row["official_roster_included"])
        self.assertFalse(row["inclusion_review_required"])

    def test_uwa_publication_profile(self):
        html = """
        <div class="page-section-header"><h1>A reliable paper</h1></div>
        <table class="properties">
          <tr><th>Publication status</th><td>Published - 2024</td></tr>
          <tr><th>Journal</th><td>Accounting Review</td></tr>
          <tr><th>ISSN</th><td>0001-4826</td></tr>
        </table>
        <a href="https://doi.org/10.1000/ABC.1">DOI</a>
        <div class="rendering_researchoutput_bibtex">
          @article{x, author = "Jane Example and John Example and A Third and A Fourth", publisher = "Example Press"}
        </div>
        <div class="rendering_researchoutput_associatespersonsclassifiedportal">
          <a rel="Person">Jane Example</a><a rel="Person">John Example</a>
        </div>
        <p class="type">Research output: Contribution to journal › Book/Film/Article review › peer-review</p>
        """
        row = uwa._parse_publication(
            html,
            "https://research-repository.uwa.edu.au/en/publications/a-reliable-paper/",
        )
        self.assertEqual(row["type"], "Journal Article")
        self.assertEqual(row["doi"], "10.1000/abc.1")
        self.assertEqual(row["issns"], ["0001-4826"])
        self.assertEqual(row["n_authors"], 4)
        self.assertEqual(row["publisher"], "Example Press")

    def test_unimelb_minerva_item(self):
        wrapper = {
            "_embedded": {"indexableObject": {
                "type": "item",
                "uuid": "abc",
                "handle": "11343/123",
                "metadata": {
                    "dc.title": [{"value": "A Minerva paper"}],
                    "dc.date.issued": [{"value": "2023-04-01"}],
                    "dc.identifier.doi": [{"value": "https://doi.org/10.1000/XYZ"}],
                    "dc.identifier.issn": [{"value": "1234-567X"}],
                    "dc.contributor.author": [{"value": "Example, Jane"}, {"value": "Other, John"}],
                    "melbourne.internal.authorids": [{"value": "Example, Jane; 12345; 0000-0002-1825-0097"}],
                    "melbourne.source.title": [{"value": "Journal of Examples"}],
                    "dc.type": [{"value": "Journal Article"}],
                },
            }},
        }
        row = unimelb._parse_item(wrapper)
        self.assertEqual(row["type"], "Journal Article")
        self.assertEqual(row["year"], "2023")
        self.assertEqual(row["doi"], "10.1000/xyz")
        self.assertEqual(row["n_authors"], 2)
        self.assertEqual(row["internal_authors"][0]["internal_id"], "12345")

    def test_unimelb_staff_directory(self):
        html = """
        <table><tr>
          <td><h5><a href="https://findanexpert.unimelb.edu.au/profile/1">Professor Jane Example</a></h5>
              <p>Professor</p></td>
          <td>Accounting</td><td><em>Department of Accounting</em></td>
        </tr></table>
        """
        rows = unimelb._staff_from_live_html(html, "Accounting")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name_clean"], "Jane Example")
        self.assertEqual(rows[0]["level_code"], "E")
        self.assertTrue(rows[0]["official_roster_included"])

    def test_unimelb_identity_search_uses_exact_author_filter(self):
        url = unimelb._identity_search_url({"repository_author_name": "Biddle, Gary"}, 0)
        self.assertIn("f.author=Biddle%2C+Gary%2Cequals", url)
        self.assertIn("dsoType=item", url)
        self.assertNotIn("query=", url)

    def test_verified_identity_override_requires_same_person_and_profile(self):
        person = {
            "name_clean": "Jane Example", "discipline": "Finance",
            "profile_url": "https://findanexpert.unimelb.edu.au/profile/1",
            "source_id": None, "orcid": None,
        }
        override = {
            "name": "Jane Example", "discipline": "Finance",
            "profile_url": person["profile_url"],
            "repository_author_name": "Example, Jane",
            "internal_id": "12345", "orcid": "", "evidence_url": "https://example/evidence",
        }
        identity = unimelb._build_identities([person], [], [override])[0]
        self.assertEqual(identity["confidence"], "high")
        self.assertEqual(identity["identity_source"], "manual_verified_override")
        self.assertEqual(person["source_id"], "12345")

        other_person = {**person, "name_clean": "Another Person", "source_id": None}
        ignored = unimelb._build_identities([other_person], [], [override])[0]
        self.assertEqual(ignored["confidence"], "none")

    def test_title_and_type_normalisation(self):
        self.assertEqual(uwa.rank("Sir Example Chair in Finance"), "Professor")
        self.assertEqual(unimelb.normalize_type("PhD thesis"), "Thesis")
        self.assertEqual(uwa.normalize_type("Research output: Working paper › Preprint"), "Preprint")

    def test_export_keeps_official_staff_without_publications_by_default(self):
        from export import export

        person = {
            "name": "Professor Jane Example",
            "name_clean": "Jane Example",
            "university": "University of Melbourne",
            "discipline": "Accounting",
            "profile_url": "https://findanexpert.unimelb.edu.au/profile/1",
            "title_clean": "Professor",
            "level_code": "E",
        }
        with tempfile.TemporaryDirectory() as directory:
            tables = export([person], [], out_dir=Path(directory), verbose=False)
        self.assertEqual([row["name"] for row in tables["staff"]], ["Jane Example"])


if __name__ == "__main__":
    unittest.main()
