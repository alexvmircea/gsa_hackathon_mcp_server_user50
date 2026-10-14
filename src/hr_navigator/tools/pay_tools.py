"""
MCP tools: get_gs_pay_info, list_gs_localities, search_duty_stations

Gives an AI agent access to 2026 OPM General Schedule pay data — base rates,
locality-adjusted salaries, the full list of locality pay areas, and an
OPM duty-station city search so salaries can be queried by actual city name.

Design notes
------------
- All tools are thin wrappers around the pure-data layer in
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
    search_duty_stations as _search_duty_stations,
)


async def get_gs_pay_info(
    grade: Annotated[
        int,
        Field(description="GS grade (1–15).", ge=1, le=15),
    ],
    step: Annotated[
        int,
        Field(description="Within-grade step (1–10).", ge=1, le=10),
    ],
    locality_code: Annotated[
        str,
        Field(
            default="RUS",
            description=(
                "OPM locality pay area code (e.g. 'DCB' for Washington-Baltimore, "
                "'NY' for New York-Newark, 'SF' for San Jose-San Francisco, "
                "'RUS' for Rest of U.S. — the national default). "
                "Ignored when 'city' is provided. "
                "Use list_gs_localities to see all valid codes."
            ),
            min_length=1,
            max_length=5,
        ),
    ] = "RUS",
    city: Annotated[
        str | None,
        Field(
            default=None,
            description=(
                "City name of the employee's duty station "
                "(e.g. 'Huntsville', 'San Francisco', 'Washington'). "
                "When provided, the locality pay area is determined automatically "
                "from the OPM duty-station table and takes precedence over "
                "'locality_code'. Use search_duty_stations to find valid city names."
            ),
            min_length=1,
            max_length=100,
        ),
    ] = None,
    state: Annotated[
        str | None,
        Field(
            default=None,
            description=(
                "State or territory of the duty station, used to disambiguate "
                "cities that appear in multiple states "
                "(e.g. 'Alabama', 'California', or 2-letter abbreviation 'AL'). "
                "Only used when 'city' is provided."
            ),
            min_length=1,
            max_length=50,
        ),
    ] = None,
) -> dict[str, Any]:
    """Return the 2026 locality-adjusted annual salary for a GS grade/step/location.

    Applies the standard OPM formula (base × (1 + locality_rate)) and enforces
    the Executive Schedule Level IV pay cap ($197,200 for 2026).

    The location can be specified two ways — use whichever the agent knows:

    1. **By city** — set ``city`` (and optionally ``state``) to the employee's
       duty-station city.  The locality pay area is resolved automatically from
       the OPM duty-station table (40,000+ cities).
    2. **By locality code** — set ``locality_code`` directly when you already
       know the OPM code.  Use ``list_gs_localities`` to enumerate codes.

    When to use
    -----------
    - "What does a GS-13 step 5 in Washington DC make?"
    - "What is the salary for a GS-9 step 1 in San Francisco?"
    - "How much does a GS-11 step 3 in Huntsville, Alabama earn?"
    - "What does GS-12 step 7 pay in the DCB locality?"

    Parameters
    ----------
    grade:  GS pay grade (1–15).
    step:   Within-grade step (1–10).
    city:   Duty-station city name (takes precedence over locality_code).
    state:  State/territory to disambiguate the city (optional).
    locality_code:
        OPM locality code used when city is not provided.
        Defaults to 'RUS' (Rest of U.S., 17.06%).

    Returns
    -------
    dict
        ``grade``, ``step``, ``base_rate``, ``locality`` (code/name/rate_pct),
        ``locality_adjusted``, ``pay_cap``, ``is_capped``, ``final_salary``,
        ``schedule_year``, and ``duty_station`` (city/state/lpa) when resolved
        by city.

    Raises
    ------
    RuntimeError
        If the grade/step is out of range, the locality code is unknown,
        or the city is not found in the OPM duty-station table.
    """
    try:
        return compute_pay(grade, step, locality_code, city=city, state=state)
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


async def search_duty_stations(
    city: Annotated[
        str,
        Field(
            description=(
                "Partial or full city name to search for "
                "(case-insensitive, e.g. 'spring', 'San Jose', 'fort')."
            ),
            min_length=1,
            max_length=100,
        ),
    ],
    state: Annotated[
        str | None,
        Field(
            default=None,
            description="Optional state/territory filter (case-insensitive).",
            min_length=1,
            max_length=50,
        ),
    ] = None,
) -> list[dict[str, Any]]:
    """Search OPM duty stations by city name fragment.

    Use this tool to discover valid city names before calling
    ``get_gs_pay_info`` with a city parameter.

    When to use
    -----------
    - "What cities in Texas are in the Dallas-Fort Worth locality area?"
    - "Is 'Fort Belvoir' a valid duty station?"
    - "Find duty stations near Springfield."

    Returns
    -------
    list of dict
        Each entry has ``city``, ``state``, ``lpa``, ``locality_code``,
        sorted by city name.  Up to 20 results (de-duplicated by city+state).
    """
    try:
        results = _search_duty_stations(city, state)
    except PayDataError as exc:
        raise RuntimeError(str(exc)) from exc
    return sorted(results, key=lambda x: (x["city"], x["state"]))
