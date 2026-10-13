# HR Policy Navigator - a Model Context Protocol (MCP) Server

An MCP server that gives AI agents governed, structured access to **federal Human
Resources policy, pay, and hiring information** drawn entirely from public
U.S. government data sources.

Built for the **GSA MCP & AI Agent Government Hackathon** (Sept-Nov 2026).

> **Scope & data notice:** This is a hackathon **prototype**. It uses only
> publicly available government data and runs in a sandbox. It is **not** deployed
> publicly and does **not** access any real employee records or PII.

---

## What it does

Federal HR rules, pay scales, and hiring requirements are spread across several
public systems (the eCFR, the Federal Register, and USAJOBS). They are accessible
to developers but not to AI agents, which lack a structured "translation layer" to
query and interpret them.

This server closes that gap. It exposes a small set of **agent-facing tools** -
each built around a real user goal rather than a raw API endpoint - so an AI
assistant can answer plain-language HR questions with authoritative, cited
information.

### Example questions an agent can answer using this server
- "What's the annual leave accrual rate for an employee with 10 years of service?"
- "What does Title 5 say about telework eligibility?"
- "Have there been any recent federal rule changes affecting the SES?"
- "What's the base pay for a GS-12 step 5 in the Washington?Baltimore locality?"
- "What qualifications are required for the 2210 (IT) occupational series?"

---

## Tools

> _This list will be finalized as the build progresses._

| Tool | Goal it serves | Primary data source(s) |
|------|----------------|------------------------|
| `look_up_hr_policy` | Find the governing regulation on an HR topic | eCFR (Title 5) |
| `check_recent_policy_changes` | Surface recent/proposed workforce rule changes | Federal Register |
| `get_pay_scale_info` | Look up GS base pay by grade/step/locality | OPM pay data _(TODO: confirm source)_ |
| `find_federal_job_requirements` | Explain qualifications for a role/series | USAJOBS |

Each tool returns **structured (Pydantic-validated) output** and raises informative
errors that an agent can reason about.

---

## Architecture

See [`docs/architecture.md`](docs/architecture.md) for diagrams and design rationale.

At a high level, the codebase separates three concerns so each can be understood in
isolation:

- **`clients/`** - raw HTTP interactions with each government API. No MCP awareness.
- **`models/`** - Pydantic models defining validated inputs and structured outputs.
- **`tools/`** - agent-facing tools that orchestrate clients and return models.

AI agent (watsonx Orchestrate / IBM Bob / Claude Desktop)
        -  MCP (stdio or HTTP)
        -
  HR Policy Navigator  --->  tools/ ---> clients/ ---> public gov APIs
   (FastMCP server)          -------> models/ (validated I/O)

---

## Data sources (all public, verified)

| Source | Auth | Notes |
|--------|------|-------|
| [eCFR API](https://www.ecfr.gov/developers/documentation/api/v1) | None | Title 5 = Administrative Personnel. Base URL `https://www.ecfr.gov`. |
| [Federal Register API](https://www.federalregister.gov/developers/documentation/api/v1) | None | Recent/proposed rules. No API key required. |
| [USAJOBS API](https://developer.usajobs.gov) | Email-keyed (free) | Requires `Authorization-Key` + `User-Agent` (registered email) headers. |

---

## Setup

### Prerequisites
- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (package manager; required for FastMCP CLI)
- A free USAJOBS API key from <https://developer.usajobs.gov/apirequest/>

### Install
```bash
# clone, then:
uv sync                      # installs locked dependencies into .venv
cp .env.example .env         # then fill in your USAJOBS credentials
```

### Configure
Edit `.env`:
```
USAJOBS_API_KEY=your_key_here
USAJOBS_USER_AGENT=your-registered-email@agency.gov
```

### Run
```bash
# Interactive debugging with the MCP Inspector (browser UI):
uv run fastmcp dev src/hr_navigator/server.py

# Run the server (stdio transport, for desktop clients):
uv run fastmcp run src/hr_navigator/server.py
```

---

## Testing & evaluation

Automated tests use an **in-process FastMCP `Client`** to call the server's tools
directly, with government API calls mocked (via `respx`) for deterministic,
offline-safe runs.

```bash
uv run pytest
```

Full testing methodology, performance metrics, and security considerations are
documented in [`docs/EVALUATION.md`](docs/EVALUATION.md).

---

## IBM stack integration

This server is intentionally **model- and host-agnostic** (the point of MCP) and is
demonstrated on the IBM ecosystem:

- **IBM Bob** - AI development partner used to build, review, and document the server.
- **watsonx.ai** - hosts the model that powers the calling agent. _(TODO: confirm model.)_
- **watsonx Orchestrate** - hosts the HR assistant agent that consumes this MCP server. _(TODO: confirm registration steps with IBM mentors.)_
- **IBM Cloud** - hosts the server when run over HTTP transport. _(TODO: confirm deployment target.)_

---

## Project status

-- In active development for the hackathon. See issues / project board for progress.

## License

_TODO: confirm license appropriate for a government hackathon submission._

## Acknowledgments

- GSA AI Community of Practice (hackathon host)
- Industry partners: IBM, Databricks, OpenAI
- Public data: Office of the Federal Register, GPO, OPM / USAJOBS
