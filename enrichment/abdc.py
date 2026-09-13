"""ABDC Journal Quality List — quality_rank by ISSN.

Two quirks in the spreadsheet: ISSNs carry trailing tabs and ratings carry
trailing spaces, so both need stripping or nothing matches at all.

The canonical ABDC title is also written back as `abdc_title`, and the
export uses it as the journal key. That collapses print/online ISSN
variants and "and" vs "&" spellings onto one journal row.
"""

import pandas as pd

from core.config import (ABDC_EDITION, ABDC_FILE, ABDC_HEADER,
                         ABDC_RATING_COL, ABDC_SHEET)

_lookup = None


def _build():
    global _lookup
    if _lookup is not None:
        return _lookup

    df = pd.read_excel(ABDC_FILE, sheet_name=ABDC_SHEET, header=ABDC_HEADER)
    df = df.loc[:, ~df.columns.astype(str).str.startswith("Unnamed")]
    df.columns = [str(c).strip() for c in df.columns]

    _lookup = {}
    for _, row in df.iterrows():
        rating = str(row[ABDC_RATING_COL]).strip()
        title = str(row["Journal Title"]).strip()
        for col in ("ISSN", "ISSNOnline"):
            v = str(row[col]).strip()
            if v and v.lower() != "nan":
                _lookup[v] = {"rating": rating, "title": title}

    # A wrong header row or sheet name yields a lookup full of junk and
    # silently unrates everything, so fail loudly instead.
    if len(_lookup) < 2000:
        raise RuntimeError(
            f"ABDC lookup has only {len(_lookup)} entries — check "
            f"ABDC_SHEET, ABDC_HEADER and ABDC_RATING_COL in core/config.py")
    return _lookup


def enrich(pubs, verbose=True):
    lookup = _build()
    for x in pubs:
        hit = next((lookup[i] for i in (x.get("issns") or []) if i in lookup), None)
        x["abdc"] = hit["rating"] if hit else None
        x["abdc_title"] = hit["title"] if hit else None
        x["abdc_edition"] = ABDC_EDITION if hit else None

    if verbose:
        arts = [x for x in pubs if x.get("type") == "Journal Article"]
        n = sum(1 for x in arts if x.get("abdc"))
        print(f"abdc: {n} of {len(arts)} journal articles rated "
              f"({len(lookup)} ISSNs in the {ABDC_EDITION} list)")
    return pubs
