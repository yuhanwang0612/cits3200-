"""Scimago Journal Rank — SJR, quartile, h-index, citations per document.

Free and openly licensed, unlike JIF, and it covers journals outside
business and economics that ABDC does not rate at all.

File quirks: semicolon-delimited, European decimals (104,065 = 104.065),
and ISSNs stored without hyphens.
"""

import pandas as pd

from core.config import SCIMAGO_FILE, SCIMAGO_YEAR

_lookup = None


def _num(v):
    try:
        return float(str(v).replace(",", "."))
    except (TypeError, ValueError):
        return None


def _build():
    global _lookup
    if _lookup is not None:
        return _lookup

    df = pd.read_csv(SCIMAGO_FILE, sep=";")
    _lookup = {}
    for _, row in df.iterrows():
        entry = {
            "sjr": _num(row["SJR"]),
            "sjr_quartile": str(row["SJR Best Quartile"]).strip(),
            "h_index": row["H index"],
            "cites_per_doc_2y": _num(row["Citations / Doc. (2years)"]),
        }
        for i in str(row["Issn"]).split(","):
            i = i.strip()
            if i and i.lower() != "nan":
                _lookup[i] = entry

    if len(_lookup) < 10000:
        raise RuntimeError(
            f"Scimago lookup has only {len(_lookup)} entries — check the file")
    return _lookup


def enrich(pubs, verbose=True):
    lookup = _build()
    for x in pubs:
        hit = next((lookup[i.replace("-", "")] for i in (x.get("issns") or [])
                    if i.replace("-", "") in lookup), None)
        x["sjr"] = hit["sjr"] if hit else None
        x["sjr_quartile"] = hit["sjr_quartile"] if hit else None
        x["h_index"] = hit["h_index"] if hit else None
        x["cites_per_doc_2y"] = hit["cites_per_doc_2y"] if hit else None
        x["scimago_year"] = SCIMAGO_YEAR if hit else None

    if verbose:
        arts = [x for x in pubs if x.get("type") == "Journal Article"]
        n = sum(1 for x in arts if x.get("sjr") is not None)
        print(f"scimago: {n} of {len(arts)} journal articles matched")
    return pubs
