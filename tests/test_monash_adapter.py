"""Regression tests for Monash Pure attribution and profile parsing."""

from base_scrapers import monash


RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><item>
  <title>A reliable finance paper</title>
  <link>https://research.monash.edu/en/publications/a-reliable-finance-paper/</link>
  <description>&lt;div class="rendering"&gt;
    &lt;h3 class="title"&gt;A reliable finance paper&lt;/h3&gt;
    Other, A., &lt;a rel="Person"&gt;Example, J.&lt;/a&gt; &amp;amp; Third, B.,
    &lt;span class="date"&gt;Aug 2023&lt;/span&gt;,
    &lt;span class="journal"&gt;In: Journal of Finance.&lt;/span&gt;
    &lt;p class="type"&gt;Research output: Contribution to journal › Article › Research › peer-review&lt;/p&gt;
  &lt;/div&gt;</description>
</item></channel></rss>"""


def test_profile_section_is_not_used_as_job_title():
    assert monash._academic_title(
        "External positions Visiting Research Professor, Example University"
    ) is None
    assert monash._academic_title("Associate Professor") == "Associate Professor"


def test_personal_pure_feed_supplies_attribution():
    rows = monash._parse_pure_rss(RSS, "Jane Example", "jane-example")
    assert len(rows) == 1
    row = rows[0]
    assert row["name"] == "Jane Example"
    assert row["source_id"] == "jane-example"
    assert row["source"] == "Monash Pure"
    assert row["type"] == "Journal Article"
    assert row["year"] == "2023"
    assert row["journal"] == "Journal of Finance"
    assert row["n_authors"] == 3


def test_openalex_only_enriches_pure_titles_and_cannot_add_namesakes():
    pure = monash._parse_pure_rss(RSS, "Jane Example", "jane-example")
    openalex = [
        {
            "title": "A reliable finance paper",
            "year": 2023,
            "doi": "10.1000/finance",
            "issns": ["1234-5678"],
            "n_authors": 3,
            "authors": "Other, A.; Example, J.; Third, B.",
            "journal": "Journal of Finance",
            "source": "OpenAlex",
        },
        {
            "title": "A namesake's unrelated medical paper",
            "year": 1915,
            "doi": "10.1000/wrong",
            "issns": ["9999-9999"],
            "n_authors": 1,
            "authors": "Example, J.",
            "journal": "Unrelated Medical Journal",
            "source": "OpenAlex",
        },
    ]

    rows = monash._merge_pure_with_openalex(pure, openalex)
    assert len(rows) == 1
    assert rows[0]["doi"] == "10.1000/finance"
    assert rows[0]["source"] == "Monash Pure"
    assert all(row["title"] != "A namesake's unrelated medical paper" for row in rows)


def test_pure_pagination_uses_the_parameter_order_monash_accepts(monkeypatch):
    seen = []

    def get(url):
        seen.append(url)
        if "page=1" in url:
            return RSS.replace(
                "A reliable finance paper", "A second finance paper"
            ).replace(
                "a-reliable-finance-paper", "a-second-finance-paper"
            )
        return RSS

    monkeypatch.setattr(monash, "_request_text", get)
    rows = monash._fetch_pure_publications({
        "profile_url": "https://research.monash.edu/en/persons/jane-example/",
        "name_clean": "Jane Example",
        "source_id": "jane-example",
        "_pub_count": 2,
    })

    assert len(rows) == 2
    assert seen == [
        "https://research.monash.edu/en/persons/jane-example/publications/?format=rss",
        "https://research.monash.edu/en/persons/jane-example/publications/?page=1&format=rss",
    ]


def test_openalex_provenance_is_not_the_journal_name():
    work = {
        "title": "A paper",
        "publication_year": 2024,
        "doi": "https://doi.org/10.1000/example",
        "type": "article",
        "primary_location": {
            "source": {"display_name": "Journal of Examples", "issn": ["1234-5678"]}
        },
        "authorships": [],
    }

    # Exercise the same record construction through a tiny response fixture.
    class Response:
        status_code = 200

        def json(self):
            return {"results": [work], "meta": {"next_cursor": None}}

    calls = iter([
        Response(),  # author lookup
        Response(),  # works lookup; author response shape is patched below
    ])

    author_response = Response()
    author_response.json = lambda: {"results": [{"id": "https://openalex.org/A1",
                                                 "orcid": "https://orcid.org/0000-0000-0000-0000"}]}
    calls = iter([author_response, Response()])
    original = monash._oa_get
    monash._oa_get = lambda *args, **kwargs: next(calls)
    original_sleep = monash.time.sleep
    monash.time.sleep = lambda *_: None
    try:
        rows = monash._fetch_pubs_openalex(
            "Jane Example", orcid="0000-0000-0000-0000"
        )
    finally:
        monash._oa_get = original
        monash.time.sleep = original_sleep

    assert rows[0]["source"] == "OpenAlex"
    assert rows[0]["journal"] == "Journal of Examples"
