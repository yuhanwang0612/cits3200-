from core.clean import clean_pubs


def test_doi_url_tracking_parameters_are_removed():
    row = {
        "name": "Jane Example",
        "title": "A paper",
        "year": "2020",
        "type": "Journal Article",
        "journal": "A Journal",
        "doi": "10.1108/s0731-9053(2010)0000026009.html?utm_source=repec",
    }

    cleaned = clean_pubs([row])

    assert cleaned[0]["doi"] == "10.1108/s0731-9053(2010)0000026009"


def test_confirmed_namesake_publication_is_excluded():
    pubs = [{
        "name": "Bryan Lim",
        "title": "Travel vaccination recommendations and infection risk",
        "doi": "10.1093/JTM/TAZ034",
        "type": "Journal Article",
        "journal": "Journal of Travel Medicine",
        "year": "2019",
    }]

    assert clean_pubs(pubs) == []


def test_exclusion_is_scoped_to_the_named_researcher():
    pub = {
        "name": "A Different Bryan Lim",
        "title": "Travel vaccination recommendations and infection risk",
        "doi": "10.1093/jtm/taz034",
        "type": "Journal Article",
        "journal": "Journal of Travel Medicine",
        "year": "2019",
    }

    assert clean_pubs([pub]) == [pub]
