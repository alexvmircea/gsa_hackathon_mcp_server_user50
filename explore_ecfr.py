#!/usr/bin/env python
# =============================================================================
# explore_ecfr.py  — a throwaway probe to discover the REAL eCFR API shapes.
#
# Purpose: resolve the three things we could not confirm from docs snippets:
#   1. The exact SEARCH endpoint path + query-parameter names.
#   2. The SEARCH JSON response structure (which keys hold results/heading/etc).
#   3. The VERSIONER 'full' XML structure (how sections/paragraphs are tagged).
#
# This file is NOT part of the server. Run it once, read the output, then we
# harden ecfr_client.py against what it actually returns.
#
# Run:
#   uv run python explore_ecfr.py
# (httpx is already a project dependency; no API key needed for eCFR.)
# =============================================================================
from __future__ import annotations

import datetime as dt
import json
import sys
import xml.etree.ElementTree as ET

import httpx

BASE = "https://www.ecfr.gov"
TIMEOUT = 20.0
TOPIC = "annual leave"        # a topic that definitely lives in Title 5
TITLE = 5                      # Administrative Personnel


def rule(label: str) -> None:
    print("\n" + "=" * 78)
    print(label)
    print("=" * 78)


def show_json_shape(data, depth: int = 0, max_depth: int = 3) -> None:
    """Print the *structure* of a JSON object (keys and types), not the full
    contents — enough to see where results and fields live."""
    pad = "  " * depth
    if depth > max_depth:
        print(pad + "...")
        return
    if isinstance(data, dict):
        for k, v in data.items():
            kind = type(v).__name__
            if isinstance(v, (dict, list)):
                print(f"{pad}{k}: {kind}")
                show_json_shape(v, depth + 1, max_depth)
            else:
                preview = str(v)
                if len(preview) > 70:
                    preview = preview[:70] + "..."
                print(f"{pad}{k}: {kind} = {preview}")
    elif isinstance(data, list):
        print(f"{pad}[list of {len(data)} items]")
        if data:
            show_json_shape(data[0], depth + 1, max_depth)


# -----------------------------------------------------------------------------
# 1. SEARCH — try several candidate paths/param names and report what works.
# -----------------------------------------------------------------------------
def probe_search(client: httpx.Client) -> None:
    rule("1. SEARCH SERVICE — probing candidate endpoints / params")

    # Candidate (path, params) combos to try. We'll print status for each so we
    # can see which the live API accepts. Order = most likely first.
    #
    # CONFIRMED (2026-10-13):
    #   - Correct:   {"query": ..., "per_page": ...}  — no title filter param exists
    #   - Incorrect: conditions[*], filter[*]  — both return 400 "unpermitted parameter"
    #   - Title filtering must be done client-side on the returned results.
    candidates = [
        ("/api/search/v1/results",
         {"query": TOPIC, "per_page": "5"}),                               # WORKS
        ("/api/search/v1/results",
         {"query": TOPIC, "conditions[hierarchy][title]": str(TITLE), "per_page": "5"}),  # 400
        ("/api/search/v1/results",
         {"query": TOPIC, "conditions[title]": str(TITLE), "per_page": "5"}),             # 400
        ("/api/search/v1/count",
         {"query": TOPIC, "per_page": "5"}),
    ]

    working_payload = None
    for path, params in candidates:
        try:
            resp = client.get(path, params=params)
            status = resp.status_code
            ctype = resp.headers.get("content-type", "")
            print(f"\n[try] GET {path}")
            print(f"      params = {params}")
            print(f"      -> {status}  ({ctype})")
            if status == 200 and "json" in ctype:
                data = resp.json()
                if working_payload is None and path.endswith("results"):
                    working_payload = data
                # show a compact count if present
                if isinstance(data, dict):
                    top_keys = list(data.keys())
                    print(f"      top-level keys: {top_keys}")
        except Exception as exc:  # noqa: BLE001 - probe script, show everything
            print(f"      !! error: {type(exc).__name__}: {exc}")

    if working_payload is not None:
        rule("1a. SEARCH RESPONSE STRUCTURE (first working 'results' call)")
        show_json_shape(working_payload)

        # Drill into the first result so we can see field names we must map.
        results = (
            working_payload.get("results")
            or working_payload.get("data")
            or []
        )
        if results:
            rule("1b. FIRST SEARCH RESULT (full dump — maps to PolicySearchHit)")
            print(json.dumps(results[0], indent=2)[:3000])
        else:
            print("\n(No results array found under 'results' or 'data'. "
                  "Inspect the structure above to locate it.)")
    else:
        print("\n!! None of the candidate search calls returned JSON 200.")
        print("   Open the interactive docs and copy the exact path/params:")
        print("   https://www.ecfr.gov/developers/documentation/api/v1")


# -----------------------------------------------------------------------------
# 2. VERSIONER — fetch real XML and reveal how sections/paragraphs are tagged.
# -----------------------------------------------------------------------------
def probe_versioner(client: httpx.Client) -> None:
    rule("2. VERSIONER SERVICE — fetching real Title 5 XML")

    # CONFIRMED (2026-10-13):
    #   - Do NOT use today's date — use the title's latest_issue_date from
    #     GET /api/versioner/v1/titles  (title 5 = 2026-10-05 as of probe run).
    #   - Section-level subsetting works with both ?part=NNN and
    #     ?part=NNN&section=NNN.NNN (returns just that section's XML, ~1-2 KB).
    #   - Full-title download (?part omitted) times out in a normal HTTP client.
    titles_resp = client.get("/api/versioner/v1/titles")
    titles_data = titles_resp.json()
    title5_meta = next(
        (t for t in titles_data.get("titles", []) if t.get("number") == TITLE), {}
    )
    issue_date = title5_meta.get("latest_issue_date") or dt.date.today().isoformat()
    print(f"Title {TITLE} latest_issue_date: {issue_date}")

    # Part 630 of Title 5 governs 'Absence and Leave' (annual/sick leave).
    path = f"/api/versioner/v1/full/{issue_date}/title-{TITLE}.xml"
    params = {"part": "630", "section": "630.308"}  # section-level = fast (~1 KB)

    try:
        resp = client.get(path, params=params)
        print(f"GET {path} -> {resp.status_code} "
              f"({resp.headers.get('content-type','')})")
        resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        print(f"!! error: {type(exc).__name__}: {exc}")
        print("   Try without ?part=, or check the date format.")
        return

    xml_text = resp.text
    print(f"XML length: {len(xml_text):,} chars")
    print("\nFirst 600 chars of XML:")
    print(xml_text[:600])

    # Parse and summarize the DIV/P structure we rely on in _extract_text().
    rule("2a. XML ELEMENT STRUCTURE (tags, and DIV 'N'/'TYPE' attributes)")
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        print(f"!! XML parse error: {exc}")
        return

    tag_counts: dict[str, int] = {}
    sample_divs: list[str] = []
    for el in root.iter():
        tag = el.tag
        tag_counts[tag] = tag_counts.get(tag, 0) + 1
        if tag.upper().startswith("DIV") and len(sample_divs) < 12:
            attrs = {k: el.get(k) for k in ("N", "TYPE", "NODE")}
            sample_divs.append(f"  {tag}  attrs={attrs}")

    print("Tag frequency (top 15):")
    for tag, n in sorted(tag_counts.items(), key=lambda kv: -kv[1])[:15]:
        print(f"  {tag}: {n}")

    print("\nSample DIV elements (shows how sections are numbered):")
    print("\n".join(sample_divs) if sample_divs else "  (no DIV elements found)")

    # Show the first few <P> paragraphs so we can confirm text extraction.
    rule("2b. FIRST PARAGRAPHS OF TEXT (what _extract_text should return)")
    count = 0
    for el in root.iter():
        if el.tag.upper() == "P" and el.text and el.text.strip():
            print("»", " ".join(el.text.split())[:200])
            count += 1
            if count >= 5:
                break
    if count == 0:
        print("(No <P> text found — inspect the tag frequency above for the "
              "real paragraph tag name.)")


def main() -> int:
    print(f"Probing eCFR at {BASE}")
    print(f"Topic='{TOPIC}'  Title={TITLE}  Date={dt.date.today().isoformat()}")
    # follow_redirects matters: eCFR has shown redirect behavior.
    with httpx.Client(base_url=BASE, timeout=TIMEOUT, follow_redirects=True) as client:
        probe_search(client)
        probe_versioner(client)
    rule("DONE")
    print("Copy this output back and we'll harden ecfr_client.py to match.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
