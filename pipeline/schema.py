"""Shared validation contract for raw and published records."""

from __future__ import annotations

import re
from typing import Any


STAFF_REQUIRED = ("name", "name_clean", "university", "discipline", "profile_url")
PUB_REQUIRED = ("name", "university", "discipline", "title", "type", "source", "publication_id")
DISCIPLINES = {"Accounting", "Finance"}
TYPES = {
    "Journal Article", "Preprint", "Conference Paper", "Book", "Book Chapter",
    "Thesis", "Research Report", "Working Paper", "Data Collection",
    "Newspaper Article", "Other",
}


def validate(staff: list[dict[str, Any]], publications: list[dict[str, Any]]) -> list[str]:
    """Return every contract violation; an empty result is valid."""
    problems: list[str] = []
    staff_keys: set[tuple[str, str, str]] = set()
    names: set[tuple[str, str, str]] = set()

    for index, record in enumerate(staff):
        missing = [field for field in STAFF_REQUIRED if not record.get(field)]
        if missing:
            problems.append(f"staff[{index}] missing {missing}")
            continue
        key = (record["university"], record["discipline"], record["profile_url"])
        if key in staff_keys:
            problems.append(f"staff[{index}] duplicate profile relationship {key}")
        staff_keys.add(key)
        names.add((record["university"], record["discipline"], record["name_clean"]))
        if record["discipline"] not in DISCIPLINES:
            problems.append(f"staff[{index}] invalid discipline {record['discipline']!r}")

    publication_keys: set[tuple[str, str, str, str]] = set()
    for index, record in enumerate(publications):
        missing = [field for field in PUB_REQUIRED if not record.get(field)]
        if missing:
            problems.append(f"publication[{index}] missing {missing}")
            continue
        owner = (record["university"], record["discipline"], record["name"])
        if owner not in names:
            problems.append(f"publication[{index}] has no matching staff record: {owner}")
        key = (*owner, record["publication_id"])
        if key in publication_keys:
            problems.append(f"publication[{index}] duplicate researcher-publication relationship {key}")
        publication_keys.add(key)
        if record["discipline"] not in DISCIPLINES:
            problems.append(f"publication[{index}] invalid discipline {record['discipline']!r}")
        if record["type"] not in TYPES:
            problems.append(f"publication[{index}] invalid type {record['type']!r}")
        # The agreed team schema makes year nullable.  This is important for
        # legitimate forthcoming/in-press outputs; an unknown date must stay
        # explicitly empty rather than being invented.
        if record.get("year") is not None and not re.fullmatch(r"(?:18|19|20)\d{2}", str(record["year"])):
            problems.append(f"publication[{index}] invalid year {record['year']!r}")

    return problems
