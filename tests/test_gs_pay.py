"""
Tests for the GS pay data loader (clients/gs_pay) and MCP tools (tools/pay_tools).
Includes duty-station city lookup and search.

All tests use the real bundled data file — no mocking of the JSON — so they
also serve as a smoke-test that the data itself is valid and consistent with
the OPM source.
"""

from __future__ import annotations

import pytest

from hr_navigator.clients.gs_pay import (
    PayDataError,
    _load_raw,
    _load_stations,
    compute_pay,
    get_base_rate,
    get_metadata,
    list_localities,
    resolve_locality_by_city,
    search_duty_stations,
)
from hr_navigator.tools.pay_tools import (
    get_gs_pay_info,
    list_gs_localities,
    search_duty_stations as tool_search_duty_stations,
)


# ---------------------------------------------------------------------------
# Data loader — _load_raw / metadata
# ---------------------------------------------------------------------------


def test_load_raw_succeeds():
    """The bundled JSON loads without error."""
    raw = _load_raw()
    assert "base_table" in raw
    assert "localities" in raw
    assert "metadata" in raw


def test_metadata_fields():
    meta = get_metadata()
    assert meta["schedule_year"] == 2026
    assert meta["pay_cap_executive_level_iv"] == 197200
    assert "source_url" in meta


# ---------------------------------------------------------------------------
# Base rates — spot-checks against the OPM 2026-GS PDF
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("grade,step,expected", [
    (1,  1,  22584),
    (7,  1,  43106),
    (9,  1,  52727),
    (12, 1,  76463),
    (13, 5, 103049),
    (15, 10, 164301),
])
def test_get_base_rate_known_values(grade, step, expected):
    assert get_base_rate(grade, step) == expected


def test_get_base_rate_all_grades_steps_are_positive_ints():
    for grade in range(1, 16):
        for step in range(1, 11):
            rate = get_base_rate(grade, step)
            assert isinstance(rate, int) and rate > 0


def test_get_base_rate_monotonic_within_grade():
    """Each step must be >= the previous step for every grade."""
    for grade in range(1, 16):
        steps = [get_base_rate(grade, s) for s in range(1, 11)]
        assert steps == sorted(steps), f"Grade {grade} steps not monotonic: {steps}"


def test_get_base_rate_invalid_grade():
    with pytest.raises(PayDataError, match="grade must be"):
        get_base_rate(0, 1)
    with pytest.raises(PayDataError, match="grade must be"):
        get_base_rate(16, 1)


def test_get_base_rate_invalid_step():
    with pytest.raises(PayDataError, match="step must be"):
        get_base_rate(9, 0)
    with pytest.raises(PayDataError, match="step must be"):
        get_base_rate(9, 11)


# ---------------------------------------------------------------------------
# Localities
# ---------------------------------------------------------------------------


def test_list_localities_count():
    locs = list_localities()
    assert len(locs) == 58


def test_list_localities_no_nulls():
    for loc in list_localities():
        assert loc["rate_pct"] is not None, f"{loc['code']} has null rate"
        assert isinstance(loc["rate_pct"], float)


def test_list_localities_rate_bounds():
    """All rates must be within the OPM floor/ceiling band."""
    for loc in list_localities():
        assert 17.0 <= loc["rate_pct"] <= 47.0, (
            f"{loc['code']} rate {loc['rate_pct']} is out of band"
        )


@pytest.mark.parametrize("code,expected_rate", [
    ("RUS",  17.06),
    ("SF",   46.34),
    ("NY",   37.95),
    ("DCB",  33.94),
    ("HOU",  35.00),
    ("LA",   36.47),
    ("OM",   18.23),
    ("ATL",  23.79),
    ("AK",   32.36),
    ("HI",   22.21),
])
def test_locality_rates_spot_check(code, expected_rate):
    locs = {loc["code"]: loc for loc in list_localities()}
    assert locs[code]["rate_pct"] == pytest.approx(expected_rate, abs=0.01)


# ---------------------------------------------------------------------------
# compute_pay — formula, cap, defaults
# ---------------------------------------------------------------------------


def test_compute_pay_formula():
    """Verify base × (1 + rate/100), rounded to nearest dollar."""
    result = compute_pay(9, 1, "RUS")
    base = get_base_rate(9, 1)          # 52727
    expected_adjusted = round(base * (1 + 17.06 / 100))
    assert result["base_rate"] == base
    assert result["locality_adjusted"] == expected_adjusted
    assert result["final_salary"] == expected_adjusted  # below cap


def test_compute_pay_cap_enforced():
    """GS-15 step 10 in SF must hit the $197,200 cap."""
    result = compute_pay(15, 10, "SF")
    assert result["is_capped"] is True
    assert result["final_salary"] == 197200
    assert result["locality_adjusted"] > 197200


def test_compute_pay_default_locality_is_rus():
    result_default = compute_pay(9, 1)
    result_rus = compute_pay(9, 1, "RUS")
    assert result_default == result_rus


def test_compute_pay_locality_case_insensitive():
    result_upper = compute_pay(9, 1, "DCB")
    result_lower = compute_pay(9, 1, "dcb")
    assert result_upper == result_lower


def test_compute_pay_result_keys():
    result = compute_pay(7, 4, "CHI")
    expected_keys = {
        "grade", "step", "base_rate", "locality",
        "locality_adjusted", "pay_cap", "is_capped",
        "final_salary", "schedule_year",
    }
    assert set(result.keys()) == expected_keys
    assert result["locality"]["code"] == "CHI"
    assert result["schedule_year"] == 2026


def test_compute_pay_unknown_locality():
    with pytest.raises(PayDataError, match="Unknown locality code"):
        compute_pay(9, 1, "ZZZZ")


# ---------------------------------------------------------------------------
# MCP tools — async wrappers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_gs_pay_info_happy_path():
    result = await get_gs_pay_info(grade=13, step=5, locality_code="DCB")
    assert result["grade"] == 13
    assert result["step"] == 5
    assert result["locality"]["code"] == "DCB"
    assert result["final_salary"] > 0


@pytest.mark.asyncio
async def test_get_gs_pay_info_bad_locality_raises_runtime_error():
    with pytest.raises(RuntimeError, match="Unknown locality code"):
        await get_gs_pay_info(grade=9, step=1, locality_code="BADCODE")


@pytest.mark.asyncio
async def test_list_gs_localities_sorted_descending():
    locs = await list_gs_localities()
    assert len(locs) == 58
    rates = [loc["rate_pct"] for loc in locs]
    assert rates == sorted(rates, reverse=True)
    # SF should be first (highest rate)
    assert locs[0]["code"] == "SF"
    # RUS should be last (lowest rate)
    assert locs[-1]["code"] == "RUS"


# ---------------------------------------------------------------------------
# Duty stations — static data
# ---------------------------------------------------------------------------


def test_load_stations_count():
    stations = _load_stations()
    assert len(stations) == 40010


def test_load_stations_all_have_city():
    for s in _load_stations():
        assert s["city"].strip(), f"Station {s['code']} has empty city"


def test_load_stations_all_have_locality_code():
    for s in _load_stations():
        assert s["locality_code"], f"Station {s['code']} has no locality_code"


def test_load_stations_no_cbsa_csa():
    """Exported JSON must not contain CBSA or CSA fields."""
    for s in _load_stations():
        assert "cbsa" not in s
        assert "csa" not in s


# ---------------------------------------------------------------------------
# resolve_locality_by_city
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("city,state,expected_code", [
    ("Huntsville",      "Alabama",           "HNT"),
    ("Huntsville",      "AL",                "HNT"),
    ("San Francisco",   "California",        "SF"),
    ("Washington",      "District Of Columbia", "DCB"),
    ("Houston",         "Texas",             "HOU"),
    ("Chicago",         "Illinois",          "CHI"),
    ("Los Angeles",     "California",        "LA"),
    ("Denver",          "Colorado",          "DEN"),   # must specify state; multiple Denvers exist
    ("Atlanta",         "Georgia",           "ATL"),
    ("Abbeville",       "Alabama",           "RUS"),   # Rest of U.S. city
])
def test_resolve_locality_by_city_known_cities(city, state, expected_code):
    result = resolve_locality_by_city(city, state)
    assert result["locality_code"] == expected_code, (
        f"{city}, {state} → got {result['locality_code']}, expected {expected_code}"
    )
    assert isinstance(result["rate_pct"], float)
    assert result["city"].lower() == city.lower()


def test_resolve_locality_by_city_case_insensitive():
    r1 = resolve_locality_by_city("huntsville", "alabama")
    r2 = resolve_locality_by_city("HUNTSVILLE", "ALABAMA")
    assert r1["locality_code"] == r2["locality_code"]


def test_resolve_locality_by_city_unknown_raises():
    # "Atlantis" is a real city in Florida; use a truly nonexistent name
    with pytest.raises(PayDataError, match="No OPM duty station found"):
        resolve_locality_by_city("Zzzznonexistentcity", "Fictional")


def test_resolve_locality_by_city_returns_expected_keys():
    result = resolve_locality_by_city("Huntsville", "Alabama")
    assert set(result.keys()) == {"locality_code", "csv_locality_code",
                                  "locality_name", "rate_pct",
                                  "city", "state", "lpa"}


def test_resolve_locality_by_city_csv_code_preserved():
    result = resolve_locality_by_city("Huntsville", "Alabama")
    assert result["csv_locality_code"] == "HU"
    assert result["locality_code"] == "HNT"


# ---------------------------------------------------------------------------
# search_duty_stations
# ---------------------------------------------------------------------------


def test_search_duty_stations_fragment():
    results = search_duty_stations("hunts")
    assert len(results) > 0
    assert all("hunt" in r["city"].lower() for r in results)


def test_search_duty_stations_with_state_filter():
    results = search_duty_stations("Springfield", "Illinois")
    assert all("illinois" in r["state"].lower() for r in results)


def test_search_duty_stations_deduplicates():
    """Same city+state should appear only once."""
    results = search_duty_stations("Washington")
    keys = [(r["city"].lower(), r["state"].lower()) for r in results]
    assert len(keys) == len(set(keys))


def test_search_duty_stations_no_match():
    results = search_duty_stations("Zzzzqqqxxx")
    assert results == []


def test_search_duty_stations_returns_expected_keys():
    results = search_duty_stations("Denver")
    assert results
    assert set(results[0].keys()) == {"city", "state", "lpa",
                                      "locality_code", "csv_locality_code"}


def test_search_duty_stations_translates_codes():
    """locality_code must be gs_pay code; csv_locality_code must be CSV code."""
    results = search_duty_stations("Huntsville", "Alabama")
    assert results
    r = results[0]
    assert r["csv_locality_code"] == "HU"
    assert r["locality_code"] == "HNT"


# ---------------------------------------------------------------------------
# compute_pay with city
# ---------------------------------------------------------------------------


def test_compute_pay_by_city():
    result = compute_pay(9, 1, city="Huntsville", state="Alabama")
    assert result["locality"]["code"] == "HNT"
    assert "duty_station" in result
    assert result["duty_station"]["city"] == "Huntsville"
    assert result["duty_station"]["csv_locality_code"] == "HU"


def test_compute_pay_city_overrides_locality_code():
    """City takes precedence over an explicitly-passed locality_code."""
    result_city = compute_pay(9, 1, city="San Francisco", state="California")
    result_code = compute_pay(9, 1, locality_code="SF")
    assert result_city["locality"]["code"] == result_code["locality"]["code"]
    assert result_city["final_salary"] == result_code["final_salary"]


def test_compute_pay_city_no_duty_station_key_when_code_only():
    """duty_station key must not appear when resolving by code only."""
    result = compute_pay(9, 1, locality_code="RUS")
    assert "duty_station" not in result


# ---------------------------------------------------------------------------
# MCP tool — get_gs_pay_info with city
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_gs_pay_info_by_city():
    result = await get_gs_pay_info(grade=11, step=3, city="Huntsville", state="Alabama")
    assert result["locality"]["code"] == "HNT"
    assert result["duty_station"]["city"] == "Huntsville"
    assert result["duty_station"]["csv_locality_code"] == "HU"
    assert set(result["duty_station"].keys()) == {"city", "state", "lpa", "csv_locality_code"}
    assert result["final_salary"] > 0


@pytest.mark.asyncio
async def test_get_gs_pay_info_city_unknown_raises():
    with pytest.raises(RuntimeError, match="No OPM duty station found"):
        await get_gs_pay_info(grade=9, step=1, city="Zzzznonexistentcity")


@pytest.mark.asyncio
async def test_tool_search_duty_stations():
    results = await tool_search_duty_stations(city="San Jose", state="California")
    assert len(results) > 0
    assert results[0]["locality_code"] == "SF"


@pytest.mark.asyncio
async def test_tool_search_duty_stations_sorted():
    results = await tool_search_duty_stations(city="fort")
    cities = [(r["city"], r["state"]) for r in results]
    assert cities == sorted(cities)
