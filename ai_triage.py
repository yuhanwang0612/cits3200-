
"""
AI triage for exported publication rows using Gemini.

The model finds suspicious rows that are worth manually checking.
It does NOT determine whether a publication is actually correct.

Usage:

    # PowerShell
    $env:GEMINI_API_KEY = "your-api-key"

    # Run on 40 random rows
    python ai_triage.py --n 40 --seed 42

    # Run on every row
    python ai_triage.py --all

    # Save flags to CSV
    python ai_triage.py --n 40 --seed 42 --out triage_flags.csv

    # Change the batch size
    python ai_triage.py --n 100 --batch 10
"""

import argparse
import json
import os
import random
import sys
import time

import pandas as pd
import requests

from dotenv import load_dotenv


# Load .env if one exists
load_dotenv()


# ============================================================
# CONFIGURATION
# ============================================================

API_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "gemini-3.6-flash:generateContent"
)

API_KEY_ENV = "GEMINI_API_KEY"


# Only these columns are sent to Gemini.
# Ratings and metrics are deliberately excluded because Gemini
# cannot verify them from the supplied data.
SEND_COLUMNS = [
    "row_id",
    "name",
    "field_of_research",
    "title",
    "journal_name",
    "year",
    "doi",
    "author_count",
    "authors",
    "publication_type",
    "source",
]


# ============================================================
# SYSTEM PROMPT
# ============================================================

SYSTEM = """
You are auditing rows from a database of academic publications by
Accounting and Finance researchers at Australian universities.

Each row was scraped from a university repository or bibliographic index.

Your job is to FLAG rows that look WRONG or SUSPICIOUS.

You are finding candidates for manual review, NOT determining whether
a publication is actually incorrect.

Worth flagging:

- The journal is implausible for an Accounting or Finance academic,
  with no obvious interdisciplinary explanation.
- The title reads like a book chapter, dataset, editorial, book review,
  conference abstract, thesis, report, or other non-journal publication.
- author_count disagrees with the number of names in authors.
- The year contradicts a year embedded in the DOI by more than about
  two years.
- The title or author list is truncated, garbled, malformed, or contains
  obvious markup.
- The journal name appears to be a repository, preprint server, database,
  or other non-journal source rather than a journal.

Do NOT flag:

- Interdisciplinary work. Finance and Accounting academics genuinely
  publish in climate, health, statistics, economics, information systems,
  computer science, and other fields.
- Missing DOIs.
- Missing author lists.
- Missing years.
- Unusual author counts by themselves. Papers with dozens or even
  hundreds of authors can be legitimate.
- Older publications.
- Journals you personally do not recognise.
- Something merely because it looks unusual.

Important:

Do not invent problems.
Only flag something when there is a concrete reason visible in the data.

Return JSON only.

The response must have exactly this structure:

{
  "flags": [
    {
      "row_id": 123,
      "issue": "short label",
      "reason": "one sentence explaining the problem",
      "confidence": "high"
    }
  ]
}

confidence must be exactly one of:

"high"
"medium"
"low"

If nothing looks suspicious, return:

{
  "flags": []
}
"""


# ============================================================
# JSON PARSER
# ============================================================

def extract_flags(text):
    """
    Parse Gemini's JSON response.

    Handles both normal JSON and JSON accidentally wrapped
    in a markdown code fence.
    """

    if not text:
        return []

    text = text.strip()

    # Remove markdown fences if Gemini adds them
    if text.startswith("```json"):
        text = text[len("```json"):]

    elif text.startswith("```"):
        text = text[len("```"):]

    if text.endswith("```"):
        text = text[:-3]

    text = text.strip()

    try:
        result = json.loads(text)

    except json.JSONDecodeError:
        print("    Could not parse Gemini response as JSON.")
        print("    Raw response:")
        print(text)
        return []

    if not isinstance(result, dict):
        print("    Gemini response was not a JSON object.")
        return []

    flags = result.get("flags", [])

    if not isinstance(flags, list):
        print("    Gemini returned an invalid 'flags' field.")
        return []

    return flags


# ============================================================
# GEMINI API
# ============================================================

def audit_gemini(rows, api_key):
    """
    Send one batch of publication rows to Gemini.
    """

    headers = {
        "x-goog-api-key": api_key,
        "Content-Type": "application/json",
    }

    user_prompt = (
        "Audit these publication rows.\n\n"
        + json.dumps(rows, indent=2, ensure_ascii=False)
    )

    payload = {
        "systemInstruction": {
            "parts": [
                {
                    "text": SYSTEM
                }
            ]
        },

        "contents": [
            {
                "role": "user",
                "parts": [
                    {
                        "text": user_prompt
                    }
                ]
            }
        ],

        "generationConfig": {
            "temperature": 0,
            "maxOutputTokens": 4000,
            "responseMimeType": "application/json",
        },
    }

    # Retry a few times if there is a temporary error
    for attempt in range(3):

        try:

            response = requests.post(
                API_URL,
                headers=headers,
                json=payload,
                timeout=120,
            )

            # If Gemini returns an HTTP error, print its actual
            # explanation instead of hiding it.
            if not response.ok:

                print(
                    f"    Gemini API error "
                    f"(HTTP {response.status_code})"
                )

                print("    Response:")
                print(response.text)

                # Retry server/rate-limit errors
                if response.status_code in (429, 500, 502, 503, 504):
                    if attempt < 2:
                        wait = 5 * (attempt + 1)
                        print(f"    Retrying in {wait} seconds...")
                        time.sleep(wait)
                        continue

                return []

            data = response.json()

            # Check that Gemini actually returned candidates
            candidates = data.get("candidates", [])

            if not candidates:
                print("    Gemini returned no candidates.")
                print(json.dumps(data, indent=2))
                return []

            candidate = candidates[0]

            content = candidate.get("content", {})

            parts = content.get("parts", [])

            if not parts:
                print("    Gemini returned no content parts.")
                print(json.dumps(data, indent=2))
                return []

            text = parts[0].get("text", "")

            if not text:
                print("    Gemini returned empty text.")
                print(json.dumps(data, indent=2))
                return []

            return extract_flags(text)

        except requests.Timeout:

            print("    Gemini request timed out.")

            if attempt < 2:
                wait = 5 * (attempt + 1)
                print(f"    Retrying in {wait} seconds...")
                time.sleep(wait)
                continue

            return []

        except requests.RequestException as e:

            print(f"    Request failed: {e}")

            if attempt < 2:
                wait = 5 * (attempt + 1)
                print(f"    Retrying in {wait} seconds...")
                time.sleep(wait)
                continue

            return []

        except json.JSONDecodeError:

            print("    Gemini returned invalid HTTP JSON.")
            print(response.text)
            return []

        except Exception as e:

            print(f"    Unexpected error: {type(e).__name__}: {e}")
            return []

    return []


# ============================================================
# MAIN
# ============================================================

def main():

    parser = argparse.ArgumentParser(
        description="Use Gemini to flag suspicious publication rows."
    )

    parser.add_argument(
        "--file",
        default="output/uq/publications.csv",
        help="input publications CSV",
    )

    parser.add_argument(
        "--n",
        type=int,
        default=40,
        help="number of rows to sample",
    )

    parser.add_argument(
        "--all",
        action="store_true",
        help="audit every row",
    )

    parser.add_argument(
        "--batch",
        type=int,
        default=20,
        help="number of rows sent to Gemini at once",
    )

    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="random seed for reproducible sampling",
    )

    parser.add_argument(
        "--out",
        default=None,
        help="write flags to this CSV",
    )

    args = parser.parse_args()


    # ========================================================
    # API KEY
    # ========================================================

    api_key = os.environ.get(API_KEY_ENV)

    if not api_key:

        sys.exit(
            "\nGEMINI_API_KEY is not set.\n\n"
            "PowerShell:\n"
            '    $env:GEMINI_API_KEY = "your-api-key"\n\n'
            "Then run:\n"
            "    python ai_triage.py --n 40 --seed 42\n"
        )


    print("provider: Gemini")
    print("model: gemini-3.6-flash")
    print()


    # ========================================================
    # LOAD CSV
    # ========================================================

    try:

        df = pd.read_csv(
            args.file,
            dtype=str,
        )

    except FileNotFoundError:

        sys.exit(
            f"Could not find input file:\n"
            f"    {args.file}\n"
        )

    except Exception as e:

        sys.exit(
            f"Could not read CSV:\n"
            f"    {e}\n"
        )


    # Give every row an ID based on its original position
    df = (
        df
        .reset_index()
        .rename(columns={"index": "row_id"})
    )


    print(f"loaded {len(df)} rows from {args.file}")


    # ========================================================
    # SELECT ROWS
    # ========================================================

    if args.all:

        selected = df.copy()

        print("auditing every row")

    else:

        sample_size = min(args.n, len(df))

        random_seed = (
            args.seed
            if args.seed is not None
            else random.randint(0, 10**6)
        )

        selected = df.sample(
            sample_size,
            random_state=random_seed,
        )

        if args.seed is not None:
            print(f"seed {args.seed} — reproducible")
        else:
            print(f"random seed {random_seed}")


    # ========================================================
    # PREPARE DATA FOR GEMINI
    # ========================================================

    cols = [
        column
        for column in SEND_COLUMNS
        if column in selected.columns
    ]

    rows = (
        selected[cols]
        .fillna("")
        .to_dict("records")
    )


    print(
        f"auditing {len(rows)} rows "
        f"in batches of {args.batch}"
    )

    print()


    # ========================================================
    # AUDIT BATCHES
    # ========================================================

    flags = []

    total = len(rows)

    for start in range(0, total, args.batch):

        end = min(
            start + args.batch,
            total,
        )

        batch = rows[start:end]

        print(
            f"  sending rows {start + 1}-{end}..."
        )

        got = audit_gemini(
            batch,
            api_key,
        )

        flags.extend(got)

        print(
            f"  {end}/{total} processed — "
            f"{len(flags)} flags so far"
        )

        # Small delay to avoid hammering the API
        if end < total:
            time.sleep(1)


    # ========================================================
    # NOTHING FLAGGED
    # ========================================================

    if not flags:

        print()
        print("nothing flagged")

        return


    # ========================================================
    # LOOKUP ORIGINAL ROWS
    # ========================================================

    lookup = (
        df
        .set_index("row_id")
        .to_dict("index")
    )


    # Sort high confidence first
    confidence_order = {
        "high": 0,
        "medium": 1,
        "low": 2,
    }

    flags.sort(
        key=lambda flag: confidence_order.get(
            flag.get("confidence", "low"),
            3,
        )
    )


    # ========================================================
    # DISPLAY FLAGS
    # ========================================================

    print()
    print(
        f"--- {len(flags)} flagged for review ---"
    )

    print(
        "These are candidates, not verdicts. "
        "Check each one manually."
    )

    print()


    for flag in flags:

        row = lookup.get(
            str(flag.get("row_id")),
            {},
        )

        # row_id may have been returned as an integer
        if not row:
            row = lookup.get(
                flag.get("row_id"),
                {},
            )

        confidence = flag.get(
            "confidence",
            "?",
        )

        issue = flag.get(
            "issue",
            "unknown",
        )

        reason = flag.get(
            "reason",
            "",
        )

        print(
            f"[{confidence:6}] {issue}"
        )

        print(
            f"         {reason}"
        )

        print(
            f"         {row.get('name', '')} · "
            f"{row.get('year', '')} · "
            f"{str(row.get('title', ''))[:80]}"
        )

        print(
            f"         {row.get('journal_name', '')}"
        )

        if row.get("link"):

            print(
                f"         {row.get('link')}"
            )

        elif row.get("article_url"):

            print(
                f"         {row.get('article_url')}"
            )

        print()


    # ========================================================
    # SAVE RESULTS
    # ========================================================

    if args.out:

        output = []

        for flag in flags:

            row = lookup.get(
                str(flag.get("row_id")),
                {},
            )

            if not row:
                row = lookup.get(
                    flag.get("row_id"),
                    {},
                )

            output.append(
                {
                    **flag,

                    "name": row.get("name"),
                    "title": row.get("title"),
                    "journal_name": row.get("journal_name"),
                    "year": row.get("year"),
                    "doi": row.get("doi"),

                    "link": (
                        row.get("link")
                        or row.get("article_url")
                    ),
                }
            )


        pd.DataFrame(output).to_csv(
            args.out,
            index=False,
        )

        print(
            f"written to {args.out}"
        )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    main()

