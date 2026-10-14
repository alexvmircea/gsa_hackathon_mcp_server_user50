"""
GS pay data loader.

Loads the static OPM General Schedule base table and locality rates from
``data/gs_pay_2026.json``, and duty-station city lookups from
``data/duty_stations.json``, exposing a small, pure API for computing
locality-adjusted salaries.

This module has no MCP or tool awareness — it is plain data access and
arithmetic that can be tested independently of the MCP layer.  It mirrors the
``clients/`` design contract (raw access to a source, nothing about agents).

Why static data rather than a live API
---------------------------------------
OPM publishes the GS schedule once a year (effective each January).  A bundled
snapshot is more reliable than scraping and has no auth or rate-limit concerns,
at the cost of an annual data refresh.  See docs/architecture.md §3.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

_DATA_PATH     = Path(__file__).resolve().parent.parent / "data" / "gs_pay_2026.json"
_STATIONS_PATH = Path(__file__).resolve().parent.parent / "data" / "duty_stations.json"

_MIN_GRADE, _MAX_GRADE = 1, 15
_MIN_STEP, _MAX_STEP = 1, 10

# Mapping from the OPM duty-station CSV "Locality Code" column to the
# gs_pay_2026.json locality codes used for pay calculations.
# ZX = Rest of U.S.; ZZ = Not in a locality pay area (treated as RUS).
_CSV_TO_GS: dict[str, str] = {
    "AK": "AK",  "AL": "AL",  "AQ": "AQ",  "AT": "ATL", "AU": "AU",
    "BH": "BH",  "BN": "BN",  "BO": "BOS", "BU": "BU",  "CC": "CC",
    "CH": "CHI", "CI": "CIN", "CL": "CLE", "CO": "COL", "CS": "CS",
    "CT": "CT",  "DA": "DFW", "DG": "DAY", "DM": "DM",  "DN": "DEN",
    "DT": "DET", "DV": "DV",  "FN": "FN",  "HA": "HAR", "HB": "HB",
    "HI": "HI",  "HO": "HOU", "HU": "HNT", "IN": "IND", "KC": "KC",
    "LA": "LA",  "LR": "LR",  "LV": "LV",  "MI": "MFL", "ML": "MIL",
    "MN": "MSP", "NY": "NY",  "OM": "OM",  "PB": "PB",  "PH": "PHL",
    "PI": "PIT", "PO": "POR", "PX": "PX",  "RA": "RA",  "RI": "RCH",
    "RN": "RN",  "RT": "RT",  "SA": "SAC", "SD": "SD",  "SJ": "SF",
    "SL": "SL",  "SN": "SN",  "SO": "SO",  "ST": "SEA", "TU": "TU",
    "VB": "VB",  "WA": "DCB", "ZX": "RUS", "ZZ": "RUS",
}


class PayDataError(Exception):
    """Raised when the pay data is missing, malformed, or a lookup is invalid."""


@lru_cache(maxsize=1)
def _load_raw() -> dict[str, Any]:
    """Load and validate the JSON once, caching the result.

    Validation is strict: if any base-table cell is missing or non-integer we
    fail loudly at load time rather than returning a wrong salary to an agent.
    """
    try:
        raw = json.loads(_DATA_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise PayDataError(
            f"GS pay data file not found at {_DATA_PATH}. "
            "Expected a bundled OPM salary table JSON."
        ) from exc
    except json.JSONDecodeError as exc:
        raise PayDataError(f"GS pay data file is not valid JSON: {exc}") from exc

    # --- base table ---
    base = raw.get("base_table", {})
    missing: list[str] = []
    for grade in range(_MIN_GRADE, _MAX_GRADE + 1):
        row = base.get(str(grade))
        if not isinstance(row, list) or len(row) != 10:
            missing.append(f"GS-{grade} (row absent or wrong length)")
            continue
        for i, cell in enumerate(row, start=1):
            if not isinstance(cell, int):
                missing.append(f"GS-{grade} step {i}")

    if missing:
        raise PayDataError(
            "GS base table is incomplete — fill these cells from the OPM "
            f"2026-GS PDF ({raw.get('metadata', {}).get('source_url', 'OPM')}):\n  "
            + "\n  ".join(missing)
        )

    # --- localities ---
    if not raw.get("localities"):
        raise PayDataError("GS pay data has no 'localities' section.")

    loc_missing: list[str] = []
    loc_oob: list[str] = []
    for code, info in raw["localities"].items():
        if code.startswith("_"):
            continue
        rate = info.get("rate_pct")
        if not isinstance(rate, (int, float)):
            loc_missing.append(f"{code} ({info.get('name', '?')})")
        elif not (17.0 <= rate <= 47.0):
            loc_oob.append(f"{code}={rate}%")

    if loc_missing:
        raise PayDataError(
            "Locality rates missing — re-run the OPM fetch script:\n  "
            + "\n  ".join(loc_missing)
        )
    if loc_oob:
        raise PayDataError(
            "Locality rates outside the plausible 17–47% band "
            f"(check for transcription errors): {', '.join(loc_oob)}"
        )

    return raw


# ---------------------------------------------------------------------------
# Duty-station index (city → locality code)
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _load_stations() -> list[dict[str, Any]]:
    """Load duty stations once and cache."""
    try:
        return json.loads(_STATIONS_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise PayDataError(
            f"Duty station data file not found at {_STATIONS_PATH}."
        ) from exc
    except json.JSONDecodeError as exc:
        raise PayDataError(f"Duty station data file is not valid JSON: {exc}") from exc


@lru_cache(maxsize=1)
def _station_city_index() -> dict[str, list[dict[str, Any]]]:
    """Build a normalised city-name → [station, ...] lookup index (cached).

    Keys are ``"city|state"`` (lower-case, stripped) for exact match and
    plain ``"city"`` for city-only match.  A city may appear in multiple
    states (e.g. "Springfield") so the value is always a list.
    """
    index: dict[str, list[dict[str, Any]]] = {}
    for s in _load_stations():
        city_key  = s["city"].strip().lower()
        state_key = s["state"].strip().lower()
        full_key  = f"{city_key}|{state_key}"
        index.setdefault(city_key, []).append(s)
        index.setdefault(full_key, []).append(s)
    return index


def resolve_locality_by_city(
    city: str, state: str | None = None
) -> dict[str, Any]:
    """Return the gs_pay locality info for an OPM duty-station city.

    Lookup is case-insensitive.  When *state* is provided a ``"city|state"``
    exact match is attempted first; if that fails, or if *state* is omitted,
    a city-only match is used.  When multiple duty stations share the same
    city name and no *state* disambiguates, the first match is returned
    (they almost always map to the same locality pay area).

    Parameters
    ----------
    city:
        City name as it appears in the OPM duty stations list
        (e.g. ``"Washington"``, ``"San Francisco"``, ``"Huntsville"``).
    state:
        Optional state/territory name or abbreviation
        (e.g. ``"Alabama"``, ``"AL"``, ``"California"``).

    Returns
    -------
    dict
        ``{"locality_code", "locality_name", "rate_pct", "city",
           "state", "lpa"}``

    Raises
    ------
    PayDataError
        If no matching duty station is found.
    """
    index = _station_city_index()
    city_norm  = city.strip().lower()
    state_norm = state.strip().lower() if state else None

    # Try exact city|state match first
    station: dict[str, Any] | None = None
    if state_norm:
        # also handle 2-letter abbreviations: match start of state name
        full_key = f"{city_norm}|{state_norm}"
        candidates = index.get(full_key)
        if not candidates and len(state_norm) == 2:
            # Try matching abbreviation against full state names
            candidates = [
                s for s in index.get(city_norm, [])
                if s["state"].lower().startswith(state_norm)
                or s["state"].lower() == state_norm
            ]
        if candidates:
            station = candidates[0]

    # Fallback: city-only
    if station is None:
        candidates = index.get(city_norm)
        if candidates:
            station = candidates[0]

    if station is None:
        raise PayDataError(
            f"No OPM duty station found for city '{city}'"
            + (f", state '{state}'" if state else "")
            + ". Use search_duty_stations() to find available cities."
        )

    # Translate the CSV locality code to our gs_pay.json code
    csv_code = station["locality_code"]
    gs_code  = _CSV_TO_GS.get(csv_code, "RUS")

    # Resolve full locality info from gs_pay data
    locs     = _load_raw()["localities"]
    loc_info = locs.get(gs_code, {})

    return {
        "locality_code": gs_code,
        "csv_locality_code": csv_code,
        "locality_name": loc_info.get("name", station["lpa"]),
        "rate_pct":      loc_info.get("rate_pct"),
        "city":          station["city"],
        "state":         station["state"],
        "lpa":           station["lpa"],
    }


def search_duty_stations(
    city_fragment: str, state: str | None = None, limit: int = 20
) -> list[dict[str, Any]]:
    """Return duty stations whose city name contains *city_fragment*.

    Useful for agents that need to discover valid city names before calling
    ``compute_pay``.

    Parameters
    ----------
    city_fragment:
        Partial or full city name (case-insensitive).
    state:
        Optional state filter (case-insensitive substring match).
    limit:
        Maximum number of results (default 20, max 100).

    Returns
    -------
    list of dict
        Each entry has ``city``, ``state``, ``lpa``, ``locality_code``.
    """
    fragment = city_fragment.strip().lower()
    state_filter = state.strip().lower() if state else None
    limit = min(limit, 100)

    results = []
    seen: set[tuple[str, str]] = set()
    for s in _load_stations():
        if fragment not in s["city"].lower():
            continue
        if state_filter and state_filter not in s["state"].lower():
            continue
        key = (s["city"].lower(), s["state"].lower())
        if key in seen:
            continue
        seen.add(key)
        gs_code = _CSV_TO_GS.get(s["locality_code"], "RUS")
        results.append({
            "city":              s["city"],
            "state":             s["state"],
            "lpa":               s["lpa"],
            "locality_code":     gs_code,
            "csv_locality_code": s["locality_code"],
        })
        if len(results) >= limit:
            break
    return results


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_metadata() -> dict[str, Any]:
    """Return the pay-table metadata block (year, source, effective date, cap)."""
    return dict(_load_raw()["metadata"])


def list_localities() -> list[dict[str, Any]]:
    """Return all locality areas as a list of ``{code, name, rate_pct}`` dicts."""
    locs = _load_raw()["localities"]
    return [
        {"code": code, "name": info["name"], "rate_pct": info["rate_pct"]}
        for code, info in locs.items()
        if not code.startswith("_")
    ]


def get_base_rate(grade: int, step: int) -> int:
    """Return the annual GS base rate for *grade*/*step*, before locality.

    Raises
    ------
    PayDataError
        If grade or step is outside the valid GS range.
    """
    if not (_MIN_GRADE <= grade <= _MAX_GRADE):
        raise PayDataError(f"GS grade must be {_MIN_GRADE}–{_MAX_GRADE}, got {grade}.")
    if not (_MIN_STEP <= step <= _MAX_STEP):
        raise PayDataError(f"GS step must be {_MIN_STEP}–{_MAX_STEP}, got {step}.")
    return _load_raw()["base_table"][str(grade)][step - 1]


def _resolve_locality(locality_code: str) -> dict[str, Any]:
    locs = _load_raw()["localities"]
    code = locality_code.upper()
    if code.startswith("_") or code not in locs:
        available = ", ".join(c for c in locs if not c.startswith("_"))
        raise PayDataError(
            f"Unknown locality code '{locality_code}'. Available: {available}."
        )
    return {"code": code, **locs[code]}


def compute_pay(
    grade: int,
    step: int,
    locality_code: str = "RUS",
    *,
    city: str | None = None,
    state: str | None = None,
) -> dict[str, Any]:
    """Compute locality-adjusted annual pay for a GS grade/step/locality.

    Applies the OPM formula ``total = base * (1 + rate/100)``, rounded to the
    nearest dollar, then enforces the Executive Level IV cap.

    If *city* is provided it takes precedence over *locality_code* — the
    city is looked up in the OPM duty-station table to determine the correct
    locality pay area.  *state* narrows the city search when the name is
    ambiguous.

    Returns
    -------
    dict
        Keys: ``grade``, ``step``, ``base_rate``, ``locality``
        (code/name/rate_pct), ``locality_adjusted``, ``pay_cap``,
        ``is_capped``, ``final_salary``, ``schedule_year``.
        When resolved via city, also includes ``duty_station``
        (city/state/lpa).
    """
    base = get_base_rate(grade, step)

    duty_station: dict[str, Any] | None = None
    if city:
        ds = resolve_locality_by_city(city, state)
        locality_code = ds["locality_code"]
        duty_station = {
            "city":              ds["city"],
            "state":             ds["state"],
            "lpa":               ds["lpa"],
            "csv_locality_code": ds["csv_locality_code"],
        }

    loc = _resolve_locality(locality_code)
    meta = _load_raw()["metadata"]
    cap = int(meta["pay_cap_executive_level_iv"])

    adjusted = round(base * (1 + loc["rate_pct"] / 100.0))
    is_capped = adjusted > cap
    final = min(adjusted, cap)

    result: dict[str, Any] = {
        "grade": grade,
        "step": step,
        "base_rate": base,
        "locality": {
            "code": loc["code"],
            "name": loc["name"],
            "rate_pct": loc["rate_pct"],
        },
        "locality_adjusted": adjusted,
        "pay_cap": cap,
        "is_capped": is_capped,
        "final_salary": final,
        "schedule_year": meta["schedule_year"],
    }
    if duty_station:
        result["duty_station"] = duty_station
    return result


def compare_pay(
    from_grade: int,
    from_step: int,
    to_grade: int,
    to_step: int,
    from_locality_code: str = "RUS",
    to_locality_code: str | None = None,
    *,
    from_city: str | None = None,
    from_state: str | None = None,
    to_city: str | None = None,
    to_state: str | None = None,
) -> dict[str, Any]:
    """Compare pay between two GS positions (grade, step, location).

    Useful for modelling promotions, within-grade step increases, and
    location changes.  Either or both positions can be specified by city
    or by locality code.

    ``to_locality_code`` defaults to ``from_locality_code`` so a pure
    grade/step comparison (same location) requires only the four grade/step
    arguments.

    Parameters
    ----------
    from_grade, from_step:      Current position grade and step.
    to_grade, to_step:          Target position grade and step.
    from_locality_code:         Current locality code (default 'RUS').
    to_locality_code:           Target locality code; defaults to same as current.
    from_city / from_state:     Current duty-station city/state (overrides from_locality_code).
    to_city / to_state:         Target duty-station city/state (overrides to_locality_code).

    Returns
    -------
    dict with keys:
        ``from``, ``to``     — full compute_pay results for each position.
        ``difference``       — ``salary_delta`` (int), ``pct_change`` (float,
                               rounded to 2 dp), ``is_promotion`` (bool),
                               ``grade_change``, ``step_change``,
                               ``locality_change`` (bool), ``summary`` (str).
    """
    from_pay = compute_pay(
        from_grade, from_step, from_locality_code,
        city=from_city, state=from_state,
    )

    # "to" locality defaults to whatever "from" actually resolved to
    # (important when from_city overrides from_locality_code).
    resolved_to_code = (
        to_locality_code
        if to_locality_code is not None
        else from_pay["locality"]["code"]
    )
    to_pay = compute_pay(
        to_grade, to_step, resolved_to_code,
        city=to_city, state=to_state,
    )

    from_salary = from_pay["final_salary"]
    to_salary   = to_pay["final_salary"]
    delta       = to_salary - from_salary
    pct_change  = round((delta / from_salary) * 100, 2) if from_salary else 0.0

    from_loc = from_pay["locality"]["code"]
    to_loc   = to_pay["locality"]["code"]
    locality_change = from_loc != to_loc

    grade_change = to_grade - from_grade
    step_change  = to_step  - from_step

    # Build a plain-English summary
    parts: list[str] = []
    if grade_change > 0:
        parts.append(f"promotion from GS-{from_grade} to GS-{to_grade}")
    elif grade_change < 0:
        parts.append(f"downgrade from GS-{from_grade} to GS-{to_grade}")
    else:
        parts.append(f"same grade GS-{from_grade}")

    if step_change > 0:
        parts.append(f"step {from_step} → {to_step}")
    elif step_change < 0:
        parts.append(f"step decrease {from_step} → {to_step}")

    if locality_change:
        from_name = from_pay["locality"]["name"]
        to_name   = to_pay["locality"]["name"]
        parts.append(f"location change ({from_name} → {to_name})")

    direction = "increase" if delta >= 0 else "decrease"
    summary = (
        f"{'; '.join(parts).capitalize()}: "
        f"${from_salary:,} → ${to_salary:,} "
        f"({direction} of ${abs(delta):,} / {abs(pct_change):.2f}%)"
    )

    return {
        "from":       from_pay,
        "to":         to_pay,
        "difference": {
            "salary_delta":      delta,
            "pct_change":        pct_change,
            "is_promotion":      grade_change > 0,
            "grade_change":      grade_change,
            "step_change":       step_change,
            "locality_change":   locality_change,
            "summary":           summary,
        },
    }
