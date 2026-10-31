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
python -m emporio_agent.cli        # interactive CLI ('sair' to leave, '/novo' for a new chat)
pytest                             # tests, no LLM calls
ruff check . && ruff format --check .
```

**Environment variables** (documented in `.env.example`, which is kept in sync):

| Variable | Purpose |
|---|---|
| `LLM_MODEL` | Model in LangChain `init_chat_model` format, e.g. `google_genai:gemini-3.5-flash` (the tested default, see [Why this model](#why-this-model)). |
| `GOOGLE_API_KEY` | Google AI Studio key (free tier), used when `LLM_MODEL` starts with `google_genai:`. |
| `GROQ_API_KEY` | Groq key (free tier), used when `LLM_MODEL` starts with `groq:`. |
| `LLM_FALLBACK_MODELS` | Optional reserve models, comma-separated, tried in order when the main one fails (see [Free-tier quota](#free-tier-quota)). |
| `LANGSMITH_TRACING`, `LANGSMITH_API_KEY`, `LANGSMITH_PROJECT` | Optional tracing. Leave `LANGSMITH_TRACING=false` to disable. |

Two providers are supported and **only `LLM_MODEL` selects one**; the code does not change. Keep both
keys in `.env`: the one that `LLM_MODEL` does not use is ignored. `.env.example` has exactly one active
`LLM_MODEL` line and the other one commented out. Both client packages are in `requirements.txt`.

| `LLM_MODEL` | Role | Free-tier limits |
|---|---|---|
| `google_genai:gemini-3.5-flash` | Reference model: final evaluation and example conversations | about 20 requests per day per model |
| `groq:openai/gpt-oss-120b` | Development model: prompt iteration, scenario by scenario | 1,000 requests per day, but 8,000 tokens per minute |

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
| Policies | Cache-augmented generation: the full manual goes into the system prompt | The manual is 8 pages: with the persona and rules the whole prompt is about 4,000 tokens (measured with Gemini), under 0.5% of the context window. A vector store would add retrieval errors, latency and moving parts for no gain. Move to RAG if the manual grows. |
| Operational data | CSVs cleaned with pandas, loaded into SQLite, read through typed, parameterized tools | No free-form text-to-SQL: no injection risk, least privilege and deterministic results. |
| Business math | Python (`pricing.py`), never the LLM | Discounts, PIX and installments must be exact and testable. |
| Tool schemas | Pydantic arguments with enums (categories, string type) | Fewer malformed calls, and the model cannot invent a category. |
| Model | Two providers, two roles, chosen only by `LLM_MODEL`: **`gemini-3.5-flash`** (Google AI Studio) as the reference and **`openai/gpt-oss-120b`** (Groq) for development. Temperature around 0.2. See [Why this model](#why-this-model). | Both are free and support tool calling. Gemini's daily quota is too small to iterate on a prompt, Groq's is large but slow; each does the job the other cannot. |
| Rate limits (free tier) | Retry with exponential backoff (up to 4 retries, waiting 4, 8, 16 and 32 s, about a minute) for per-minute limits, 503/504 and network errors; **no retry for a daily quota**; a chain of reserve models; a polite message when everything is exhausted. A configurable pause between scenarios in `make eval` is still planned (slice 8). | Both free tiers throttle, in different ways (see [Free-tier quota](#free-tier-quota)). |
| Memory | LangGraph checkpointer, one `thread_id` per session | Multi-turn context ("and how much is that one?") and order verification across turns. In-memory by default. |
| Interface | CLI first, Streamlit if time allows | The focus is the agent. The UI is a thin layer over the same `agent.py`. |
| Observability | Optional LangSmith tracing | Shows every tool call and routing decision when debugging. |

> **Privacy note (LGPD care).** On the free tier, Google may use the content sent to the API to improve its
> products. Everything in this project is fictional (store, customers, orders), so nothing real is sent. A
> real deployment would use a paid tier or a provider with a no-training agreement, and would also redact
> personal data in logs.

### Why this model

**What the model has to do.** This agent needs four things from an LLM: reliable **tool calling** (it
must choose between six tools, or none), good **Brazilian Portuguese** with a friendly tone, a context
window big enough to hold the whole policy manual (about 14,000 characters, a few thousand tokens), and a
**free** way to run it, so that a reviewer can reproduce the project without a credit card.

**The free options considered.**

| Option | Free | Tested here? | Verdict |
|---|---|---|---|
| **Gemini (Google AI Studio)** | Yes, free tier with rate limits | **Yes** (models, tool calling and routing below) | **Chosen.** Fast, supports tool calling, large context, one API key. |
| **Groq (hosted open-weight models)** | Yes, free tier with rate limits | **Yes** (smoke test and routing below) | **Chosen for development.** Large daily limit, very fast, but a small per-minute token budget. |
| Local model (for example through Ollama) | Yes, runs on the reviewer's machine | **No** | Best for privacy (nothing leaves the machine), but it needs hardware and a model download, and small local models are generally less reliable at tool calling. Not chosen for a prototype that must run anywhere. |
| Paid APIs | No | No | Ruled out by the "free and reproducible" requirement. |

**Two providers, two roles.** The free tier of Gemini allows only about 20 requests per day per model, which
is not enough to iterate on a prompt, and the tier of Groq allows 1,000. So:

| Role | Model | Used for |
|---|---|---|
| **Reference** | Gemini `gemini-3.5-flash` | The final evaluation and the conversations in `examples/`, each run **once**, with a full daily quota |
| **Development** | Groq `openai/gpt-oss-120b` | Prompt iteration and tests, scenario by scenario |
| Emergency only | Billing on Google AI Studio | If the free quota blocks the delivery |

A prompt tuned on the development model can behave differently on the reference one, so every claim about
the final behaviour must come from a run on the reference model. The code does not depend on the provider.

**Which Groq model.** The key's account lists 11 models; only three are text chat models with tool calling
(the API reports it in `supported_features`). They ran the same routing check as the Gemini models, but with
the **project's real prompt and tools** and ten questions, looking at the first decision (which tool, or none):

| Groq model | Right | Misses | Free-tier note |
|---|---|---|---|
| **`openai/gpt-oss-120b`** | **10/10** | none | 8,000 tokens/min |
| `openai/gpt-oss-20b` | 9/10 | "Tem saxofone?" went to `get_product_details` | 8,000 tokens/min |
| `qwen/qwen3.8-27b` | 9/10 | did **not** escalate the complaint | 8,000 tokens/min and only **1,000 output tokens/min**, so each reply took about 45 s |

`gpt-oss-120b` is the development model: it was the only one with no miss, including the complaint that the
Flash-Lite models skipped. These numbers come from a single run and are not comparable with the Gemini ones
below (a shorter prompt and eight questions); the comparison that counts will be produced by `make eval` on
each model.

**Why `gemini-3.5-flash` inside the Gemini family.** The model was chosen by testing, not by name, on 2026-10-31 with the project's
own API key. The key listed 26 "flash" models; ignoring preview, speech, image and live variants left these
candidates:

| Model | Result in the tests |
|---|---|
| `gemini-3.8-flash`, `gemini-3.7-flash` | The newest, but **unavailable when tested**: HTTP 503 ("high demand") on both attempts, and 504 on the 3.8. |
| `gemini-3.6-flash` | Worked; the first call took 48 s. |
| **`gemini-3.5-flash`** | **Worked: correct tool call and a final answer that quotes the tool's exact value (`R$ 2.199,00`).** |
| `gemini-3.5-flash-lite`, `gemini-3.1-flash-lite` | Worked and are faster (about 1 s), but see below. |
| `gemini-2.5-flash` | Not available to new users (404). |

Flash against Flash-Lite was decided with a small routing check on the **project's real tools**: eight
questions, looking only at the model's first decision (which tool to call, or none).

| Model | Right | Average latency | Misses |
|---|---|---|---|
| `gemini-3.5-flash` | 7/8 | 2.0 s | Asked for the PIX price of a product and called `get_product_details` instead of `calculate_payment_options`. Harmless: that tool also returns the PIX price. |
| `gemini-3.1-flash-lite` | 7/8 | 2.3 s | Did **not** escalate a complaint about a broken guitar; chose the wrong category (brass) for a saxophone. |
| `gemini-3.5-flash-lite` | 7/8 | 1.1 s | Same two misses. It also ignores the `temperature` setting. |

The scores tie, but the Lite misses are the costly kind (a complaint left without a human, a wrong
category), so Flash is the default and Flash-Lite is the plan B if the free-tier daily limit runs out.

**What this does not prove.** The routing check ran once, with a short prompt and no escalation
instruction, so the complaint miss may disappear with the real prompt. The newest models may become
available later and are worth re-testing. Free-tier quotas are not exposed by the API, so they were not
compared; check them in the AI Studio dashboard. A reproducible version of this routing check will live in
`make eval` (slice 8).

### Free-tier quota

**The Gemini free tier allows only about 20 requests per day per model** (the API reports it as
`GenerateRequestsPerDayPerProjectPerModel-FreeTier`, limit 20; it resets after roughly half a day). This was
found by running the real agent, not from the documentation. It matters because one customer message that
needs a tool already costs **two** requests (the model asks for the tool, then answers), so a single model
serves roughly ten such questions per day, and the testing done for this project used up the main model's
quota on its first day.

What the agent does about it:

- **The daily quota is never retried** (waiting seconds cannot fix it); per-minute limits, 503/504 and
  network errors are retried with backoff.
- **Quotas are per model, so reserve models multiply the capacity.** `LLM_FALLBACK_MODELS` lists models that
  take over, in order, when the main one fails. The documented chain is Flash first, then two Flash-Lite
  models, which is the quality trade-off described in [Why this model](#why-this-model).
- When every model is out of quota the customer gets a clear message and the store's WhatsApp number instead
  of a stack trace.

**Groq's free tier is limited differently: 1,000 requests per day but only 8,000 tokens per minute per model.**
Each call to the agent sends about 4,800 tokens (the rules, the whole manual and the tool schemas), so the
budget allows about one and a half calls per minute. A question that needs a tool makes two calls and waits
for the token window, so it takes **30 to 60 seconds** on Groq. The retry above covers that wait. Each model
has its own budget, which is why the same reserve-model chain also helps there.

Practical consequence for anyone running the project: expect to spend the free quota quickly. `make eval`
will pause between scenarios and still needs a day's budget on several models. A paid tier or another
provider removes the limit; only `.env` changes.

### Prompt strategy

- **Persona:** the store's virtual assistant. Informal but professional, warm, knows music, short
  WhatsApp-style replies.
- **Current date and time** (America/Campo_Grande) is injected, together with the store's opening status
  computed in code (`hours.py`), so "are you open now?" and the reopening time never depend on the model
  doing date math.
- **Hard rules:** never state price, stock, promotion or delivery date without calling a tool; show a
  promotional price together with the original price and the discount; do not reveal order data before
  identity verification; if unsure about a policy, say so and offer a human instead of improvising.
- **Learned from real conversations** (each rule below fixed a failure seen with the real model): never
  promise what the manual does not offer (a Flash-Lite model offered to "reserve" a guitar); copy installment
  plans exactly as the tool wrote them (one model wrote "aproximado"); call `escalate_to_human` before saying
  a complaint was registered (one model promised it without calling the tool); do not repeat that the store
  is closed when the customer did not ask.
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

- Memory is in-process by default, so a conversation does not survive a restart. The history is trimmed
  to the latest 40 messages per conversation.
- The free tier allows about 20 requests per day per model (see [Free-tier quota](#free-tier-quota)). The
  agent tries the exhausted main model first on every turn before falling to a reserve; a circuit breaker
  that remembers dead models would save that wasted call.
- On the Groq free tier a question with a tool takes 30 to 60 seconds, because the prompt (about 4,800
  tokens) nearly fills the 8,000 tokens-per-minute budget. Shrinking the prompt would speed it up.
- The `gpt-oss` models write `R$\u202f2.199,00`, with a narrow no-break space after "R$". It looks the same to
  a customer, but a plain string comparison fails, so the grounding check (slice 7) must normalize spaces.
- Reserve models are Flash-Lite, which in the routing check were more likely to skip an escalation, so quality
  drops when the main model is out of quota.
- The free Gemini tier answers with HTTP 503 ("high demand") now and then, on any model. The agent needs a
  retry with backoff (slice 6); even so, a busy hour can make a reply slow.
- The full policy sits in every prompt. This is cheap at 8 pages but would not scale to a large manual.
- Product search is filter-based plus fuzzy name matching, not semantic. Vague requests ("something warm
  for jazz") depend on the model translating them into filters.
- The grounding check validates `R$` values and tracking codes, not every factual claim.
- Order identity check is e-mail or phone only, which is enough for a prototype and not for production.
- Product names are matched fuzzily but strictly: an ambiguous name returns candidates and an unknown name
  is reported as not found, so a badly misspelled name may need one more question to the customer.
- Opening hours know only the national holidays with a fixed date. Movable holidays (Carnival, Good Friday,
  Corpus Christi) and state or city holidays are not in the calendar, so for those the agent states the rule
  and says the team confirms.
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
