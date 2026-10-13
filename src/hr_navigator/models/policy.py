"""
Pydantic models for the look_up_hr_policy tool.

These models are the single source of truth for what the tool accepts
and returns.  FastMCP generates JSON Schema from them automatically,
giving the calling agent a precise, self-describing contract.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Input model
# ---------------------------------------------------------------------------


class PolicyLookupInput(BaseModel):
    """Input parameters for look_up_hr_policy."""

    topic: Annotated[
        str,
        Field(
            description=(
                "Plain-language description of the HR policy topic to look up. "
                "Examples: 'annual leave accrual', 'telework eligibility', "
                "'probationary period', 'reduction in force'."
            ),
            min_length=2,
            max_length=300,
        ),
    ]

    title: Annotated[
        int,
        Field(
            default=5,
            description=(
                "CFR title number to search. Default is 5 (Administrative Personnel), "
                "which covers the vast majority of federal HR policy. Change only if "
                "you have a specific reason to search a different title."
            ),
            ge=1,
            le=50,
        ),
    ] = 5

    max_results: Annotated[
        int,
        Field(
            default=3,
            description=(
                "Maximum number of regulatory sections to return (1–5). "
                "Higher values increase response time."
            ),
            ge=1,
            le=5,
        ),
    ] = 3


# ---------------------------------------------------------------------------
# Output models
# ---------------------------------------------------------------------------


class PolicyResult(BaseModel):
    """One matching regulatory section returned by look_up_hr_policy."""

    section_ref: Annotated[
        str,
        Field(
            description=(
                "Human-readable CFR citation, e.g. '5 CFR § 630.202'. "
                "Use this when citing the source in your answer."
            )
        ),
    ]

    heading: Annotated[
        str,
        Field(description="Official section heading / title from the CFR."),
    ]

    part: Annotated[
        int,
        Field(description="CFR part number (e.g. 630 for Leave)."),
    ]

    section: Annotated[
        int,
        Field(description="CFR section number within the part (e.g. 202)."),
    ]

    cfr_title: Annotated[
        int,
        Field(description="CFR title number (almost always 5 for federal HR topics)."),
    ]

    text_excerpt: Annotated[
        str,
        Field(
            description=(
                "Up to ~1 500 characters of the section's regulatory text. "
                "This is the authoritative source — quote it directly when answering."
            )
        ),
    ]

    url: Annotated[
        str,
        Field(
            description=(
                "Direct link to this section on ecfr.gov. Include in responses "
                "so the user can read the full text."
            )
        ),
    ]

    retrieved_at: Annotated[
        datetime,
        Field(
            description=(
                "UTC timestamp of when this result was retrieved. "
                "Useful for noting how current the information is."
            )
        ),
    ]

    @field_validator("section_ref", mode="before")
    @classmethod
    def _normalise_section_ref(cls, v: str) -> str:
        return v.strip()


class PolicyLookupOutput(BaseModel):
    """Structured output from look_up_hr_policy."""

    results: Annotated[
        list[PolicyResult],
        Field(
            description=(
                "Matching regulatory sections, ordered by relevance. "
                "Empty list means no matching sections were found in the CFR."
            )
        ),
    ]

    query_topic: Annotated[
        str,
        Field(description="The topic that was searched, echoed back for traceability."),
    ]

    cfr_title_searched: Annotated[
        int,
        Field(description="The CFR title that was searched."),
    ]
