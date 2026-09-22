"""The documentation page links to the full data dictionary, served from docs/."""

from pathlib import Path

import app as webapp

ROOT = Path(__file__).resolve().parents[1]


def test_data_dictionary_downloads_as_a_file():
    response = webapp.app.test_client().get("/downloads/data-dictionary.md")

    assert response.status_code == 200
    assert "attachment" in response.headers["Content-Disposition"]
    assert "G8-research-data-dictionary.md" in response.headers["Content-Disposition"]
    # The same file as the repo copy, not a separate one that can drift.
    assert response.data == (ROOT / "docs" / "DATA_DICTIONARY.md").read_bytes()


def test_documentation_page_has_column_guide_and_download_button():
    html = (ROOT / "site" / "documentation.html").read_text(encoding="utf-8")

    assert 'id="columns"' in html
    assert 'href="#columns"' in html
    assert 'href="/downloads/data-dictionary.md"' in html
