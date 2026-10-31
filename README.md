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

Requirements: Python 3.11+ and a free Google AI Studio API key (https://aistudio.google.com).

**With `make` (Linux/macOS/WSL):**

```bash
git clone https://github.com/alexmabud/emporio-musica-agent.git
cd emporio-musica-agent
cp .env.example .env          # then set LLM_MODEL and GOOGLE_API_KEY (see below)
make install                  # venv + dependencies (+ pytest and ruff)
make chat                     # interactive CLI
```

Other commands: `make ui` (Streamlit chat, optional), `make test` (deterministic tests, no LLM calls),
`make eval` (scripted scenarios, calls the LLM), `make lint`.

**Without `make`** (any OS, same result):

```bash
python -m venv .venv
source .venv/bin/activate          # Linux/macOS
# .venv\Scripts\Activate.ps1       # Windows PowerShell   (cmd.exe: .venv\Scripts\activate.bat)
pip install -r requirements-dev.txt   # runtime deps + project (editable) + pytest + ruff
                                      # runtime only: pip install -r requirements.txt
cp .env.example .env               # Windows: copy .env.example .env
python -m emporio_agent.cli        # interactive CLI (available from the agent slice)
pytest                             # tests, no LLM calls
ruff check . && ruff format --check .
```

**Environment variables** (documented in `.env.example`, which is kept in sync):

| Variable | Purpose |
|---|---|
| `LLM_MODEL` | Model in LangChain `init_chat_model` format, e.g. `google_genai:<flash-model-id>`. Pick a current Flash model in AI Studio. |
| `GOOGLE_API_KEY` | Google AI Studio key (free tier). |
| `LANGSMITH_TRACING`, `LANGSMITH_API_KEY`, `LANGSMITH_PROJECT` | Optional tracing. Leave `LANGSMITH_TRACING=false` to disable. |

The provider is chosen only by `LLM_MODEL` plus its key, so switching needs no code change. A free
alternative is Groq: `pip install langchain-groq`, then set `LLM_MODEL=groq:<model-id>` and `GROQ_API_KEY`.

## How it works

```
customer message
      │
      ▼
 ReAct agent (LangChain create_agent) ── system prompt = persona + rules + full policy manual
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

The model decides on its own whether a question needs data, policy, or neither. Pure policy questions
(hours, returns, address) never call a tool; price, stock, promotion and delivery questions always do.
`calculate_payment_options` and `escalate_to_human` are the legitimate exceptions: they apply policy rules
but need a tool to do exact math or to hand the case to a person.

## Technical decisions

| Decision | Choice | Why |
|---|---|---|
| Agent pattern | ReAct with function calling | The task is "know when to query data versus policy". A tool-calling loop lets the model make that call and keeps outputs structured. |
| Orchestration | LangChain 1.x `create_agent` | The current replacement for LangGraph's `create_react_agent`. It runs on the LangGraph runtime, so it keeps the tool loop, a checkpointer for memory and tracing. Its middleware (`after_model`, `before_model`) will be evaluated in slices 6/7 for the grounding check right after the model's reply and for trimming the history. A custom `StateGraph` is a "with more time" item. |
| Policies | Cache-augmented generation: the full manual goes into the system prompt | The manual is 8 pages, which fits easily in context. A vector store would add retrieval errors, latency and moving parts for no gain. Move to RAG if the manual grows. |
| Operational data | CSVs cleaned with pandas, loaded into SQLite, read through typed, parameterized tools | No free-form text-to-SQL: no injection risk, least privilege and deterministic results. |
| Business math | Python (`pricing.py`), never the LLM | Discounts, PIX and installments must be exact and testable. |
| Tool schemas | Pydantic arguments with enums (categories, string type) | Fewer malformed calls, and the model cannot invent a category. |
| Model | Gemini through Google AI Studio (free tier), a Flash-line model, `LLM_MODEL=google_genai:<flash-model-id>`, temperature around 0.2. Groq is the documented free alternative. | Free, fast, with solid tool calling, which is enough for this narrow domain. Provider and model are set only by `.env`, so switching to Groq (or any `init_chat_model` provider) needs no code change. |
| Rate limits (free tier) | Retry with exponential backoff on HTTP 429, and a configurable pause between scenarios in `make eval` | Free tiers throttle by minute and by day. **Planned for slices 6 and 8; not implemented yet.** |
| Memory | LangGraph checkpointer, one `thread_id` per session | Multi-turn context ("and how much is that one?") and order verification across turns. In-memory by default. |
| Interface | CLI first, Streamlit if time allows | The focus is the agent. The UI is a thin layer over the same `agent.py`. |
| Observability | Optional LangSmith tracing | Shows every tool call and routing decision when debugging. |

> **Privacy note (LGPD care).** On the free tier, Google may use the content sent to the API to improve its
> products. Everything in this project is fictional (store, customers, orders), so nothing real is sent. A
> real deployment would use a paid tier or a provider with a no-training agreement, and would also redact
> personal data in logs.

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

- **Identity check (LGPD):** `get_order_status` checks the e-mail or phone against the customer **of that
  order**. Phones are normalized to digits before comparing. If the e-mail given belongs to more than one
  customer (see customers 11 and 43 under [Data handling](#data-handling)), the phone is required to release
  the data. Customers are never identified by name.
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
| Products 135, 136, 137, 139, 140, 142 and 144: descriptions contradict the name or specs (for example a bass described as another brand, a synth described with 88 keys while specs say 61). Checked one by one: 138, 141 and 143 in the same range are coherent. | Structured fields (name, price, stock, status, specs) are trusted. The description is not shown for those seven products (`description_reliable = false`). |
| `specs` is a JSON string whose keys differ by category | Parsed on load. |
| Product rows are not sorted by ID | Sorted on load. |
| Statuses `active`, `discontinued`, `coming_soon`; at least one `active` product has zero stock | A product is sellable only if it is `active` and stock is above zero. |
| Categories 6, 7 and 8 (woodwinds, brass, orchestral strings) have no products | The agent says they are not available right now and never invents items. |
| Several promotion rows per product, only 4 active | Only `is_active = 1` is ever offered. Expired promotions are not presented as current. |
| Some order totals do not equal items × price (orders 3 and 20) | The stored `total_brl` is reported as the source of truth. |
| Product 130 is `coming_soon` yet appears in orders 3 and 14 | Reported as stored. Noted as a data inconsistency. |
| Customers with similar names | Identified only by e-mail or phone, never by name. |
| Customers 11 and 43 share the e-mail `bruno.martins@jmail.com` | The identity check runs against the customer **of that order**. If the e-mail given belongs to more than one customer, the agent asks for the phone before releasing any data. Flagged as `email_is_shared`; the rule is enforced and tested in the tools slice. |

## Assumptions

Where the brief or the data was ambiguous, a reasonable interpretation was chosen and written down here.

- **The data is a snapshot.** The latest order is from March 2026, so "pending" orders are months old. The
  agent reports status and dates as stored and does not compute delays against today's date.
- **Time zone:** the store operates in America/Campo_Grande (UTC-4). The current date and time injected in
  the prompt use this time zone.
- **Two phone numbers in the policy manual (conflict).** Section 1.2 labels (67) 3341-4444 as
  "Telefone / WhatsApp", but section 7, the section about remote service, says the WhatsApp channel is
  (67) 3321-4500. Rule used: **(67) 3341-4444 is the store's landline (calls) and (67) 3321-4500 is the
  WhatsApp number**, because the dedicated section is the more specific source.
- **Store e-mail:** it is written once with an accent in the manual; that is treated as a typo and
  `contato@emporiodamusica.com.br` is used.
- **Installment rule.** "Up to 3x with no minimum, except below R$ 50" is ambiguous. It is interpreted as a
  minimum of R$ 50 per installment.
- **Scope:** the store sells instruments only. Accessories such as strings, picks, cables, cases, pedals and
  amps are politely redirected.
- **PIX discount** does not stack on promotional prices, following the policy manual.
- **1x on a credit card** is treated as a payment in full: it has no minimum installment.
- **Installment cents:** when the total does not divide evenly, the leftover cents go to the first
  installment (R$ 1.000 in 3x is 1x R$ 333,34 + 2x R$ 333,33), so installments always add up to the total.
  The agent states every value, so what the customer sees is what was calculated.
- **Quantity:** the combined-payment rule (above R$ 2.000) uses the total with the quantity.

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
- Payment options are calculated per product (with a quantity). Carts with several different products are
  out of scope: the agent calculates item by item and says the cart is closed by the team.

## With more time

- Vector RAG with re-ranking if the policy base grows.
- A custom LangGraph `StateGraph` with explicit nodes (classify, retrieve, answer, validate).
- LLM-as-judge evaluation on top of the trajectory and key-fact checks.
- FastAPI, Docker and a WhatsApp integration.
- PostgreSQL instead of SQLite, and persistent per-customer memory across sessions.
- A cart with several products, with PIX eligibility decided per item.
- PII redaction in logs and a real ticketing integration for escalations.
- Provider prompt caching of the static prefix (persona plus full policy). It is not controllable on the
  free tier, so it is out of scope for now.

## Use of AI coding assistants

**TODO — rewrite from the real history once the build is done.** Planned description:

- **Planning: Claude (chat).** Used first for the initial analysis of the data, the design of the stack and
  the writing of [`CLAUDE.md`](CLAUDE.md), the spec that drives the rest of the work.
- **Implementation: Claude Code in VS Code**, following the skills in [`.claude/skills/`](.claude/skills/)
  (spec, TDD slices, verification, review). Skills and `CLAUDE.md` are versioned so the workflow is visible.
- **Division of work:** the assistant drafts code and tests slice by slice, test first. Each slice is
  verified (tests and lint) and then **reviewed and committed by me**. Design decisions, data-quality
  findings and the scope are mine to approve.

## Roadmap

1. Project scaffold, Makefile, requirements, `.env.example`, skills
2. Data loader with cleaning rules and SQLite
3. Pricing rules (promo, PIX, installments) with tests
4. Agent tools with Pydantic schemas and tests
5. Policy extraction and system prompt
6. `create_agent` agent with memory, and CLI (with retry on 429)
7. Grounding validation with tests
8. Eval scenarios and runner
9. CI (ruff and pytest)
10. Streamlit UI (optional)
11. Example conversations
12. Final README
