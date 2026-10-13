# Architecture & Design Decisions

This document explains how the HR Policy Navigator is structured and the reasoning
behind key decisions. It supports the hackathon's technical-quality judging
criteria (code readability/maintainability, reliability, and adherence to MCP
design principles).

---

## 1. Design goals

1. **Agent-first, not endpoint-first.** Each tool represents a real user goal, not a
   thin wrapper around a single REST call. A tool may orchestrate several underlying
   API calls to answer one plain-language question.
2. **Separation of concerns.** HTTP logic, data models, and tool definitions live in
   distinct layers so any component can be understood and tested in isolation.
3. **Structured, validated I/O.** Pydantic models define every tool's inputs and
   outputs, giving the agent a precise contract and free input validation.
4. **Graceful failure.** API errors become clear, actionable messages the agent can
   reason about and recover from.
5. **Public data only.** No PII; all sources are public government APIs.

---

## 2. Layered structure

```
????????????????????????????????????????????????????????????
?  server.py  ? creates the FastMCP instance, registers      ?
?               tools. Deliberately thin.                     ?
????????????????????????????????????????????????????????????
                ?
        ??????????????????    goal-oriented, agent-facing
        ?    tools/       ?    (orchestration lives here)
        ?  policy_tools   ?
        ?  pay_tools      ?
        ?  job_tools      ?
        ???????????????????
            ?        ?
   ?????????????  ??????????????
   ?  models/   ?  ?  clients/   ?   raw HTTP; no MCP awareness
   ? (Pydantic) ?  ?  ecfr       ?
   ? validated  ?  ?  fed_reg    ?
   ? I/O types  ?  ?  usajobs    ?
   ??????????????  ??????????????
                          ?
                   ????????????????????????????????
                   ?  Public government APIs        ?
                   ?  eCFR · Federal Register ·     ?
                   ?  USAJOBS                        ?
                   ?????????????????????????????????
```

### Why this split
- **`clients/`** can be unit-tested against mocked HTTP responses without touching
  MCP. They know nothing about tools or the agent.
- **`models/`** are pure data definitions ? reusable, independently testable, and the
  single source of truth for what each tool accepts and returns.
- **`tools/`** read as plain descriptions of user goals. They compose client calls
  and map raw responses into models. This is where the "not a thin wrapper" property
  is realized.

---

## 3. Data source decisions

| Concern | Source | Why |
|---------|--------|-----|
| HR regulations | eCFR **Title 5** (Administrative Personnel) | Authoritative, public, no auth, daily-updated. |
| Recent rule changes | Federal Register API | Captures proposed/final rules before/as they take effect; no auth. |
| Hiring / occupational series | USAJOBS API | Official OPM source; codelists allow filtering by stable codes rather than fragile name matches. |
| GS pay | _TODO: confirm authoritative public source_ | Options under evaluation; document final choice here. |

### Known risks / caveats
- **eCFR base URL stability.** Observed signs of redirection toward
  federalregister.gov infrastructure. Mitigation: base URLs are configurable in
  `config.py`, and clients handle redirects and errors defensively.
- **USAJOBS scope.** Covers public job listings only; no internal/status data.
- **Rate limits & etiquette.** USAJOBS expects documented request etiquette and
  paging limits; codelists are fetched once and cached.

---

## 4. MCP design-principle adherence

- **Pydantic models** for all structured inputs/outputs ? validated, self-describing
  schemas generated automatically by FastMCP from type hints.
- **Precise tool names & docstrings.** Docstrings are written *for the LLM*: when to
  use the tool and what each argument means, so the agent can select tools without
  ambiguity.
- **Goal-oriented tools.** E.g., `look_up_hr_policy` performs a search then fetches
  the relevant section ? multiple calls behind one meaningful action.
- **Error handling.** Tools raise exceptions with clear messages; FastMCP converts
  them into MCP error responses the agent can act on.

---

## 5. Transport & deployment

- **stdio** (default) for local desktop clients (e.g., Claude Desktop, IBM Bob).
- **HTTP** for remote hosting and for consumption by watsonx Orchestrate.
- Hosting target: _TODO ? IBM Cloud (e.g., Code Engine); confirm with mentors._

---

## 6. Testing strategy (summary)

- In-process `fastmcp.Client` exercises each tool end-to-end without a network round
  trip.
- Government API calls mocked with `respx` for deterministic, offline-safe tests.
- A small set of "golden" real calls run manually against live APIs and are recorded
  in `docs/EVALUATION.md`.

Full detail: see [`EVALUATION.md`](EVALUATION.md).

---

## 7. Open decisions / TODO

- [ ] Finalize authoritative public source for GS pay data.
- [ ] Confirm FastMCP package variant used in the IBM sandbox (`fastmcp` vs
      `mcp.server.fastmcp`).
- [ ] Confirm watsonx model and Orchestrate registration steps.
- [ ] Confirm deployment target on IBM Cloud.
- [ ] Decide license.
