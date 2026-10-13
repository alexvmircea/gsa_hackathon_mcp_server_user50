"""
MCP tool: look_up_hr_policy

Gives an AI agent governed, structured access to federal HR regulations
stored in the Electronic Code of Federal Regulations (eCFR), Title 5
(Administrative Personnel).

Design notes
------------
- This is the orchestration layer: it composes ECFRClient calls and maps
  raw API responses into validated PolicyResult models.
- It is *not* a thin wrapper — it runs a search then fetches the full
  text for each hit, so the agent gets actual regulatory prose rather
  than just titles and citations.
- Errors are raised with human-readable messages so FastMCP can convert
  them into MCP error responses the agent can reason about.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from hr_navigator.clients.ecfr import ECFRClient, ECFRError, extract_text_from_xml
from hr_navigator.config import DEFAULT_CFR_TITLE, DEFAULT_MAX_RESULTS, ECFR_BASE_URL
from hr_navigator.models.policy import PolicyLookupOutput, PolicyResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_section_url(base_url: str, title: int, part: int, section: int) -> str:
    """Return the ecfr.gov browser URL for a specific section."""
    return f"{base_url.rstrip('/')}/current/title-{title}/part-{part}/section-{part}.{section}"


def _parse_hierarchy(hit: dict[str, Any]) -> tuple[int, int, int] | None:
    """Extract (title, part, section) integers from a search-result hierarchy dict.

    The eCFR search API returns a ``hierarchy`` object like::

        {"title": "5", "part": "630", "section": "202"}

    Returns ``None`` if any required field is missing or non-numeric.
    """
    h = hit.get("hierarchy", {})
    try:
        title = int(h.get("title", 0))
        part = int(h.get("part", 0))
        # section may be "630.202" — take the digits after the dot
        raw_section = str(h.get("section", "0"))
        # Handle "630.202" → 202, or plain "202"
        section_str = raw_section.split(".")[-1]
        section = int(re.sub(r"\D", "", section_str) or "0")
        if title and part and section:
            return title, part, section
    except (ValueError, TypeError):
        pass
    return None


def _heading_from_hit(hit: dict[str, Any]) -> str:
    """Pull the best available heading string from a search hit."""
    headings: dict[str, Any] = hit.get("headings", {})
    # Prefer section-level heading, fall back to part, then title
    return (
        headings.get("section")
        or headings.get("part")
        or headings.get("title")
        or "Untitled section"
    )


# ---------------------------------------------------------------------------
# Tool implementation (called directly by server.py)
# ---------------------------------------------------------------------------


async def look_up_hr_policy(
    topic: str,
    title: int = DEFAULT_CFR_TITLE,
    max_results: int = DEFAULT_MAX_RESULTS,
) -> PolicyLookupOutput:
    """Find the governing federal regulation(s) for an HR policy topic.

    Use this tool when an agent needs to answer questions about federal
    workforce rules, leave policies, pay administration, ethics requirements,
    classification standards, or any other topic governed by Title 5 of the
    Code of Federal Regulations.

    What this tool does
    -------------------
    1. Searches the eCFR full-text index for the given *topic*.
    2. For each of the top hits, fetches the actual regulatory text.
    3. Returns a ranked list of :class:`PolicyResult` objects, each
       containing the CFR citation, section heading, a text excerpt
       (up to ~1 500 characters of the actual regulation), and a direct
       link to ecfr.gov.

    When to use
    -----------
    - "What does the law/regulation say about <topic>?"
    - "What's the annual leave accrual rate for an employee with X years?"
    - "What are the rules around telework eligibility?"
    - "What does Title 5 say about probationary periods?"
    - Any question where the answer should be grounded in an authoritative
      CFR citation.

    When *not* to use
    -----------------
    - For *recent changes* to regulations, use ``check_recent_policy_changes``
      (Federal Register) instead.
    - For pay scale tables (GS grades/steps), use ``get_pay_scale_info``.
    - For job qualification requirements, use ``find_federal_job_requirements``.

    Parameters
    ----------
    topic:
        Plain-language description of the HR policy topic.
        Examples: "annual leave accrual", "telework eligibility",
        "probationary period", "reduction in force", "SES pay cap".
    title:
        CFR title number to search (default 5 = Administrative Personnel).
        Rarely needs to be changed for federal HR questions.
    max_results:
        Number of regulatory sections to return (1–5, default 3).
        Use 1 for a quick, focused answer; 5 for broader coverage.

    Returns
    -------
    PolicyLookupOutput
        Contains a ``results`` list of matching sections and metadata
        about the query.  If the list is empty, no matching regulation
        was found — consider rephrasing the topic.

    Raises
    ------
    RuntimeError
        If the eCFR API is unavailable or returns an unexpected error.
        The message will describe the failure so the agent can decide
        whether to retry or inform the user.
    """
    async with ECFRClient(base_url=ECFR_BASE_URL) as client:
        # Step 1: full-text search
        try:
            hits = await client.search(
                query=topic,
                title=title,
                max_results=max_results,
            )
        except ECFRError as exc:
            raise RuntimeError(
                f"Could not search the eCFR for '{topic}'. "
                f"The eCFR API may be temporarily unavailable. "
                f"Details: {exc}"
            ) from exc

        if not hits:
            return PolicyLookupOutput(
                results=[],
                query_topic=topic,
                cfr_title_searched=title,
            )

        # Step 2: fetch full text for each hit
        results: list[PolicyResult] = []
        retrieved_at = datetime.now(tz=timezone.utc)

        for hit in hits[:max_results]:
            coords = _parse_hierarchy(hit)
            if coords is None:
                # Skip hits we cannot resolve to a section
                continue
            hit_title, part, section = coords

            # Prefer the text excerpt already in the search result; only
            # call get_section if we need more detail or the excerpt is absent.
            excerpt: str = hit.get("full_text_excerpt", "").strip()

            if not excerpt:
                try:
                    xml = await client.get_section(
                        title=hit_title, part=part, section=section
                    )
                    excerpt = extract_text_from_xml(xml)
                except ECFRError:
                    # Non-fatal: keep the result with an empty excerpt
                    excerpt = "(Full text unavailable — see link below.)"

            section_ref = f"{hit_title} CFR § {part}.{section}"
            url = _build_section_url(ECFR_BASE_URL, hit_title, part, section)

            results.append(
                PolicyResult(
                    section_ref=section_ref,
                    heading=_heading_from_hit(hit),
                    part=part,
                    section=section,
                    cfr_title=hit_title,
                    text_excerpt=excerpt or "(No text excerpt available.)",
                    url=url,
                    retrieved_at=retrieved_at,
                )
            )

        return PolicyLookupOutput(
            results=results,
            query_topic=topic,
            cfr_title_searched=title,
        )
