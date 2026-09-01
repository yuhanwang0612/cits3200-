"""Clarivate Journal Citation Reports — CITS3200 Group 20.

Fills the two columns the Scope of Work data dictionary asks for and that
nothing else can supply:

    impact_factor        Journal Impact Factor (JIF) for the edition year
    impact_factor_5yr    Five year JIF

Same shape as abdc.py and scimago.py: it reads the journal table, looks each
journal up by ISSN, and writes the values back. It is university agnostic, so
any of the eight universities can run it against their own publications file.

    set CLARIVATE_API_KEY=...            (Windows)
    export CLARIVATE_API_KEY=...         (macOS/Linux)

    python clarivate.py output/journals.csv
    python clarivate.py output/journals.csv --publications output/unsw_publications.csv

The key is read from the environment and nowhere else. It is never a command
line argument, because arguments end up in shell history, and never a file in
the repo, because files get committed.

Clarivate throttles at 5 requests per second, so this paces itself. 437 ISSNs
takes roughly two minutes.

If the response shape is not what this expects, run:

    python clarivate.py output/journals.csv --probe 0022-1082

which prints the raw JSON for one journal so the field names can be checked
against reality rather than guessed at.
"""

import argparse
import csv
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://api.clarivate.com/apis/wos-journals/v1"
KEY_ENV = "CLARIVATE_API_KEY"

# Clarivate documents a ceiling of 5 requests per second. Sitting just under it
# leaves room for the clock disagreeing slightly at their end.
MIN_INTERVAL = 0.25
MAX_RETRIES = 4
TIMEOUT = 30

ADDED_COLUMNS = ["impact_factor", "impact_factor_5yr", "clarivate_match_type", "jcr_year"]

MATCH_ISSN = "issn"
MATCH_EISSN = "eissn"
MATCH_NONE = "not-found"
MATCH_FAILED = "lookup-failed"

# The JIF lives at a different depth depending on which endpoint answered, and
# Clarivate has moved it between releases. Rather than pin one path and break
# silently when it moves, look for the first key that matches, at any depth.
# A wrong guess then shows up as a missing value, not as a wrong number.
JIF_KEYS = ("jif", "impactFactor", "journalImpactFactor")
# Confirmed against a live /journals/J_FINANC/reports/year/2025 response:
#   "metrics": {"impactMetrics": {"jif": "12.2", "jif5Years": 12.3, ...}}
# Note `jif` arrives as a string and `jif5Years` as a number, and note the
# plural. The first version of this looked for "jif5Year" and filled 0 of 571.
JIF5_KEYS = ("jif5Years", "jif5Year", "fiveYearJif", "jif5",
             "impactFactor5Year", "fiveYearImpactFactor")


class MissingKey(RuntimeError):
    """Raised when the environment variable is not set."""


class RateLimited(RuntimeError):
    """Raised when Clarivate keeps refusing after MAX_RETRIES."""


def api_key():
    key = os.environ.get(KEY_ENV, "").strip()
    if not key:
        raise MissingKey(
            f"{KEY_ENV} is not set.\n"
            f"  Windows : setx {KEY_ENV} \"your-key\"   (then open a new terminal)\n"
            f"  mac/Linux: export {KEY_ENV}='your-key'\n"
            "Do not put the key in a file inside the repository."
        )
    return key


_last_call = [0.0]


def request_json(path, params, key, retries=MAX_RETRIES):
    """One GET, paced and retried. Returns None if the lookup failed.

    None means "we do not know", which is different from an empty result
    meaning "Clarivate has no such journal". Conflating those two is how a
    transient network problem becomes a permanent wrong answer in the data.
    """
    wait = _last_call[0] + MIN_INTERVAL - time.monotonic()
    if wait > 0:
        time.sleep(wait)

    url = f"{BASE}{path}?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={"X-ApiKey": key,
                                                   "Accept": "application/json"})
    for attempt in range(retries):
        try:
            _last_call[0] = time.monotonic()
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            if error.code in (401, 403):
                # A bad key will not fix itself on retry, and hammering an
                # endpoint with a rejected key is how a key gets suspended.
                raise MissingKey(
                    f"Clarivate rejected the key ({error.code}). Check {KEY_ENV} "
                    "is the Journals API key and that the subscription is active."
                ) from None
            if error.code == 404:
                return {}
            if error.code in (429, 500, 502, 503, 504):
                time.sleep(2 ** attempt)
                continue
            return None
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            time.sleep(2 ** attempt)
    raise RateLimited(f"gave up on {path} after {retries} attempts")


def find_number(payload, names):
    """First numeric value stored under any of `names`, at any depth.

    Clarivate sometimes wraps the figure as {"value": 5.4} and sometimes gives
    it plainly, so both are accepted.
    """
    if isinstance(payload, dict):
        for name in names:
            if name in payload:
                value = payload[name]
                if isinstance(value, dict):
                    value = value.get("value")
                number = as_number(value)
                if number is not None:
                    return number
        for value in payload.values():
            found = find_number(value, names)
            if found is not None:
                return found
    elif isinstance(payload, list):
        for item in payload:
            found = find_number(item, names)
            if found is not None:
                return found
    return None


def as_number(value):
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "")
    if not text or text.upper() in ("N/A", "NA", "-"):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def records_from(payload):
    """The journal records in a response, whichever way it is wrapped."""
    if isinstance(payload, dict):
        for field in ("hits", "records", "journals", "data", "results"):
            value = payload.get(field)
            if isinstance(value, list):
                return value
        if payload.get("id") or payload.get("name"):
            return [payload]
    if isinstance(payload, list):
        return payload
    return []


ISSN_IN_TEXT = re.compile(r"\b(\d{4})-?(\d{3}[\dXx])\b")
TAGS = re.compile(r"<[^>]+>")


def issns_in(record):
    """Every ISSN a record carries, normalised to NNNN-NNNC.

    The search endpoint does not return an `issn` field at all. It returns
    where the query matched:

        "matches": [{"field": "issn", "value": ["<em>0022-1082</em>"]}]

    so the ISSN is in a value, wrapped in highlight tags, under a key called
    "value". Looking for keys named "issn" finds nothing, which is why the
    first version of this matched zero of 437 journals while reporting zero
    failures. Scanning the text for the ISSN pattern handles both that shape
    and a plain issn field, and the result is still checked against the exact
    ISSN we asked for, so a stray number cannot be mistaken for a match.
    """
    raw = json.dumps(record)
    # Tags are removed rather than replaced with a space, because a highlighter
    # is free to split the ISSN itself: `<em>0022</em>-1082`. A space there
    # would break the number in half. Both the stripped and the original text
    # are scanned so neither form is missed.
    return {f"{m.group(1)}-{m.group(2).upper()}"
            for text in (TAGS.sub("", raw), raw)
            for m in ISSN_IN_TEXT.finditer(text)}


def normalise_issn(value):
    """0022-1082 and 00221082 are the same ISSN. Anything else is not one.

    The shape has to be checked, not just the length: eight alphanumeric
    characters is also what "nonsense" is, and formatting that as an ISSN
    would create a join key that matches nothing and looks real.
    """
    digits = "".join(c for c in str(value).upper() if c.isalnum())
    if len(digits) != 8:
        return None
    if not digits[:7].isdigit():
        return None
    if not (digits[7].isdigit() or digits[7] == "X"):
        return None
    return f"{digits[:4]}-{digits[4:]}"


def latest_report_year(detail):
    """The newest JCR year this journal has a report for.

    The detail record lists them:

        "journalCitationReports": [{"year": 2025, "url": ...}, {"year": 2024...

    but not in a guaranteed order, so take the maximum rather than the first.
    Reading the first is what put 1997 and 2005 in jcr_year for fourteen
    journals, and a 1997 impact factor presented as current is a wrong number,
    not a missing one.
    """
    reports = detail.get("journalCitationReports") or []
    years = []
    for report in reports:
        value = as_number((report or {}).get("year"))
        if value and 1900 < value < 2100:
            years.append(int(value))
    return max(years) if years else None


def lookup(issn, key):
    """Return (impact_factor, impact_factor_5yr, jcr_year, match_type).

    Two calls, because the search endpoint carries no metrics. It answers with
    an id and the field the query matched, and the figures live on the journal
    itself:

        GET /journals?q=0022-1082   ->  {"hits": [{"id": "J_FINANC", ...}]}
        GET /journals/J_FINANC      ->  the impact metrics

    Matching stays on ISSN only. A title search would be one call instead of
    two and would silently attach the wrong journal's impact factor, which is
    the one error in this pipeline that nothing downstream could detect.
    """
    payload = request_json("/journals", {"q": issn, "limit": 10}, key)
    if payload is None:
        return None, None, None, MATCH_FAILED

    wanted = normalise_issn(issn)
    for record in records_from(payload):
        if wanted and wanted not in issns_in(record):
            continue

        journal_id = record.get("id")
        if not journal_id:
            return None, None, None, MATCH_NONE

        # /journals/{id} holds no metrics at all, only bibliographic detail and
        # the list of years a report exists for. Its one job here is telling us
        # which report to ask for.
        detail = request_json(f"/journals/{journal_id}", {}, key)
        if detail is None:
            # We know which journal it is but could not read it. That is a
            # failed lookup, not a journal without an impact factor.
            return None, None, None, MATCH_FAILED

        year = latest_report_year(detail)
        if year is None:
            # In JCR but with no report published: indexed, not rated.
            return None, None, None, MATCH_ISSN

        report = request_json(
            f"/journals/{journal_id}/reports/year/{year}", {}, key)
        if report is None:
            return None, None, None, MATCH_FAILED

        return (find_number(report, JIF_KEYS),
                find_number(report, JIF5_KEYS),
                year,
                MATCH_ISSN)

    return None, None, None, MATCH_NONE


def read_csv(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        return list(reader), list(reader.fieldnames or [])


def write_csv(path, fieldnames, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def enrich_journals(path, key, limit=None):
    rows, fieldnames = read_csv(path)
    for column in ADDED_COLUMNS:
        if column not in fieldnames:
            fieldnames.append(column)

    counts = {MATCH_ISSN: 0, MATCH_NONE: 0, MATCH_FAILED: 0, "no-issn": 0}
    done = 0

    for row in rows:
        issn = (row.get("issn") or row.get("issn_online")
                or row.get("issn_scimago") or "").strip()
        if not issn:
            row["clarivate_match_type"] = ""
            counts["no-issn"] += 1
            continue
        if limit is not None and done >= limit:
            break

        jif, jif5, year, match = lookup(issn, key)
        row["impact_factor"] = "" if jif is None else jif
        row["impact_factor_5yr"] = "" if jif5 is None else jif5
        row["jcr_year"] = "" if year is None else int(year)
        row["clarivate_match_type"] = match
        counts[match] = counts.get(match, 0) + 1
        done += 1

        if done % 25 == 0:
            print(f"  {done} looked up...", flush=True)

    write_csv(path, fieldnames, rows)
    return rows, counts


def apply_to_publications(path, journal_rows):
    """Copy the two figures onto every publication row, joined on journal name."""
    rows, fieldnames = read_csv(path)
    by_name = {(r.get("journal_name") or "").strip(): r for r in journal_rows}

    for column in ("impact_factor", "impact_factor_5yr"):
        if column not in fieldnames:
            fieldnames.append(column)

    filled = 0
    for row in rows:
        record = by_name.get((row.get("journal_name") or "").strip())
        for column in ("impact_factor", "impact_factor_5yr"):
            row[column] = (record or {}).get(column) or ""
        if row["impact_factor"]:
            filled += 1

    write_csv(path, fieldnames, rows)
    return filled, len(rows)


def probe(issn, key):
    """Print both halves of the lookup, because the metrics are in the second."""
    payload = request_json("/journals", {"q": issn, "limit": 3}, key)
    print("--- search ---")
    print(json.dumps(payload, indent=2)[:2000])

    for record in records_from(payload or {}):
        journal_id = record.get("id")
        if not journal_id:
            continue
        print(f"\n--- /journals/{journal_id} ---")
        detail = request_json(f"/journals/{journal_id}", {}, key)
        print(json.dumps(detail, indent=2)[:5000])
        year = find_number(detail, YEAR_KEYS)
        print(f"\n  jif  found: {find_number(detail, JIF_KEYS)}")
        print(f"  jif5 found: {find_number(detail, JIF5_KEYS)}")
        print(f"  year found: {year}")
        if year:
            print(f"\n--- /journals/{journal_id}/reports/year/{int(year)} ---")
            report = request_json(
                f"/journals/{journal_id}/reports/year/{int(year)}", {}, key)
            print(json.dumps(report, indent=2)[:4000])
        break


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Add Clarivate JIF and 5-year JIF to a journals table.")
    parser.add_argument("journals", help="journals.csv produced by journals.py")
    parser.add_argument("--publications",
                        help="also write the figures back onto this publications CSV")
    parser.add_argument("--limit", type=int,
                        help="stop after this many lookups (for a quick trial run)")
    parser.add_argument("--probe", metavar="ISSN",
                        help="print the raw response for one ISSN and exit")
    args = parser.parse_args(argv)

    try:
        key = api_key()
    except MissingKey as error:
        print(f"\n{error}\n", file=sys.stderr)
        return 2

    if args.probe:
        probe(args.probe, key)
        return 0

    print(f"\nlooking up journals in {args.journals}")
    started = time.time()
    try:
        rows, counts = enrich_journals(args.journals, key, args.limit)
    except MissingKey as error:
        print(f"\n{error}\n", file=sys.stderr)
        return 2
    except RateLimited as error:
        print(f"\nstopped: {error}", file=sys.stderr)
        print("Nothing was written. Run it again when the limit clears.\n",
              file=sys.stderr)
        return 1

    with_jif = sum(1 for r in rows if (r.get("impact_factor") or "") != "")
    print(f"\n  {len(rows)} journals, {time.time() - started:.0f}s")
    print(f"  matched by ISSN : {counts.get(MATCH_ISSN, 0)}")
    print(f"  not in JCR      : {counts.get(MATCH_NONE, 0)}")
    print(f"  lookup failed   : {counts.get(MATCH_FAILED, 0)}")
    print(f"  no ISSN to try  : {counts.get('no-issn', 0)}")
    print(f"  now carry a JIF : {with_jif}")

    if counts.get(MATCH_ISSN, 0) and not with_jif:
        # Every lookup succeeded and no figure came back, which means the
        # response is shaped differently to what JIF_KEYS expects.
        print("\n  Journals matched but no impact factor was found in any of them.")
        print("  The field names have probably changed. Run:")
        print(f"    python clarivate.py {args.journals} --probe 0022-1082")
        print("  and check what the JIF is actually called in the response.")

    if args.publications:
        filled, total = apply_to_publications(args.publications, rows)
        print(f"\n  {filled} of {total} publication rows now carry an impact factor")

    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
