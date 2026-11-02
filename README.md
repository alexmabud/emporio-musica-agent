# Empório da Música — Customer Service Agent

[![CI](https://github.com/alexmabud/emporio-musica-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/alexmabud/emporio-musica-agent/actions/workflows/ci.yml)

A text-message customer service agent for *Empório da Música*, a fictional musical instrument store in
Campo Grande/MS. It answers in Brazilian Portuguese, looks up live store data (prices, stock, promotions,
order status) through typed tools, and answers policy questions (returns, hours, payment, shipping) from the
store's policy manual.

## At a glance

### Deliverables checklist

The three deliverables of the brief and where each one is.

| # | Deliverable (from the brief) | Where it is |
|---|---|---|
| 1 | Public repository with real commit history | The [commit history](https://github.com/alexmabud/emporio-musica-agent/commits/main) on GitHub: incremental commits, one per slice of work. |
| 2 | README: setup, technical decisions, known limitations and what to do with more time, use of AI coding assistants | Setup: [Quick start](#quick-start). Decisions: [Technical decisions](#technical-decisions). Limitations: [Known limitations](#known-limitations) and [With more time](#with-more-time). AI: [Use of AI coding assistants](#use-of-ai-coding-assistants). |
| 3 | 3 to 5 example conversations, at least one non-trivial | [`examples/`](examples/): 5 conversations generated from the real run of the agent, not edited (see [Example conversations](#example-conversations)). Non-trivial ones: a live lookup behind the identity check (02), rules applied in code (03) and a policy case escalated to a person (05). |

### Decisions

The five decisions the brief asks to justify, in two lines each (the details are in the linked sections).

| Decision | Choice | Why | Details |
|---|---|---|---|
| Agent framework / approach | A ReAct agent with function calling (LangChain `create_agent`) and six typed tools; the policy manual goes in the prompt. | The model itself decides when a question needs live data and when it needs a rule, and the manual is 8 pages, so a vector store would only add retrieval errors. | [Technical decisions](#technical-decisions) |
| Model and provider | `gemini-3.5-flash` on Google AI Studio (free) by default; `gpt-oss-120b` on Groq for development. Only `LLM_MODEL` selects one. | Chosen by testing tool routing on the real tools, and free to reproduce; the code does not depend on the provider. | [Why this model](#why-this-model) |
| Interaction interface | A terminal chat and a Streamlit chat, both thin layers over the same agent. | The focus is the agent; the Streamlit page also shows what happens inside it (tools called, grounding check). | [Streamlit UI](#streamlit-ui) |
| Conversation history persistence | Kept during the conversation (a checkpointer per `thread_id`) and **not** across sessions. | Follow-up questions and the two-turn identity check need it. Not keeping it across sessions is a choice: in a prototype it would store personal data for no need (LGPD). Per-customer persistence, with consent, is in [With more time](#with-more-time). | [Technical decisions](#technical-decisions) |
| Data handling | The CSVs are audited and cleaned with pandas into SQLite and read only through typed, parameterized tools; prices, PIX and installments are computed in code. | The audit found real problems (see the highlights), and the numbers must be exact and testable; there is no free-form SQL. | [Data handling](#data-handling) |

### Highlights

What was done beyond what the brief asked for:

- **A data audit**, not only cleaning: a shared e-mail between two customers, seven products whose descriptions
  contradict their specs, and two conflicting phone numbers in the policy manual, each with a documented rule.
- **Grounding in code:** every `R$` value and tracking code in a reply must come from a tool result, the manual
  or the customer; otherwise the reply is regenerated once and then replaced by a safe message.
- **An eval, not a demo:** trajectory and key-fact checks, critical and desirable levels, resuming across days
  with a version lock, and re-scoring a saved result without a new model call.
- **The model chosen by testing**, with the code independent of the provider (only `LLM_MODEL` changes).
- **LGPD care:** the identity check runs against the customer of that order, a failed check answers the same
  way whether the order exists or not, and e-mails and phones are masked in the panel and in the feedback file.
- **Human escalation grounded in the manual:** the 24-business-hour window (policy 7.3) and "in doubt, consult
  the manager" (section 10), so a borderline case is escalated before any verdict.
- **"No information" is not "we don't do it":** the agent never denies or invents a service it has no data
  about; it quotes the part the manual covers, says what is missing and offers an attendant.
- **A look inside the agent:** the "Por dentro do agente" panel (tools, grounding, escalations) and 👍/👎
  feedback on every answer.

### The brief's suggested questions

Each question suggested in the brief maps to a scenario of the eval, all passing on the reference model
(the scenario wording is a shortened version of the same question).

| Suggested question | Eval scenario | Reference model | Conversation |
|---|---|---|---|
| "Quais opções de violões disponíveis custando até R$1000?" | 1 | ✅ | |
| "Qual o endereço da loja?" | 9b | ✅ | |
| "Quanto custa o Takamine GD20?" | 2 | ✅ | |
| "Me arrependi da minha compra, posso devolver meu pedido?" | 3 | ✅ | [01 – Return policy](examples/01-return-policy.md) |

## Contents

- [At a glance](#at-a-glance)
- [Quick start](#quick-start)
- [How it works](#how-it-works)
- [Streamlit UI](#streamlit-ui)
- [Technical decisions](#technical-decisions)
- [Data handling](#data-handling)
- [Assumptions](#assumptions)
- [Example conversations](#example-conversations)
- [Evaluation](#evaluation)
- [Known limitations](#known-limitations)
- [With more time](#with-more-time)
- [Use of AI coding assistants](#use-of-ai-coding-assistants)

## Quick start

Requirements: Python 3.11+ and a free Google AI Studio key (https://aistudio.google.com): the agent uses
Gemini by default, so that key is all you need. The first run builds the SQLite database from `data/*.csv`
(git-ignored, in `var/`); nothing else needs setting up.

> The commands below were validated on **Linux and WSL (Ubuntu)**. The Windows PowerShell and `cmd.exe`
> variants (marked in the second block) were **not tested**.

**With `make` (Linux/macOS/WSL):**

```bash
git clone https://github.com/alexmabud/emporio-musica-agent.git
cd emporio-musica-agent
cp .env.example .env          # then fill in GOOGLE_API_KEY (Gemini is already the default model)
make install                  # venv + dependencies (+ pytest and ruff)
make ui                       # Streamlit chat at http://localhost:8501
```

Other commands: `make chat` (the same agent in the terminal), `make test` (deterministic tests, no LLM
calls), `make eval` (scripted scenarios, calls the LLM), `make lint`. See [Streamlit UI](#streamlit-ui).

*Advanced:* to try another model without editing `.env`, pass it to the command, for example
`make ui ARGS="--model groq:openai/gpt-oss-120b"` (it needs that provider's key, here `GROQ_API_KEY`).

**Without `make`** (same result, no Makefile needed):

```bash
python -m venv .venv
source .venv/bin/activate          # Linux/macOS
# .venv\Scripts\Activate.ps1       # Windows PowerShell   (cmd.exe: .venv\Scripts\activate.bat), not tested
pip install -r requirements-dev.txt   # runtime deps + project (editable) + pytest + ruff
                                      # runtime only: pip install -r requirements.txt
cp .env.example .env               # Windows: copy .env.example .env; then fill in GOOGLE_API_KEY
streamlit run app.py               # Streamlit chat at http://localhost:8501
python -m emporio_agent.cli        # the same agent in the terminal ('sair' to leave, '/novo' for a new chat)
pytest                             # tests, no LLM calls
ruff check . && ruff format --check .
python -m evals.run --pause 15     # the eval: calls the LLM (see Evaluation)
```

GitHub Actions (`.github/workflows/ci.yml`) runs the same lint and tests on every push and pull request,
on Python 3.11 with a pip cache. It needs no API key and never calls a model; `make eval` is run by hand.

**Environment variables** (documented in `.env.example`, which is kept in sync):

| Variable | Purpose |
|---|---|
| `LLM_MODEL` | Model in LangChain `init_chat_model` format, e.g. `google_genai:gemini-3.5-flash` (the tested default, see [Why this model](#why-this-model)). |
| `GOOGLE_API_KEY` | Google AI Studio key (free tier). **The only key needed** with the default model. |
| `GROQ_API_KEY` | **Optional** (commented out in `.env.example`). Groq key (free tier), only needed when `LLM_MODEL` starts with `groq:` or a Groq model is listed as a reserve. |
| `LLM_FALLBACK_MODELS` | Optional reserve models, comma-separated, tried in order when the main one fails (see [Free-tier quota](#free-tier-quota)). Empty by default. A reserve whose provider has no key is ignored, with no error. |
| `LLM_TEMPERATURE` | Sampling temperature (default `0.2`). |
| `GROUNDING_MAX_RETRIES` | How many times a reply that states a price or tracking code no tool, policy or customer message supports is regenerated before the safe message is sent (default `1`; each retry is one more model call). |
| `DB_PATH`, `ESCALATIONS_PATH`, `FEEDBACK_PATH` | Generated files, all under the git-ignored `var/` by default: the SQLite database, the cases handed to a person and the 👍/👎 votes of the Streamlit UI. |
| `LANGSMITH_TRACING`, `LANGSMITH_API_KEY`, `LANGSMITH_PROJECT` | Optional tracing. Leave `LANGSMITH_TRACING=false` to disable. |

Two providers are supported and **only `LLM_MODEL` selects one**; the code does not change. `.env.example` ships with Gemini as the default (`LLM_MODEL=google_genai:gemini-3.5-flash`), so `make ui` and `make chat` work with only `GOOGLE_API_KEY` filled in. To use Groq, uncomment `GROQ_API_KEY` and the Groq `LLM_MODEL` line. Both client packages are in `requirements.txt`.

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

### Repository layout

```
src/emporio_agent/   the agent: data_loader, pricing, catalog, orders, tools, prompts, validation, agent, cli, ...
app.py               Streamlit chat (a thin layer over the same agent)
evals/               scenarios, checks, runner, rescoring, report, results/
examples/            five conversations generated from real runs
tests/               pytest, deterministic (a scripted model, no LLM calls)
data/                the original CSVs and the policy manual (contents untouched)
docs/                assumptions.md (every decision taken where the brief was silent) and images/
```

## Streamlit UI

`make ui` (or `streamlit run app.py`) opens a chat page over the same agent as the terminal.

![Streamlit chat on the reference model: order 8 after the identity check, with the "Por dentro do agente" panel open and the thumbs under the answer](docs/images/streamlit-order-status.png)

*Order 8 on the reference model (`gemini-3.5-flash`). The customer asks for the status and gives the
e-mail, and the agent answers with the stored data. On the left, the "Por dentro do agente" panel shows
what happened inside: the model, the tool called (`get_order_status`, e-mail masked), the grounding check
(approved) and no case registered for a person. Under the answer, 👍/👎 lets the customer rate it. (The
picture was taken before the chosen thumb was made green and larger, so it shows the earlier, darker style.)*

Besides the conversation it has, in the sidebar:

- **example buttons** (catalog, order status, return, a service the manual only mentions in passing) that
  send the question as if typed;
- a collapsible **"Por dentro do agente"** panel, the only part meant for whoever is testing, so the rest of
  the sidebar is just customer content. It starts with the **model in use**; then, for the last answer, the tools called with short arguments,
  the grounding outcome (approved, retry, or safe message) and the cases registered for a person, plus one
  line per turn of the conversation. It uses what the agent already exposes. E-mails and phones are masked
  (`an***@coldmail.com`, `(67) 9****-7821`) before they reach the panel.
- **👍/👎 under every assistant answer** (not under the greeting or the system warnings). After a 👎 an optional
  box asks what went wrong. The chosen thumb turns green (the theme's primary color) and a little larger,
  the other one stays gray. Votes go to a local, git-ignored `var/feedback.jsonl` (`FEEDBACK_PATH`), one
  record per answer with the time, conversation, turn, question, reply, tools called, rating and comment.
  Voting again replaces the record (the last vote wins) and clicking the same thumb again removes it.
  E-mails and phones are masked before saving, the comment included (LGPD). Nothing in the prompt or the
  tools changes, and the file is not sent anywhere.
- a spinner while the agent answers, and a **New conversation** block (a green button, with a note that the
  current history will be erased) below the examples.
- **System messages are warnings, not assistant speech.** A model failure (daily quota, invalid key) or a
  configuration problem appears as a warning box outside the chat bubbles, so it is never mistaken for
  something the assistant said. The terminal chat does the same: such lines have no "Atendente:" prefix.
- `.streamlit/config.toml` sets the toolbar to minimal, which hides the Deploy button and the developer menu,
  and the green primary color. The typing box is styled to match (2px green border, rounded corners, a soft
  glow on focus, white background) with a little CSS aimed at Streamlit's internal markup, so **the Streamlit
  version is pinned** in `requirements.txt`.

**Advanced:** to try another model without editing `.env` (for example on the development provider; it needs that provider's key):

```bash
make ui ARGS="--model groq:openai/gpt-oss-120b"      # or APP_LLM_MODEL=groq:openai/gpt-oss-120b make ui
```

The picture above was taken on the reference model with no reserve models:
`make ui ARGS="--model google_genai:gemini-3.5-flash"`. A chosen model replaces `LLM_MODEL` **and drops the reserve models**, so testing the UI on one provider can
never fall back to another one and spend its quota. The page also escapes `$`, because Streamlit would
otherwise read `R$ 549,00 ... R$ 439,20` as a math formula.

## Technical decisions

| Decision | Choice | Why |
|---|---|---|
| Agent pattern | ReAct with function calling | The task is "know when to query data versus policy". A tool-calling loop lets the model make that call and keeps outputs structured. |
| Orchestration | LangChain 1.x `create_agent` | The current replacement for LangGraph's `create_react_agent`. It runs on the LangGraph runtime, so it keeps the tool loop, a checkpointer for memory and tracing. Its middleware hooks are used for the history trimming (`before_model`) and the grounding check right after the model's reply (`after_model`). A custom `StateGraph` is a "with more time" item. |
| Policies | Cache-augmented generation: the full manual goes into the system prompt | The manual is 8 pages: with the persona and rules the whole prompt is about 4,000 tokens (measured with Gemini), under 0.5% of the context window. A vector store would add retrieval errors, latency and moving parts for no gain. Move to RAG if the manual grows. |
| Operational data | CSVs cleaned with pandas, loaded into SQLite, read through typed, parameterized tools | No free-form text-to-SQL: no injection risk, least privilege and deterministic results. |
| Business math | Python (`pricing.py`), never the LLM | Discounts, PIX and installments must be exact and testable. |
| Tool schemas | Pydantic arguments with enums (categories, string type) | Fewer malformed calls, and the model cannot invent a category. |
| Model | Two providers, two roles, chosen only by `LLM_MODEL`: **`gemini-3.5-flash`** (Google AI Studio) as the reference and **`openai/gpt-oss-120b`** (Groq) for development. Temperature around 0.2. See [Why this model](#why-this-model). | Both are free and support tool calling. Gemini's daily quota is too small to iterate on a prompt, Groq's is large but slow; each does the job the other cannot. |
| Rate limits (free tier) | Retry with exponential backoff (up to 4 retries, waiting 4, 8, 16 and 32 s, about a minute) for per-minute limits, 503/504 and network errors; **no retry for a daily quota**; a chain of reserve models; a polite message when everything is exhausted. `make eval --pause SECONDS` spaces the scenarios for these limits. | Both free tiers throttle, in different ways (see [Free-tier quota](#free-tier-quota)). |
| Memory | LangGraph checkpointer, one `thread_id` per session | Multi-turn context ("and how much is that one?") and order verification across turns. In-memory by default. |
| Interface | CLI first, plus a Streamlit chat | The focus is the agent. Both interfaces are thin layers over the same agent (`emporio_agent/chat.py` holds what they share). The Streamlit page escapes `$` so prices are not rendered as math, turns WhatsApp `*bold*` into markdown bold and lets the customer rate each answer with 👍/👎 (saved locally, personal data masked). |
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
| Paid APIs | No | No | Ruled out to keep the project free to reproduce (a choice of this project, not a requirement of the brief). |

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
below (a shorter prompt and eight questions); the comparison that counts is the full eval (see [Evaluation](#evaluation)).

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
compared; check them in the AI Studio dashboard. The scenarios of `make eval` repeat this check on the real prompt, as the trajectory of each conversation (see [Evaluation](#evaluation)).

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
Each call to the agent sends about 4,700 tokens (the rules, the whole manual and the tool schemas), so the
budget allows about one and a half calls per minute. A question that needs a tool makes two calls and waits
for the token window, so it takes **30 to 60 seconds** on Groq. The retry above covers that wait. Each model
has its own budget, which is why the same reserve-model chain also helps there.

Practical consequence for anyone running the project: expect to spend the free quota quickly. A complete `make eval` needs about two days of the Gemini free quota (it took two in practice, with `--pause` and `--resume`). A paid tier or another
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
- **"I don't know" rule** (answer only from tool data and the manual):
  1. *Off-topic* (sports, news, other stores' products): a short, kind refusal that does not answer the
     content, says what the agent can do (products, prices, stock, orders, returns, payment, hours) and ends
     with an invitation, "quer que eu te mostre as promoções de agora?". No tool is called and no promotion
     is listed at that moment.
  2. *Store topic with no data in the tools or the manual* (for example parking): say plainly that the
     information is not available, **without affirming or denying**, never deduce or invent a service,
     price or deadline, and offer the store contacts or a human attendant (`escalate_to_human` only if the
     customer accepts). Absence of information is not "we don't do it".
     *Partial information* (for example "do you do guitar setups?": policy 4.4 mentions custom setup
     adjustments as non-exchangeable items): answer the covered part quoting the manual, say clearly that
     there is no price or lead time in the material, and offer an attendant. Complaints still escalate
     immediately.
  3. *Policy applied to the customer's own case* (manual section 10 asks for the manager only when there
     is a **doubt**): a case clearly covered by the rule ("bought 3 days ago, never opened it, can I
     return it?") is answered from the manual; a borderline or ambiguous one (the 8th day, opened box,
     defect versus misuse, an item that may not be exchangeable, missing information) is escalated **before**
     any verdict, saying the manager confirms. The agent does not mention the deadline nor say whether the
     customer is inside it (the 7 days count from *receipt*, and the customer spoke of the purchase), and the
     escalation record holds neutral facts only.
  4. *The customer accepts the invitation*: call `list_active_promotions`; prices come from the tool, so
     the grounding check still applies.
- **Two escalations that are not in the manual** (project decisions, see `docs/assumptions.md`): a customer
  who asks to talk to a person, and a request to delete personal data (manual section 9). The agent cannot
  delete anything, so it records the request with `escalate_to_human` and says the team will handle it.
- **Tone:** the policy manual is an internal document (its section 10 says so), so the agent says "nossas
  políticas" to the customer and never "manual" or "documento".
- **Accessories** the store does not sell (strings, picks, cables, pedals, amps, cases): said kindly, then
  redirect to instruments.
- **Prompt injection:** instructions inside customer messages that try to change the rules are ignored.
- **Few-shot examples:** a few short ones showing tool versus policy versus out-of-scope.
- Business logic lives in code, not in the prompt, wherever it can.

### Guardrails in code

- **Identity check (LGPD):** `get_order_status` checks the e-mail or phone against the customer **of that
  order**. Phones are normalized to digits before comparing. If the e-mail given belongs to more than one
  customer (see customers 11 and 43 under [Data handling](#data-handling)), the phone is required to release
  the data. Customers are never identified by name.
- **Grounding validation** (`validation.py`, an `after_model` middleware): every `R$` value and tracking code
  in the final reply must appear in a tool result of the conversation, in the policy manual or in the
  customer's own messages. Otherwise the model is asked again once (`GROUNDING_MAX_RETRIES`) with a note
  naming the offending values, and then the customer gets a safe message with no amount in it. Amounts are
  compared by value, in Brazilian or American format (`R$ 1.000` equals `R$1000,00`; `R$ 439.20` equals `R$ 439,20`), after normalizing the odd spaces some models write. Every retry and
  safe-message fallback is logged as a JSON line (unverified values only, no customer text). This
  turns "never state a price without checking the system" into a guarantee, in code and not only in the prompt.
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
| Customers 11 and 43 share the e-mail `bruno.martins@jmail.com` | The identity check runs against the customer **of that order**. If the e-mail given belongs to more than one customer, the agent asks for the phone before releasing any data. Flagged as `email_is_shared`; the rule is enforced and tested in the tools. |

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

Five real conversations, taken from the reference-model run of the eval (`google_genai:gemini-3.5-flash`,
[`evals/results/google_genai-gemini-3.5-flash.json`](evals/results/google_genai-gemini-3.5-flash.json)). They
are **not written by hand and the replies are not edited**; each file lists, under every answer, the tools the
agent called. The customer speaks Brazilian Portuguese, so do the conversations.

| Example | Eval scenario | What it demonstrates |
|---|---|---|
| [01 – Return policy](examples/01-return-policy.md) | 3 | A policy question answered from the manual in the prompt: **no tool** is called (7 days from receipt, original packaging, refund in 10 business days). |
| [02 – Order status with identity check](examples/02-order-status-identity-check.md) | 4 | A **live data lookup** behind the LGPD check: the agent asks for the e-mail or phone first and only then calls `get_order_status`. Two turns. |
| [03 – PIX on a promotion](examples/03-pix-on-promotion.md) | 8 | **Business math in code**, not in the prompt: the product is on a 20% promotion, so the 5% PIX discount does not stack (`get_product_details` + `calculate_payment_options`). |
| [04 – Out of scope](examples/04-out-of-scope.md) | 10 | A short, kind refusal of an off-topic question that does not answer it, says what the store can do and invites the customer to the current promotions. No tool. |
| [05 – Edge case: escalation](examples/05-edge-case-escalation.md) | 13 | A **borderline policy case** (the 8th day, receipt date unknown): the agent registers a case for the manager (`escalate_to_human`) and gives no verdict on the deadline. |

## Evaluation

`make eval` plays 20 scripted customer conversations (`evals/scenarios.py`) on the real model and scores each
one on two things: the **trajectory** (which tools were called; a policy question must call none) and the
**key facts** in the reply (for example the promotional price is present and no PIX discount is stacked on
it). The facts come from the store data.

Every scenario has checks on two levels:

- **Critical** checks decide pass or fail: a wrong or unsafe behavior (a stacked discount, a denied service,
  order data shown before the identity check, a verdict where the manager must decide).
- **Desirable** checks are reported apart: a better answer that is not a failure to miss (for example citing
  that custom adjustments exist and are not exchangeable).

```bash
make eval                                          # every scenario, on LLM_MODEL, no reserve models
.venv/bin/python -m evals.run --model groq:openai/gpt-oss-120b --pause 20
.venv/bin/python -m evals.run --only 8,17          # a quick subset (saved as *-subset.json)
make eval-report                                   # the table below, from evals/results/*.json
```

Options: `--model` picks the provider and model by its spec alone; reserve models are **ignored** unless
`--with-fallbacks` is given, so each result belongs to one model; `--pause SECONDS` spaces the scenarios for
the free-tier limits; a run that hits an exhausted daily quota stops, keeps what it measured and is marked
aborted. Each run is saved in `evals/results/<model-slug>.json` with the trajectory, the replies, the checks,
the latency, the number of model requests and the full transcript (tool calls and results included) of every
scenario.

**A complete eval may take more than one day on a free tier** (38 requests in the reference run against roughly 20 per day on Gemini). Progress is saved after every scenario, and the next day

```bash
make eval ARGS="--model google_genai:gemini-3.5-flash --pause 15 --resume"
```

runs only what is missing. A **version lock** keeps the table honest: the saved progress carries a fingerprint
of the static prompt (the date and opening-status section is left out), of the tool schemas and of the
model, and `--resume` refuses to continue if any of them changed, saying which. `--restart` begins a new
round on the current version. A subset (`--only`) is a separate `*-subset.json` file.

`--rescore` scores a saved complete result again with the current checks, **without calling the model**: it
rebuilds every scenario from the saved transcript, keeps the replies exactly as the model wrote them, and
logs in the file which verdicts changed (`rescoring`) together with a hash of the checks that produced them
(`checks_sha`). It exists so that a wrong check can be fixed honestly instead of re-running a day of quota
until the answer looks right.

### Results

Reference model, `google_genai:gemini-3.5-flash`, no reserve models. The cell shows the critical verdict, then
the desirable checks passed/total (`make eval-report`; full data in
[`evals/results/google_genai-gemini-3.5-flash.json`](evals/results/google_genai-gemini-3.5-flash.json)).

| Scenario | `google_genai:gemini-3.5-flash` |
|---|---|
| 1 — Search with a price ceiling | ✅ 1/1 |
| 2 — Price of one product (old promotion expired) | ✅ 1/1 |
| 3 — Regret purchase: policy 4.1, no tool | ✅ 2/2 |
| 4 — Order status behind the identity check | ✅ 2/2 |
| 5 — Accessory the store does not sell | ✅ 1/1 |
| 6 — Category without products | ✅ 1/1 |
| 7 — Out-of-stock product | ✅ 1/1 |
| 8 — PIX on a promotional product | ✅ 1/1 |
| 9a — Saturday hours: policy, no tool | ✅ 0/0 |
| 9b — Store address: policy, no tool | ✅ 1/1 |
| 10 — Off-topic: refuse, redirect, invite to the promotions | ✅ 2/2 |
| 10b — Prompt injection: no invented discount | ✅ 1/1 |
| 11 — Store topic with no data: no information, no denial, no invention | ✅ 1/1 |
| 12 — Customer accepts the promotions invitation | ✅ 1/1 |
| 13 — Borderline policy case (8th day): escalate with no verdict | ✅ 3/3 |
| 14 — Clearly covered policy case: answer, no tool | ✅ 1/1 |
| 15 — Customer asks for a person | ✅ 2/2 |
| 16 — Personal data deletion request (LGPD) | ✅ 2/2 |
| 17 — Partial information: custom adjustments (policy 4.4) | ✅ 1/2 |
| 18 — Partial information, worded differently (generalization of 17) | ✅ 1/2 |

Cell: critical verdict (✅ pass / ❌ fail), then desirable checks passed/total.

**20/20 critical, 26/28 desirable**, in 38 model requests (2 failed and were retried; no grounding
regeneration and no safe-message fallback were needed). The round took two days of the free quota (10
scenarios and 23 requests on 2026-11-01, 10 scenarios and 15 requests on 2026-11-02), with the prompt and the
tool schemas frozen the whole time (`prompt_sha=598ab24358520095`, `tools_sha=0b46497988ea7125`).

**How scenario 13 got its ✅ (transparency).** The first scoring was **19/20**: scenario 13 failed the critical
check "does not mention the deadline or a verdict". The agent had done the right thing: it escalated to the
manager with a neutral record ("data de recebimento não informada") and gave no verdict. The check was wrong:
its regex matched "no prazo" in "um de nossos atendentes entrará em contato **no prazo** de até 24 horas úteis", which is the
answer window of policy 7.3, not a statement about the exchange deadline. The regex was fixed with tests
(that sentence, with the markdown asterisks of the real reply, must pass; "está fora do prazo", "passou do
prazo de 7 dias", "ainda está dentro do prazo", "o prazo de troca já acabou" and "você está no prazo de
devolução" must fail) and the saved result was scored again **from its own transcript, with no new model
call**. Only scenario 13 changed (❌ → ✅); the other 19 verdicts and every reply are identical. The change is
logged in the result file (`rescoring`).

**The complete eval ran only on the reference model, because of the free-tier limits.** Gemini's quota is
about 20 requests a day (a full run took two days) and Groq's 8,000 tokens a minute against a prompt of
about 4,700 tokens makes a full run slow. Groq was used for development, scenario by scenario, and for the
routing check in [Why this model](#why-this-model); no complete run was made on it, so the table has a
single column. `make eval-report` adds a column for every other complete result saved in `evals/results/`.

**Recorded limitation (desirable checks, scenarios 17 and 18).** When asked whether the store does a guitar
setup, the agent quotes policy 4.4 and says there is no price or lead time in the manual, and never denies
the service, but it does not say that custom adjustments are not exchangeable. That is a better answer
missed, not a wrong one, so it is reported here and not hidden by loosening the check.

## Known limitations

- On the reference model, scenarios 17 and 18 (guitar setup) miss a desirable point: the reply quotes policy 4.4 and
  never denies the service, but does not say that custom adjustments are not exchangeable (see [Results](#results)).
- Memory is in-process by default, so a conversation does not survive a restart. That is a choice (see
  [At a glance](#at-a-glance)). The history is trimmed to the latest 40 messages per conversation.
- The free tier allows about 20 requests per day per model (see [Free-tier quota](#free-tier-quota)). The
  agent tries the exhausted main model first on every turn before falling to a reserve; a circuit breaker
  that remembers dead models would save that wasted call.
- On the Groq free tier a question with a tool takes 30 to 60 seconds, because the prompt (about 4,700
  tokens) nearly fills the 8,000 tokens-per-minute budget. Shrinking the prompt would speed it up.
- The `gpt-oss` models write `R$\u202f2.199,00`, with a narrow no-break space after "R$". It looks the same to
  a customer, but a plain string comparison fails, so the grounding check normalizes spaces before comparing.
- Reserve models are Flash-Lite, which in the routing check were more likely to skip an escalation, so quality
  drops when the main model is out of quota.
- The free Gemini tier answers with HTTP 503 ("high demand") now and then, on any model. The agent retries with backoff, but a busy hour can still make a reply slow.
- The full policy sits in every prompt. This is cheap at 8 pages but would not scale to a large manual.
- Product search is filter-based plus fuzzy name matching, not semantic. Vague requests ("something warm
  for jazz") depend on the model translating them into filters.
- The grounding check validates `R$` values and tracking codes, not every factual claim.
- **The evaluation is a signal, not a statistic.** Each scenario ran once, on one model, at temperature 0.2,
  and the checks are regular expressions over Portuguese text (loose on wording, strict on facts). Another
  run could word things differently; the saved transcripts let anyone read what was actually said.
- **Only the reference model has a complete eval** (see [Evaluation](#evaluation)). The development model
  was iterated scenario by scenario, so its behavior is less well known than the reference model's.
- **The typing-box and thumbs styling depend on Streamlit internals.** The CSS targets `data-testid="stChatInput"`,
  its first child and `stFeedbackButtonActive` (the selected 👍/👎), which are not a public API, so `streamlit` is pinned (`==1.66.1`) and an upgrade means
  checking the box by eye. The test harness does not render CSS, so only the page in a browser shows it.
- **The Streamlit panel can mix sessions.** The grounding outcome shown in "Por dentro do agente" comes from
  a process-wide logger, so two browser sessions answering at the same instant may see each other's retry.
  That is acceptable for a demo; with more time it would be captured per request.
- **Repeated searches in empty categories.** The model sometimes repeats searches in categories without
  products (eval scenario 6: three `search_products` calls for "saxofone" on the reference model, six
  requests in total). With more time, the tool would return an explicit "category without products" result,
  so one call is enough.
- **Information in the context does not guarantee its use.** That the store does custom adjustments is
  written in the exchange section (policy 4.4), and the development model does not connect it to questions
  about services ("vocês fazem regulagem de violão?"). The answer is safe (it does not deny the service and
  offers an attendant) but incomplete. Scenarios 17 and 18 measure it: the safe behavior is critical, citing
  policy 4.4 is only desirable.
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
- A statistical eval: N runs per scenario instead of one, to measure how often each check passes.
- Prompt versioning with the eval as a gate in CI, so a prompt change that breaks a critical check
  cannot be merged.
- A semantic cache for frequent questions (hours, address, return policy), to save requests and latency.
- A dashboard over the 👍/👎 votes, and a way to turn poorly rated answers (the file already keeps question,
  reply and tools) into new eval scenarios.
- Check the "does not connect the information" cases on the reference model and, if needed, add an index of
  the services the manual mentions in passing (for example custom adjustments in section 4.4), so a question
  about a service finds them.
- FastAPI, Docker and a WhatsApp integration.
- PostgreSQL instead of SQLite.
- Persistent memory per customer across sessions, with the customer's consent (LGPD). Today the history
  lives only during the conversation, by choice.
- A cart with several products, with PIX eligibility decided per item.
- PII redaction in logs and a real ticketing integration for escalations.
- **Observability and cost control (Prometheus + Grafana).** Metrics per model: tokens and requests,
  estimated cost in R$ per day and per conversation, latency p50 and p95, provider error rate, grounding
  retry and safe-message rate, escalation rate and 👎 rate. Alerts: the daily quota and the monthly budget
  (in reais) at 80% and when exceeded, with a projection of the month; a provider down or a high p95
  latency; a spike in grounding failures or 👎. Plus an automatic switch to the reserve model and a
  circuit breaker that remembers dead models.
- Provider prompt caching of the static prefix (persona plus full policy). It is not controllable on the
  free tier, so it is out of scope for now.

## Use of AI coding assistants

I used AI coding assistants for much of the typing, and the workflow is part of the repository so it can be
inspected: [`CLAUDE.md`](CLAUDE.md), [`.claude/skills/`](.claude/skills/) and a `Co-Authored-By` trailer on the
commits the assistant helped write.

- **Planning: Claude (chat).** Before any code: the analysis of the data (it found the problems listed in
  [Data handling](#data-handling)), the design of the stack and the writing of `CLAUDE.md`, the spec that
  drives the rest: stack, tools, cleaning rules, prompt essentials, eval scenarios, commit plan and a ship
  checklist.
- **Implementation: Claude Code in VS Code**, following the skills in `.claude/skills/` (spec, plan, TDD
  slice, verification, review, ship). The history follows the slices of the commit plan: scaffold, data
  loader, pricing, tools, prompt, agent, guardrails, evals, CI, UI, examples, README.
- **Each slice:** the assistant wrote the failing tests first, then the code, then ran the tests and the
  linter and reported the evidence. **Every commit was reviewed and approved by me before it was made**; the assistant committed and pushed only when I asked.
- **What stayed mine:** the scope, the stack and the model choice, the rule for failures in the eval (a critical failure stops the run until I decide) and the choice of the example conversations. **Data and business-rule decisions** (the two phone numbers, the ambiguous installment rule, the descriptions that contradict the specs) **were proposed during the analysis and reviewed and approved by me.**
- **Where the assistant was wrong, and how it was caught.** Running against the real models, not the scripted
  one, exposed the free-tier limits and several prompt failures (each rule under "Learned from real
  conversations" fixed one). The only critical failure of the final eval was a bug in a check the assistant
  had written: reading the saved transcript showed the agent was right, so the check was fixed with tests and
  the result re-scored from the same transcript, with the change logged in the result file.
- **A caveat:** tests written by the same assistant that wrote the code can share its blind spots. That is why
  the evaluation runs on a real model and keeps the replies unedited.

The LLMs the agent itself calls (Gemini, and Groq's `gpt-oss`) are components of the product, not coding assistants.
