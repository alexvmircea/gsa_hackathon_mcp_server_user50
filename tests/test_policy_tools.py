"""
Tests for the look_up_hr_policy tool.

Strategy (per docs/architecture.md §6):
- In-process fastmcp.Client calls the tool without a real network round-trip.
- httpx calls to the eCFR API are intercepted by respx and return canned
  responses for deterministic, offline-safe execution.

Test cases
----------
1. Successful lookup — search returns hits; section text is fetched.
2. Search returns no results — tool returns empty results list gracefully.
3. eCFR search API returns HTTP 503 — tool raises RuntimeError with a
   useful message (FastMCP will convert this to an MCP error response).
4. Section fetch fails — tool still returns the result, marking text as
   unavailable (non-fatal degradation).
5. Input validation — topic below min_length raises Pydantic ValidationError
   before any HTTP call is made.
"""

from __future__ import annotations

import json
from datetime import date

import pytest
import respx
from httpx import Response
from pydantic import ValidationError

from hr_navigator.models.policy import PolicyLookupInput, PolicyLookupOutput
from hr_navigator.tools.policy_tools import look_up_hr_policy

# ---------------------------------------------------------------------------
# Fixtures / shared test data
# ---------------------------------------------------------------------------

SEARCH_URL = "https://www.ecfr.gov/api/search/v1/results"
SECTION_URL_PATTERN = r"https://www\.ecfr\.gov/api/versioner/v1/full/.*/title-5\.xml"

# Minimal valid eCFR search response with one hit
ONE_HIT_RESPONSE = {
    "results": [
        {
            "hierarchy": {"title": "5", "part": "630", "section": "202"},
            "headings": {
                "title": "Administrative Personnel",
                "part": "Leave",
                "section": "Definitions",
            },
            "full_text_excerpt": (
                "As used in this subpart: "
                "Annual leave means leave earned and credited under 5 U.S.C. 6303."
            ),
        }
    ]
}

# ---------------------------------------------------------------------------
# Test 1: Successful lookup
# ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_successful_lookup_returns_results() -> None:
    """Tool returns a populated PolicyLookupOutput when the API responds normally."""
    respx.get(SEARCH_URL).mock(
        return_value=Response(200, json=ONE_HIT_RESPONSE)
    )

    output = await look_up_hr_policy(topic="annual leave accrual", title=5, max_results=1)

    assert isinstance(output, PolicyLookupOutput)
    assert output.query_topic == "annual leave accrual"
    assert output.cfr_title_searched == 5
    assert len(output.results) == 1

    result = output.results[0]
    assert result.section_ref == "5 CFR § 630.202"
    assert result.part == 630
    assert result.section == 202
    assert result.cfr_title == 5
    assert "annual leave" in result.text_excerpt.lower()
    assert result.url == "https://www.ecfr.gov/current/title-5/part-630/section-630.202"
    assert result.heading == "Definitions"


# ---------------------------------------------------------------------------
# Test 2: Search returns no results
# ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_empty_search_returns_empty_output() -> None:
    """Tool returns an empty results list — not an error — when nothing is found."""
    respx.get(SEARCH_URL).mock(
        return_value=Response(200, json={"results": []})
    )

    output = await look_up_hr_policy(topic="nonexistent topic xyz", title=5)

    assert isinstance(output, PolicyLookupOutput)
    assert output.results == []
    assert output.query_topic == "nonexistent topic xyz"


# ---------------------------------------------------------------------------
# Test 3: eCFR search API unavailable (HTTP 503)
# ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_api_error_raises_runtime_error() -> None:
    """Tool raises RuntimeError with a descriptive message on API failure."""
    respx.get(SEARCH_URL).mock(
        return_value=Response(503, text="Service Unavailable")
    )

    with pytest.raises(RuntimeError, match="eCFR API"):
        await look_up_hr_policy(topic="annual leave")


# ---------------------------------------------------------------------------
# Test 4: Section fetch fails — graceful degradation
# ---------------------------------------------------------------------------


@respx.mock
@pytest.mark.asyncio
async def test_section_fetch_failure_is_non_fatal() -> None:
    """When get_section fails, the result is kept but text is marked unavailable."""
    # Search returns a hit with NO full_text_excerpt (forces a get_section call)
    hit_without_excerpt = {
        "results": [
            {
                "hierarchy": {"title": "5", "part": "630", "section": "202"},
                "headings": {"section": "Definitions"},
                # No full_text_excerpt key
            }
        ]
    }
    respx.get(SEARCH_URL).mock(
        return_value=Response(200, json=hit_without_excerpt)
    )
    # Section fetch returns 404
    respx.get(url__regex=SECTION_URL_PATTERN).mock(
        return_value=Response(404, text="Not Found")
    )

    output = await look_up_hr_policy(topic="annual leave", max_results=1)

    assert len(output.results) == 1
    result = output.results[0]
    assert "unavailable" in result.text_excerpt.lower()
    assert result.section_ref == "5 CFR § 630.202"


# ---------------------------------------------------------------------------
# Test 5: Input validation
# ---------------------------------------------------------------------------


def test_input_validation_rejects_short_topic() -> None:
    """PolicyLookupInput raises ValidationError for a topic below min_length."""
    with pytest.raises(ValidationError):
        PolicyLookupInput(topic="x")  # min_length=2, so single char should fail


def test_input_validation_rejects_out_of_range_max_results() -> None:
    """PolicyLookupInput rejects max_results outside 1–5."""
    with pytest.raises(ValidationError):
        PolicyLookupInput(topic="annual leave", max_results=10)


def test_input_validation_accepts_valid_input() -> None:
    """PolicyLookupInput accepts a well-formed payload."""
    inp = PolicyLookupInput(topic="annual leave accrual", title=5, max_results=3)
    assert inp.topic == "annual leave accrual"
    assert inp.title == 5
    assert inp.max_results == 3
