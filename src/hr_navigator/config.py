"""
Central configuration for HR Policy Navigator.

Base URLs are read from environment variables so they can be swapped
without code changes — important given the eCFR migration risk noted
in docs/architecture.md §3.
"""

import os

from dotenv import load_dotenv

load_dotenv()

# eCFR (Title 5 — Administrative Personnel)
ECFR_BASE_URL: str = os.getenv("ECFR_BASE_URL", "https://www.ecfr.gov")

# Federal Register (proposed / final rules)
FEDERAL_REGISTER_BASE_URL: str = os.getenv(
    "FEDERAL_REGISTER_BASE_URL", "https://www.federalregister.gov"
)

# USAJOBS
USAJOBS_BASE_URL: str = os.getenv("USAJOBS_BASE_URL", "https://data.usajobs.gov")
USAJOBS_API_KEY: str | None = os.getenv("USAJOBS_API_KEY")
USAJOBS_USER_AGENT: str | None = os.getenv("USAJOBS_USER_AGENT")

# Default Title 5 = Administrative Personnel (the scope of this project)
DEFAULT_CFR_TITLE: int = 5

# Number of search results to expand into full section text by default
DEFAULT_MAX_RESULTS: int = 3
