"""export.canonical_journal_name: one spelling per journal across universities."""

import pytest

from export import canonical_journal_name


def name(journal, abdc_title=None, scimago_title=None):
    return canonical_journal_name({"journal": journal, "abdc_title": abdc_title,
                                   "scimago_title": scimago_title})


def test_abdc_title_wins():
    assert name("Accounting & Finance", abdc_title="Accounting and Finance") == "Accounting and Finance"


@pytest.mark.parametrize("raw", ["PLoS ONE", "Plos One", "PLOS ONE", "PLoS One"])
def test_scimago_spelling_when_it_is_the_same_journal(raw):
    assert name(raw, scimago_title="PLoS ONE") == "PLoS ONE"


def test_scimago_title_never_renames_a_journal():
    # a wrong ISSN (here an SSRN copy) must not rename the journal
    assert name("Journal of Finance", scimago_title="SSRN Electronic Journal") == "Journal of Finance"


@pytest.mark.parametrize("raw, expected", [
    ("The Journal of Business", "Journal of Business"),
    ("JOURNAL OF BUSINESS", "Journal of Business"),
    ("Journal of Business", "Journal of Business"),
    ("Corporate Ownership & Control", "Corporate Ownership and Control"),
    ("Corporate ownership and control", "Corporate Ownership and Control"),
    ("Strategic finance", "Strategic Finance"),
    ("ISACA journal", "ISACA Journal"),
    ("Revizor: revija o reviziji", "Revizor: Revija o Reviziji"),
    ("Revizor: Revija o reviziji", "Revizor: Revija o Reviziji"),
])
def test_tidy_rule_gives_one_spelling(raw, expected):
    assert name(raw) == expected


def test_blank_stays_blank():
    assert name(None) is None and name("") == ""
