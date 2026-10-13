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

from hr_navigator.tools.policy_tools import look_up_hr_policy

mcp = fastmcp.FastMCP(
    name="HR Policy Navigator",
    instructions=(
        "You have access to federal HR policy data from the U.S. Electronic Code of "
        "Federal Regulations (eCFR), specifically Title 5 (Administrative Personnel). "
        "Use look_up_hr_policy to answer questions about federal workforce rules, "
        "leave policies, pay administration, and other HR topics governed by Title 5."
    ),
)

# Register tools
mcp.tool()(look_up_hr_policy)


def main() -> None:
    """Entry point for the `hr-navigator` CLI command."""
    mcp.run()


if __name__ == "__main__":
    main()
