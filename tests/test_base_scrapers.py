"""Regression tests for the UWA and UniMelb base adapters."""

from __future__ import annotations

import importlib.util
import inspect
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


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

    def test_unimelb_generates_minerva_family_first_author_name(self):
        names = unimelb.repository_author_names("Tongqing (Tony) Ding")
        self.assertIn("Ding, Tongqing", names)
        self.assertIn("Ding, Tony", names)

    def test_unimelb_can_discover_identity_outside_department_seed(self):
        person = {
            "name_clean": "Tongqing (Tony) Ding", "discipline": "Accounting",
            "profile_url": "https://findanexpert.unimelb.edu.au/profile/1",
            "source_id": None, "orcid": None,
        }
        wrapper = {
            "_embedded": {"indexableObject": {
                "type": "item", "uuid": "abc", "metadata": {
                    "dc.title": [{"value": "A paper"}],
                    "melbourne.internal.authorids": [{
                        "value": "Ding, Tongqing; 12345; 0000-0001-0000-0001"
                    }],
                },
            }},
        }

        class Client:
            def get_json(self, url):
                if "Ding%2C+Tongqing" in url:
                    return {"_embedded": {"searchResult": {
                        "page": {"totalPages": 1},
                        "_embedded": {"objects": [wrapper]},
                    }}}
                return {"_embedded": {"searchResult": {
                    "page": {"totalPages": 1}, "_embedded": {"objects": []},
                }}}

        identity = {
            "person": person, "confidence": "none", "candidate_count": 0,
            "internal_id": "", "orcid": "", "repository_author_name": "",
        }
        stats = unimelb._resolve_missing_minerva_identities(
            Client(), [identity], max_workers=1, verbose=False
        )
        self.assertEqual(identity["confidence"], "high")
        self.assertEqual(person["source_id"], "12345")
        self.assertEqual(person["orcid"], "0000-0001-0000-0001")
        self.assertEqual(stats["resolved"], 1)

    def test_unimelb_parenthesised_nickname_does_not_block_identity_match(self):
        person = {
            "name_clean": "Tongqing (Tony) Ding", "discipline": "Accounting",
            "profile_url": "https://findanexpert.unimelb.edu.au/profile/1",
            "source_id": None, "orcid": None,
        }
        seed = [{"internal_authors": [{
            "name": "Ding, Tongqing", "internal_id": "12345", "orcid": "", "raw": "Ding, Tongqing; 12345",
        }]}]
        identity = unimelb._build_identities([person], seed)[0]
        self.assertEqual(identity["confidence"], "high")
        self.assertEqual(identity["internal_id"], "12345")

    def test_unimelb_openalex_identity_requires_name_and_institution(self):
        person = {
            "name_clean": "Tongqing (Tony) Ding", "discipline": "Accounting",
            "profile_url": "https://findanexpert.unimelb.edu.au/profile/1",
            "source_id": None, "orcid": None,
        }
        response = {"results": [
            {
                "id": "https://openalex.org/A1", "display_name": "Tongqing Ding",
                "display_name_alternatives": [], "orcid": "https://orcid.org/0000-0001-0000-0001",
                "last_known_institutions": [{"ror": "https://ror.org/01ej9dk98"}], "affiliations": [],
            },
            {
                "id": "https://openalex.org/A2", "display_name": "Tongqing Ding",
                "display_name_alternatives": [], "orcid": "https://orcid.org/0000-0002-0000-0002",
                "last_known_institutions": [{"ror": "https://ror.org/not-unimelb"}], "affiliations": [],
            },
        ]}
        with patch.object(unimelb, "cached_get", return_value=response):
            stats = unimelb._add_openalex_ids([person], verbose=False)
        self.assertEqual(person["openalex_author_ids"], ["A1"])
        self.assertEqual(person["orcid"], "0000-0001-0000-0001")
        self.assertEqual(stats["resolved"], 1)

    def test_unimelb_orcid_identity_requires_name_and_institution(self):
        person = {
            "name_clean": "Michelle Sabe", "discipline": "Accounting",
            "profile_url": "https://fbe.unimelb.edu.au/michelle-sabe",
            "source_id": None, "orcid": None,
        }
        response = {"expanded-result": [
            {
                "orcid-id": "0009-0000-2679-0205", "given-names": "Michelle",
                "family-names": "Sabe", "institution-name": ["The University of Melbourne"],
            },
            {
                "orcid-id": "0000-0001-9999-9999", "given-names": "Michelle",
                "family-names": "Sabe", "institution-name": ["Another University"],
            },
        ]}
        with patch.object(unimelb, "cached_get", return_value=response):
            stats = unimelb._add_orcid_ids([person], verbose=False)
        self.assertEqual(person["orcid"], "0009-0000-2679-0205")
        self.assertEqual(
            person["orcid_identity_status"],
            "verified_exact_name_and_unimelb_affiliation",
        )
        self.assertEqual(stats["resolved"], 1)

    def test_unimelb_orcid_without_unimelb_affiliation_is_not_guessed(self):
        person = {
            "name_clean": "Rosemary Addis", "discipline": "Accounting",
            "profile_url": "https://fbe.unimelb.edu.au/rosemary-addis",
            "source_id": None, "orcid": None,
        }
        response = {"expanded-result": [{
            "orcid-id": "0000-0001-9466-4470", "given-names": "Rosemary",
            "family-names": "Addis", "institution-name": [],
        }]}
        with patch.object(unimelb, "cached_get", return_value=response):
            stats = unimelb._add_orcid_ids([person], verbose=False)
        self.assertIsNone(person["orcid"])
        self.assertEqual(person["orcid_identity_status"], "not_found")
        self.assertEqual(stats["not_found"], 1)

    def test_unimelb_unique_extra_given_name_can_be_verified(self):
        person = {
            "name_clean": "Flora Kuang", "discipline": "Accounting",
            "profile_url": "https://findanexpert.unimelb.edu.au/profile/743113",
            "source_id": None, "orcid": None,
        }
        response = {"results": [{
            "id": "https://openalex.org/A5049443262", "display_name": "Yu Flora Kuang",
            "display_name_alternatives": ["Flora Yu Kuang"],
            "orcid": "https://orcid.org/0000-0001-5095-4118",
            "last_known_institutions": [{"ror": "https://ror.org/01ej9dk98"}],
            "affiliations": [],
        }]}
        with patch.object(unimelb, "cached_get", return_value=response):
            stats = unimelb._add_openalex_ids([person], verbose=False)
        self.assertEqual(person["openalex_author_ids"], ["A5049443262"])
        self.assertEqual(
            person["openalex_identity_status"],
            "verified_one_token_name_extension_and_unimelb_affiliation",
        )
        self.assertEqual(stats["resolved"], 1)

    def test_unimelb_openalex_ambiguous_people_are_not_guessed(self):
        person = {
            "name_clean": "Alex Smith", "discipline": "Finance",
            "profile_url": "https://findanexpert.unimelb.edu.au/profile/2",
            "source_id": None, "orcid": None,
        }
        response = {"results": [
            {
                "id": "https://openalex.org/A1", "display_name": "Alex Smith",
                "orcid": "https://orcid.org/0000-0001-0000-0001",
                "last_known_institutions": [{"ror": "https://ror.org/01ej9dk98"}], "affiliations": [],
            },
            {
                "id": "https://openalex.org/A2", "display_name": "Alex Smith",
                "orcid": "https://orcid.org/0000-0002-0000-0002",
                "last_known_institutions": [],
                "affiliations": [{"institution": {"ror": "https://ror.org/01ej9dk98"}}],
            },
        ]}
        with patch.object(unimelb, "cached_get", return_value=response):
            stats = unimelb._add_openalex_ids([person], verbose=False)
        self.assertEqual(person["openalex_author_ids"], [])
        self.assertIsNone(person["orcid"])
        self.assertEqual(person["openalex_identity_status"], "ambiguous")
        self.assertEqual(stats["ambiguous"], 1)

    def test_unimelb_openalex_lookup_stops_after_repeated_failures(self):
        people = [{
            "name_clean": f"Person {index}", "discipline": "Finance",
            "profile_url": f"https://findanexpert.unimelb.edu.au/profile/{index}",
            "source_id": None, "orcid": None,
        } for index in range(4)]
        with patch.object(unimelb, "cached_get", side_effect=ConnectionError("offline")) as lookup:
            stats = unimelb._add_openalex_ids(people, verbose=False)
        self.assertEqual(lookup.call_count, 3)
        self.assertEqual(stats["aborted_after_repeated_errors"], 1)
        self.assertEqual(people[-1]["openalex_identity_status"], "not_attempted_after_repeated_errors")

    def test_unimelb_approved_openalex_override_survives_automatic_lookup(self):
        person = {
            "name_clean": "Jane Example", "discipline": "Finance",
            "profile_url": "https://findanexpert.unimelb.edu.au/profile/1",
            "source_id": None, "orcid": None,
        }
        override = {
            "name": "Jane Example", "discipline": "Finance",
            "profile_url": person["profile_url"],
            "openalex_author_ids": ["A123"], "orcid": "",
            "evidence_url": "https://openalex.org/A123",
        }
        self.assertEqual(unimelb._apply_manual_retrieval_overrides([person], [override]), 1)
        with patch.object(unimelb, "cached_get") as lookup:
            unimelb._add_openalex_ids([person], verbose=False)
        lookup.assert_not_called()
        self.assertEqual(person["openalex_author_ids"], ["A123"])
        self.assertEqual(person["identity_source"], "manual_verified_override")

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
