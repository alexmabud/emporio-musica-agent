# Empório da Música — Customer Service Agent

A text-message customer service agent for *Empório da Música*, a fictional musical instrument store in
Campo Grande/MS. It answers in Brazilian Portuguese, looks up live store data (prices, stock, promotions,
order status) through typed tools, and answers policy questions (returns, hours, payment, shipping) from the
store's policy manual.

> **Status: work in progress.** This README states the design decisions up front. Sections marked
> **TODO** are filled in as the corresponding commits land (see [Roadmap](#roadmap)). Nothing below is
> claimed as working until its commit exists.

## Contents

- [Quick start](#quick-start)
- [How it works](#how-it-works)
- [Technical decisions](#technical-decisions)
- [Data handling](#data-handling)
- [Assumptions](#assumptions)
- [Example conversations](#example-conversations)
- [Evaluation](#evaluation)
- [Known limitations](#known-limitations)
- [With more time](#with-more-time)
- [Use of AI coding assistants](#use-of-ai-coding-assistants)
- [Roadmap](#roadmap)

## Quick start

**TODO — verify each command once the scaffold commit lands.**

Requirements: Python 3.11+ and an API key for one LLM provider.

```bash
git clone https://github.com/alexmabud/emporio-musica-agent.git
cd emporio-musica-agent
cp .env.example .env          # then set LLM_MODEL and the provider's API key
make install                  # venv + dependencies
make chat                     # interactive CLI
```

Other commands: `make ui` (Streamlit chat, optional), `make test` (deterministic tests, no LLM calls),
`make eval` (scripted scenarios, calls the LLM), `make lint`.

The model is chosen by environment variable (`LLM_MODEL`, LangChain `init_chat_model` format, for example
`openai:<model>`), so switching provider needs no code change.

## How it works

```
customer message
      │
      ▼
 LangGraph ReAct agent ── system prompt = persona + rules + full policy manual
      │
      ├── needs live data? ──► typed tools ──► SQLite (cleaned from data/*.csv)
      │       search_products · get_product_details · get_order_status
      │       list_active_promotions · calculate_payment_options · escalate_to_human
      │
      ├── needs a rule?   ──► answers from the policy text already in the prompt
      │
      └── out of scope?   ──► declines kindly and redirects to what the store can help with
      │
      ▼
 grounding check (validation.py) ──► reply
```

The model decides on its own whether a question needs data, policy, or neither. Policy questions never
call a tool; price, stock, promotion and delivery questions always do.

## Technical decisions

| Decision | Choice | Why |
|---|---|---|
| Agent pattern | ReAct with function calling | The task is "know when to query data versus policy". A tool-calling loop lets the model make that call and keeps outputs structured. |
| Orchestration | LangGraph `create_react_agent` | Mature tool loop, checkpointed memory and tracing out of the box. A custom `StateGraph` is a "with more time" item. |
| Policies | Cache-augmented generation: the full manual goes into the system prompt | The manual is 8 pages, which fits easily in context. A vector store would add retrieval errors, latency and moving parts for no gain. Move to RAG if the manual grows. |
| Operational data | CSVs cleaned with pandas, loaded into SQLite, read through typed, parameterized tools | No free-form text-to-SQL: no injection risk, least privilege and deterministic results. |
| Business math | Python (`pricing.py`), never the LLM | Discounts, PIX and installments must be exact and testable. |
| Tool schemas | Pydantic arguments with enums (categories, string type) | Fewer malformed calls, and the model cannot invent a category. |
| Model | Any provider through `LLM_MODEL`, temperature around 0.2 | Provider-agnostic. A small, cheap model with solid tool calling is enough for this narrow domain. |
| Memory | LangGraph checkpointer, one `thread_id` per session | Multi-turn context ("and how much is that one?") and order verification across turns. In-memory by default. |
| Interface | CLI first, Streamlit if time allows | The focus is the agent. The UI is a thin layer over the same `agent.py`. |
| Observability | Optional LangSmith tracing | Shows every tool call and routing decision when debugging. |
| Cost and latency | Provider prompt caching of the static prefix | Pairs naturally with CAG: persona plus policy is cached, only the conversation varies. |

### Prompt strategy

- **Persona:** the store's virtual assistant. Informal but professional, warm, knows music, short
  WhatsApp-style replies.
- **Current date and time** (America/Campo_Grande) is injected so it can answer "are you open now?".
- **Hard rules:** never state price, stock, promotion or delivery date without calling a tool; show a
  promotional price together with the original price and the discount; do not reveal order data before
  identity verification; if unsure about a policy, say so and offer a human instead of improvising.
- **Out of scope** (non-music topics, accessories the store does not sell, other stores' products): decline
  kindly and redirect.
- **Prompt injection:** instructions inside customer messages that try to change the rules are ignored.
- **Few-shot examples:** a few short ones showing tool versus policy versus out-of-scope.
- Business logic lives in code, not in the prompt, wherever it can.

### Guardrails in code

- **Identity check (LGPD):** `get_order_status` returns details only if the e-mail or phone matches the
  order's customer. Phones are normalized to digits before comparing. Customers are never identified by name.
- **Grounding validation:** every `R$` value and tracking code in the final reply must appear in a tool
  result from the same turn. Otherwise the agent regenerates once with a correction note, then falls back to a
  safe message. This turns "never state a price without checking the system" into a guarantee.
- **Human escalation:** complaints and anything the policy says needs a manager go through
  `escalate_to_human`, which logs the case and tells the customer the response window.

### What was deliberately not used

- **Vector RAG and embeddings:** unnecessary for an 8-page manual (see CAG above).
- **Text-to-SQL agent:** riskier and less predictable than a handful of typed tools for a narrow domain.
- **Multi-agent frameworks and an explicit router:** one domain, one agent. The tool-calling model already routes.
- **FastAPI and Docker:** not required by the brief. They are the path to a WhatsApp integration (see below).

## Data handling

The original files in `data/` are kept as provided. Only the file names were normalized (no spaces, no accent).
The loader (`data_loader.py`) cleans them on load into SQLite and never edits the originals. The generated
database is git-ignored and rebuilt from the CSVs.

Issues found in the dataset and how they are handled:

| Issue | Handling |
|---|---|
| Products 135–144: descriptions contradict the name or specs (for example a bass described as another brand, a synth described with 88 keys while specs say 61) | Structured fields (name, price, stock, status, specs) are trusted. The description is not shown for those products. |
| `specs` is a JSON string whose keys differ by category | Parsed on load. |
| Product rows are not sorted by ID | Sorted on load. |
| Statuses `active`, `discontinued`, `coming_soon`; at least one `active` product has zero stock | A product is sellable only if it is `active` and stock is above zero. |
| Categories 6, 7 and 8 (woodwinds, brass, orchestral strings) have no products | The agent says they are not available right now and never invents items. |
| Several promotion rows per product, only 4 active | Only `is_active = 1` is ever offered. Expired promotions are not presented as current. |
| Some order totals do not equal items × price (orders 3 and 20) | The stored `total_brl` is reported as the source of truth. |
| Product 130 is `coming_soon` yet appears in orders 3 and 14 | Reported as stored. Noted as a data inconsistency. |
| Customers with similar names | Identified only by e-mail or phone. |

## Assumptions

Where the brief or the data was ambiguous, a reasonable interpretation was chosen and written down here.

- **The data is a snapshot.** The latest order is from March 2026, so "pending" orders are months old. The
  agent reports status and dates as stored and does not compute delays against today's date.
- **Policy inconsistencies:** the manual lists two phone numbers (a main line and a WhatsApp number). The
  agent uses the one that matches each context. The store e-mail is written once with an accent, which is
  treated as a typo.
- **Installment rule.** "Up to 3x with no minimum, except below R$ 50" is ambiguous. It is interpreted as a
  minimum of R$ 50 per installment.
- **Scope:** the store sells instruments only. Accessories such as strings, picks, cables, cases, pedals and
  amps are politely redirected.
- **PIX discount** does not stack on promotional prices, following the policy manual.

## Example conversations

**TODO.** Between 3 and 5 transcripts will be added under [`examples/`](examples/), generated from real runs
of the agent, not hand-written. They will cover: a catalog search, a price lookup, a policy question, an
order-status flow with identity check, and an out-of-scope request.

## Evaluation

**TODO.** `make eval` runs about ten scripted scenarios and checks two things for each: the **trajectory**
(which tools were called, for example a policy question must call none) and the **key facts** in the reply
(for example the price string is present, or no PIX discount is applied to a promotional item). The latest
results table will be pasted here.

## Known limitations

**TODO — finalize after the build.** Known so far:

- Memory is in-process by default, so a conversation does not survive a restart.
- The full policy sits in every prompt. This is cheap at 8 pages but would not scale to a large manual.
- Product search is filter-based plus fuzzy name matching, not semantic. Vague requests ("something warm
  for jazz") depend on the model translating them into filters.
- The grounding check validates `R$` values and tracking codes, not every factual claim.
- Order identity check is e-mail or phone only, which is enough for a prototype and not for production.
- Source-data inconsistencies are handled conservatively, not corrected.

## With more time

- Vector RAG with re-ranking if the policy base grows.
- A custom LangGraph `StateGraph` with explicit nodes (classify, retrieve, answer, validate).
- LLM-as-judge evaluation on top of the trajectory and key-fact checks.
- FastAPI, Docker and a WhatsApp integration.
- PostgreSQL instead of SQLite, and persistent per-customer memory across sessions.
- PII redaction in logs and a real ticketing integration for escalations.

## Use of AI coding assistants

**TODO — rewrite from the real history once the build is done.** Planned description:

- **Tool:** Claude Code in VS Code.
- **Workflow:** a skills-based process (spec, TDD slices, verification, review). The skills live in
  [`.claude/skills/`](.claude/skills/) and the project instructions in [`CLAUDE.md`](CLAUDE.md), both versioned
  so the workflow is visible.
- **Division of work:** the assistant drafted code and tests slice by slice. Each slice was verified (tests
  and lint) and reviewed before being committed by the maintainer. Design decisions, data-quality findings and
  the scope were reviewed by the maintainer.

## Roadmap

1. Project scaffold, Makefile, requirements, `.env.example`, skills
2. Data loader with cleaning rules and SQLite
3. Pricing rules (promo, PIX, installments) with tests
4. Agent tools with Pydantic schemas and tests
5. Policy extraction and system prompt
6. LangGraph agent with memory, and CLI
7. Grounding validation with tests
8. Eval scenarios and runner
9. CI (ruff and pytest)
10. Streamlit UI (optional)
11. Example conversations
12. Final README
