"""
eCFR API client.

Handles all raw HTTP interactions with the Electronic Code of Federal
Regulations API (https://www.ecfr.gov/developers/documentation/api/v1).

This module has *no* MCP or tool awareness — it is a plain async HTTP
client that can be exercised and tested independently of the MCP layer.

eCFR API overview
-----------------
- Search endpoint  : GET /api/search/v1/results
- Full text (XML)  : GET /api/versioner/v1/full/{date}/title-{n}.xml
- Title structure  : GET /api/versioner/v1/structure/{date}/title-{n}.json

All endpoints are public (no auth required).
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

import httpx

from hr_navigator.config import ECFR_BASE_URL

# Timeout applied to every outbound request
_TIMEOUT = httpx.Timeout(15.0, connect=5.0)


class ECFRError(Exception):
    """Raised when the eCFR API returns an unexpected response."""


class ECFRClient:
    """Async HTTP client for the eCFR v1 API.

    Usage::

        async with ECFRClient() as client:
            hits = await client.search("annual leave", title=5)
            text = await client.get_section(title=5, part=630, section=202)
    """

    def __init__(self, base_url: str = ECFR_BASE_URL) -> None:
        self._base_url = base_url.rstrip("/")
        self._client: httpx.AsyncClient | None = None

    # ------------------------------------------------------------------
    # Context-manager support
    # ------------------------------------------------------------------

    async def __aenter__(self) -> "ECFRClient":
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=_TIMEOUT,
            follow_redirects=True,
            headers={"Accept": "application/json"},
        )
        return self

    async def __aexit__(self, *_: Any) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @property
    def _http_client(self) -> httpx.AsyncClient:
        assert self._client is not None
        return self._client

    async def _get(self, path: str, **params: Any) -> Any:
        """Issue a GET request and return the parsed JSON body.

        Raises :class:`ECFRError` for non-2xx responses.
        """
        try:
            response = await self._http_client.get(path, params=params)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ECFRError(
                f"eCFR API error {exc.response.status_code} for {exc.request.url}: "
                f"{exc.response.text[:200]}"
            ) from exc
        except httpx.RequestError as exc:
            raise ECFRError(
                f"eCFR API request failed: {exc}"
            ) from exc
        return response.json()

    async def _get_xml(self, path: str, **params: Any) -> str:
        """Issue a GET request and return raw XML text.

        Uses the shared client from ``__aenter__``, overriding the default
        ``Accept: application/json`` header for this request only.

        Raises :class:`ECFRError` for non-2xx responses.
        """
        try:
            response = await self._http_client.get(
                path, params=params, headers={"Accept": "application/xml"}
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ECFRError(
                f"eCFR API error {exc.response.status_code} for {exc.request.url}: "
                f"{exc.response.text[:200]}"
            ) from exc
        except httpx.RequestError as exc:
            raise ECFRError(f"eCFR API request failed: {exc}") from exc
        return response.text

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def search(
        self,
        query: str,
        title: int = 5,
        max_results: int = 10,
    ) -> list[dict[str, Any]]:
        """Full-text search within a CFR title.

        Parameters
        ----------
        query:
            Plain-language search string (e.g. "annual leave accrual").
        title:
            CFR title number to restrict the search (default 5 =
            Administrative Personnel).
        max_results:
            Maximum number of results to return (1–20).

        Returns
        -------
        list[dict]
            Each dict is one search hit from the eCFR results array,
            containing at minimum ``hierarchy``, ``headings``, and
            ``full_text_excerpt`` keys (when present in the API response).

        Raises
        ------
        ECFRError
            On HTTP or network failure.

        Notes
        -----
        The eCFR search API does not accept server-side title filtering
        (``conditions[*]`` and ``filter[*]`` params return HTTP 400).
        Title filtering is applied client-side after fetching results.
        """
        per_page = min(max(1, max_results), 20)
        data = await self._get(
            "/api/search/v1/results",
            query=query,
            per_page=per_page,
            page=1,
        )
        results: list[dict[str, Any]] = data.get("results", [])
        # Filter to the requested title client-side
        if title:
            results = [
                r for r in results
                if str(r.get("hierarchy", {}).get("title", "")) == str(title)
            ]
        return results[:max_results]

    async def get_section(
        self,
        title: int,
        part: int,
        section: int,
        as_of: date | None = None,
    ) -> str:
        """Fetch the full XML text for one CFR section.

        Parameters
        ----------
        title:
            CFR title number (e.g. 5).
        part:
            CFR part number (e.g. 630).
        section:
            CFR section number within the part (e.g. 202).
        as_of:
            Date for the point-in-time version. Defaults to today.

        Returns
        -------
        str
            Raw XML string for the section.  Callers are responsible for
            extracting the human-readable prose (see
            :func:`extract_text_from_xml`).

        Raises
        ------
        ECFRError
            On HTTP or network failure, or if the section does not exist.
        """
        if as_of:
            effective_date = as_of.isoformat()
        else:
            # The versioner rejects dates past the title's latest_issue_date,
            # so we fetch that date dynamically rather than using date.today().
            effective_date = await self._get_latest_issue_date(title)
        path = f"/api/versioner/v1/full/{effective_date}/title-{title}.xml"
        xml = await self._get_xml(
            path,
            # eCFR supports section-level subsetting via the `section` param
            part=str(part),
            section=f"{part}.{section}",
        )
        return xml

    async def _get_latest_issue_date(self, title: int) -> str:
        """Return the latest_issue_date string for a title from the versioner."""
        data = await self._get("/api/versioner/v1/titles")
        for t in data.get("titles", []):
            if t.get("number") == title:
                return t["latest_issue_date"]
        # Fallback: walk back from today until we find a valid date
        return (date.today()).isoformat()

    async def get_title_structure(
        self,
        title: int = 5,
        as_of: date | None = None,
    ) -> dict[str, Any]:
        """Return the JSON structural outline for a CFR title.

        Useful for discovering which parts and sections exist before
        issuing targeted :meth:`get_section` calls.

        Parameters
        ----------
        title:
            CFR title number (default 5).
        as_of:
            Point-in-time date. Defaults to today.

        Returns
        -------
        dict
            The raw JSON structure returned by the eCFR API.

        Raises
        ------
        ECFRError
            On HTTP or network failure.
        """
        if as_of:
            effective_date = as_of.isoformat()
        else:
            effective_date = await self._get_latest_issue_date(title)
        return await self._get(
            f"/api/versioner/v1/structure/{effective_date}/title-{title}.json"
        )


# ---------------------------------------------------------------------------
# Utility: extract readable text from eCFR XML
# ---------------------------------------------------------------------------

def extract_text_from_xml(xml: str, max_chars: int = 1500) -> str:
    """Strip XML tags and return a plain-text excerpt from eCFR section XML.

    This is intentionally simple (regex-based) so it works without an XML
    parser dependency.  The eCFR XML is well-structured and does not contain
    CDATA sections that would trip up this approach.

    Parameters
    ----------
    xml:
        Raw XML string from :meth:`ECFRClient.get_section`.
    max_chars:
        Truncate the result at this many characters and append "…".

    Returns
    -------
    str
        Human-readable prose extracted from the XML, trimmed to *max_chars*.
    """
    # Remove XML tags
    text = re.sub(r"<[^>]+>", " ", xml)
    # Collapse runs of whitespace
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + "…"
    return text
