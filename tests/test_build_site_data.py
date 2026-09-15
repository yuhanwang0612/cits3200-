from pathlib import Path

import build_site_data as site_data


def test_normalisers_preserve_missing_and_canonicalise_identifiers():
    assert site_data.normalise_doi("https://doi.org/10.1000/ABC. ") == "10.1000/abc"
    assert site_data.normalise_doi("") is None
    assert site_data.normalise_rank("A *") == "A*"
    assert site_data.normalise_rank("unranked") == "none"
    assert site_data.normalise_rank("") is None
    assert site_data.normalise_field("Accounting & Finance", "Professor of Finance") == "Accounting & Finance"
    assert site_data.normalise_field(None, "Professor of Accounting") is None


def test_publication_metrics_are_article_weighted():
    publications = [
        {"quality_rank": "A*", "impact_factor": 6.0, "impact_factor_5yr": 7.0},
        {"quality_rank": "B", "impact_factor": 2.0, "impact_factor_5yr": None},
        {"quality_rank": None, "impact_factor": None, "impact_factor_5yr": None},
    ]
    metrics = site_data.metric_summary(publications)
    assert metrics == {
        "publication_count": 3,
        "abdc_ranked_count": 2,
        "top_tier_count": 1,
        "avg_jif": 4.0,
        "avg_jif_5": 7.0,
    }


def test_unknown_rank_is_not_reported_as_zero():
    metrics = site_data.metric_summary([
        {"quality_rank": None, "impact_factor": None, "impact_factor_5yr": None},
    ])
    assert metrics["abdc_ranked_count"] is None
    assert metrics["top_tier_count"] is None


def test_real_repository_sources_build_and_reconcile():
    sources = site_data.discover_sources()
    assert {source["key"] for source in sources} == set(site_data.UNIVERSITIES)

    universities, researchers, publications, source_meta, warnings = site_data.build_dataset(sources)
    site_data.validate_dataset(universities, researchers, publications)

    assert len(universities) == 8
    assert len(researchers) > 500
    assert sum(len(rows) for rows in publications.values()) > 10_000
    by_code = {row["code"]: row for row in universities}
    assert by_code["USYD"]["researcher_count"] is None
    assert by_code["USYD"]["coverage"]["staff"] == "missing"
    assert not any(row["university_code"] == "USYD" for row in researchers)
    assert by_code["ADL"]["researcher_count"] == 129
    assert by_code["ADL"]["accounting"]["researcher_count"] is None
    assert by_code["ADL"]["finance"]["researcher_count"] is None
    assert by_code["ADL"]["coverage"]["discipline_split"] == "missing"
    assert by_code["UWA"]["accounting"]["publication_count"] > 0
    assert by_code["UWA"]["finance"]["publication_count"] > 0
    assert any(item["source_kind"] == "canonical" for item in source_meta)
    assert any("USYD" in warning for warning in warnings)


def test_university_page_uses_precomputed_real_metrics():
    html = (Path(site_data.__file__).parent / "site" / "universities.html").read_text()
    assert "loadJSON('data/universities.json')" in html
    assert "loadJSON('data/researchers.json')" not in html
    assert "u.top_tier_count" in html
