"""
HR Policy Navigator — FastMCP server entry point.

Deliberately thin: creates the FastMCP instance and registers tools.
All business logic lives in tools/ and clients/.

Run (dev mode with MCP Inspector):
    uv run fastmcp dev src/hr_navigator/server.py

Run (stdio transport for desktop clients):
    uv run fastmcp run src/hr_navigator/server.py
"""

import fastmcp

from hr_navigator.tools.pay_tools import (
    get_gs_pay_info,
    list_gs_localities,
    search_duty_stations,
)
from hr_navigator.tools.policy_tools import look_up_hr_policy

mcp = fastmcp.FastMCP(
    name="HR Policy Navigator",
    instructions=(
        "You have access to federal HR policy data from the U.S. Electronic Code of "
        "Federal Regulations (eCFR), specifically Title 5 (Administrative Personnel), "
        "and 2026 OPM General Schedule pay tables with all 58 locality areas. "
        "Use look_up_hr_policy to answer questions about federal workforce rules, "
        "leave policies, pay administration, and other HR topics governed by Title 5. "
        "Use get_gs_pay_info to compute locality-adjusted GS salaries by "
        "locality code or by actual city name. "
        "Use list_gs_localities to look up locality pay area codes and rates. "
        "Use search_duty_stations to find valid OPM duty-station city names."
    ),
)

# Register tools
mcp.tool()(look_up_hr_policy)
mcp.tool()(get_gs_pay_info)
mcp.tool()(list_gs_localities)
mcp.tool()(search_duty_stations)


def main() -> None:
    """Entry point for the `hr-navigator` CLI command."""
    mcp.run()


if __name__ == "__main__":
    main()
