"""
GS pay data loader.

Loads the static OPM General Schedule base table and locality rates from
``data/gs_pay_2026.json`` and exposes a small, pure API for computing
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

_DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "gs_pay_2026.json"

_MIN_GRADE, _MAX_GRADE = 1, 15
_MIN_STEP, _MAX_STEP = 1, 10


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


def compute_pay(grade: int, step: int, locality_code: str = "RUS") -> dict[str, Any]:
    """Compute locality-adjusted annual pay for a GS grade/step/locality.

    Applies the OPM formula ``total = base * (1 + rate/100)``, rounded to the
    nearest dollar, then enforces the Executive Level IV cap.

    Returns
    -------
    dict
        Keys: ``grade``, ``step``, ``base_rate``, ``locality``
        (code/name/rate_pct), ``locality_adjusted``, ``pay_cap``,
        ``is_capped``, ``final_salary``, ``schedule_year``.
    """
    base = get_base_rate(grade, step)
    loc = _resolve_locality(locality_code)
    meta = _load_raw()["metadata"]
    cap = int(meta["pay_cap_executive_level_iv"])

    adjusted = round(base * (1 + loc["rate_pct"] / 100.0))
    is_capped = adjusted > cap
    final = min(adjusted, cap)

    return {
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
