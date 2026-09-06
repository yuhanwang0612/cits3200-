"""Turning a job title into an academic level.

Two problems this solves. Some people's rank is not in their job title at
all — an endowed chair name carries no rank word, and a convenorship is a
role rather than a level. And "Dr" is a qualification, not a rank, so it
must not be read as one.
"""

import re

PREFIX = re.compile(
    r"^(Associate Professor|Emeritus Professor|Professor|Dr|Mr|Mrs|Ms|Miss"
    r"|A/Prof|Prof|Assoc\.? Prof\.?)\.?\s+",
    re.IGNORECASE,
)

# Trailing parenthetical, e.g. "Associate Lecturer (Finance)".
SUFFIX = re.compile(r"\s*\([^)]*\)\s*$")

# Order matters: compound titles must be tested before their components,
# or "Associate Lecturer" matches the "Lecturer" pattern first.
LADDER = [
    ("Emeritus Professor",     r"emeritus prof"),
    ("Associate Professor",    r"associate prof|a/prof"),
    ("Associate Lecturer",     r"associate lecturer"),
    ("Senior Lecturer",        r"senior lecturer"),
    ("Senior Research Fellow", r"senior research fellow"),
    ("Research Fellow",        r"research fellow"),
    ("Teaching Associate",     r"teaching associate"),
    ("Professor",              r"\bprofessor\b|chair in"),
    ("Lecturer",               r"\blecturer\b"),
]

# Australian academic levels. Teaching-only and casual appointments are
# deliberately absent — they sit outside this ladder and map to None.
LEVEL = {
    "Associate Lecturer":     "A",
    "Lecturer":               "B",
    "Fellow":                 "B",
    "Research Fellow":        "B",
    "Senior Lecturer":        "C",
    "Senior Fellow":          "C",
    "Senior Research Fellow": "C",
    "Associate Professor":    "D",
    "Reader":                 "D",
    "Professor":              "E",
    "Professorial Fellow":    "E",
    "Professor Emeritus":     "E",
    "Emeritus Professor":     "E",
}

# Honorifics that say nothing about rank.
_QUALIFICATIONS = {"dr", "mr", "mrs", "ms", "miss"}


def split_prefix(name):
    """('Associate Professor Jane Doe') -> ('Jane Doe', 'Associate Professor')"""
    m = PREFIX.match(name or "")
    return PREFIX.sub("", name or "").strip(), (m.group(1) if m else None)


def rank(title, prefix=None):
    """Normalise a job title onto the ladder.

    Falls back to the name prefix when the title carries no rank word, which
    is how an endowed-chair title still resolves to Professor. A prefix that
    is only a qualification is ignored.
    """
    for label, pat in LADDER:
        if title and re.search(pat, title, re.I):
            return label
    if prefix and prefix.lower() not in _QUALIFICATIONS:
        return prefix
    return None


def level(rank_label):
    """Academic level A–E, or None for roles outside the ladder."""
    return LEVEL.get(rank_label)
