"""Monash adapter applies data/monash_identity_overrides.csv on every run.

These corrections used to live only in the standalone monash_scraper.py, so a
normal `run.py --uni monash` silently ignored them and put Christine Brown's
ORCID back on Kym Brown.
"""

from base_scrapers import monash


KYM_URL = "https://research.monash.edu/en/persons/kym-brown/"
CHRISTINE_ORCID = "0000-0002-0306-2982"
KYM_ORCID = "0000-0003-2725-6236"


def record(name, url, orcid=None):
    return {"name_clean": name, "profile_url": url, "orcid": orcid}


def test_real_overrides_file_is_loaded():
    overrides = monash.load_identity_overrides()
    kym = next(o for o in overrides if o["name"] == "Kym Brown")
    assert kym["orcid"] == KYM_ORCID


def test_override_replaces_scraped_orcid():
    """The profile page offered Christine's ORCID; the reviewed one wins."""
    people = [record("Kym Brown", KYM_URL, orcid=CHRISTINE_ORCID)]
    overrides = [{
        "name": "Kym Brown",
        # The file has no trailing slash; scraped URLs do.
        "profile_url": KYM_URL.rstrip("/"),
        "orcid": KYM_ORCID,
        "publication_name": "",
    }]

    assert monash.apply_identity_overrides(people, overrides) == 1
    assert people[0]["orcid"] == KYM_ORCID


def test_falls_back_to_name_when_url_differs():
    people = [record("Kym Brown", "https://research.monash.edu/en/persons/k-brown/")]
    overrides = [{"name": "Kym Brown", "profile_url": KYM_URL, "orcid": KYM_ORCID,
                  "publication_name": ""}]

    monash.apply_identity_overrides(people, overrides)

    assert people[0]["orcid"] == KYM_ORCID


def test_unmatched_records_are_left_alone():
    people = [record("Someone Else", "https://research.monash.edu/en/persons/x/", "0000-0000-0000-0001")]
    overrides = [{"name": "Kym Brown", "profile_url": KYM_URL, "orcid": KYM_ORCID,
                  "publication_name": ""}]

    assert monash.apply_identity_overrides(people, overrides) == 0
    assert people[0]["orcid"] == "0000-0000-0000-0001"


def test_publication_name_without_orcid_skips_name_search(monkeypatch):
    """John Chu publishes as "Zhu, Z."; searching his name finds someone else."""
    def fail(*args, **kwargs):
        raise AssertionError("OpenAlex should not be queried")

    monkeypatch.setattr(monash, "_fetch_pubs_openalex", fail)
    person = record("John Chu", "https://research.monash.edu/en/persons/john-chu/")
    monash.apply_identity_overrides([person], [{
        "name": "John Chu", "profile_url": "", "orcid": "", "publication_name": "Zhu, Z.",
    }])

    assert monash._openalex_pubs(person) == []


def test_publication_name_with_orcid_still_fetches(monkeypatch):
    """A verified ORCID is safe to use even when the name search is not."""
    calls = []
    monkeypatch.setattr(monash, "_fetch_pubs_openalex",
                        lambda name, orcid=None, profile_pub_count=0: calls.append(orcid) or [])
    person = record("John Chu", "https://research.monash.edu/en/persons/john-chu/")
    monash.apply_identity_overrides([person], [{
        "name": "John Chu", "profile_url": "", "orcid": "0000-0001-2345-6789",
        "publication_name": "Zhu, Z.",
    }])

    monash._openalex_pubs(person)

    assert calls == ["0000-0001-2345-6789"]


def test_missing_file_means_no_overrides(tmp_path):
    assert monash.load_identity_overrides(tmp_path / "absent.csv") == []
