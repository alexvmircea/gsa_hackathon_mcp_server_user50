"""
MCP tools: get_gs_pay_info, list_gs_localities

Gives an AI agent access to 2026 OPM General Schedule pay data — base rates,
locality-adjusted salaries, and the full list of locality pay areas.

Design notes
------------
- Both tools are thin wrappers around the pure-data layer in
  ``clients/gs_pay``.  No business logic lives here.
- ``PayDataError`` is translated to ``RuntimeError`` so FastMCP surfaces it
  as a structured MCP error the agent can reason about.
"""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import Field

from hr_navigator.clients.gs_pay import (
    PayDataError,
    compute_pay,
    list_localities,
)


async def get_gs_pay_info(
    grade: Annotated[
        int,
        Field(
            description="GS grade (1–15).",
            ge=1,
            le=15,
        ),
    ],
    step: Annotated[
        int,
        Field(
            description="Within-grade step (1–10).",
            ge=1,
            le=10,
        ),
    ],
    locality_code: Annotated[
        str,
        Field(
            default="RUS",
            description=(
                "OPM locality pay area code (e.g. 'DCB' for Washington-Baltimore, "
                "'NY' for New York-Newark, 'SF' for San Jose-San Francisco, "
                "'RUS' for Rest of U.S. — the national default). "
                "Use list_gs_localities to see all valid codes."
            ),
            min_length=1,
            max_length=5,
        ),
    ] = "RUS",
) -> dict[str, Any]:
    """Return the 2026 locality-adjusted annual salary for a GS grade, step, and locality.

    Applies the standard OPM formula (base × (1 + locality_rate)) and enforces
    the Executive Schedule Level IV pay cap ($197,200 for 2026).

    When to use
    -----------
    - "What does a GS-13 step 5 in DC make?"
    - "What is the salary for a GS-9 step 1 in San Francisco?"
    - "How much does a GS-15 step 10 earn after the pay cap?"

    Parameters
    ----------
    grade:
        GS pay grade (1–15).
    step:
        Within-grade step (1–10).
    locality_code:
        OPM locality code. Defaults to 'RUS' (Rest of U.S., the national
        floor rate of 17.06%).  Use ``list_gs_localities`` to look up codes.

    Returns
    -------
    dict
        ``grade``, ``step``, ``base_rate``, ``locality`` (code/name/rate_pct),
        ``locality_adjusted``, ``pay_cap``, ``is_capped``, ``final_salary``,
        ``schedule_year``.

    Raises
    ------
    RuntimeError
        If the grade/step is out of range or the locality code is unknown.
    """
    try:
        return compute_pay(grade, step, locality_code)
    except PayDataError as exc:
        raise RuntimeError(str(exc)) from exc


async def list_gs_localities() -> list[dict[str, Any]]:
    """Return all 58 OPM locality pay areas with their codes and 2026 rates.

    When to use
    -----------
    - "What locality code should I use for Chicago?"
    - "What are all the available locality pay areas?"
    - "Which locality has the highest pay adjustment?"

    Returns
    -------
    list of dict
        Each entry contains ``code``, ``name``, and ``rate_pct``, sorted by
        ``rate_pct`` descending (highest adjustment first).
    """
    try:
        locs = list_localities()
    except PayDataError as exc:
        raise RuntimeError(str(exc)) from exc
    return sorted(locs, key=lambda x: x["rate_pct"], reverse=True)
