# Data assumptions and cleaning decisions

Every rule below is applied in code by `src/emporio_agent/data_loader.py` and covered by
`tests/test_data_loader.py`. The original files in `data/` are never modified (only their names were
normalized). This list feeds the README.

## Audit result

Checked on the original files: no nulls except the order columns below, no leading/trailing or double
spaces, no encoding damage, positive prices, non-negative integer stock, valid dates, unique ids, no
orphan rows (order items, orders, promotions) and no orders without items. Product specs are valid JSON
with uniform keys per category.

## Cleaning decisions

| # | Issue found | Decision | Effect on the agent |
|---|---|---|---|
| 1 | Descriptions of products **135, 136, 137, 139, 140, 142 and 144** contradict their name or specs. Checked one by one: **135** Fender Jazz Bass, body Alder (name Music Man, specs Ash); **136** "Precision" (a Fender model, name Ibanez); **137** 5-string Ibanez (name Yamaha, specs 4 strings); **139** Pearl Export, Poplar (name Yamaha, shells Maple); **140** Tama, Birch (name Pearl, shells Maple); **142** Roland, 88 keys (name Korg, specs 61); **144** Yamaha arranger (name Roland, type Synthesizer). **138** (Music Man StingRay), **141** (Yamaha Stage Custom) and **143** (Nord Stage) match their brand and specs, as do 131-134 and 145. 143 is the borderline case ("piano de palco" versus type Synthesizer): kept, since the brand matches and no number disagrees. | `description_reliable = False` and `description = NULL` for exactly these seven ids. Name, price, stock, status and specs are trusted. | The agent never quotes a description for these seven products. |
| 2 | Product rows are not sorted by id (145 comes before 131). | Sorted by `product_id`. | None. |
| 3 | Product 84 is named "Giannini GN-15 Nylon **Cedr** Natural"; its own specs say `top: Cedro`. | Fixed to "Cedro" by an explicit correction map. The original is kept in `name_raw`. The "Cedar" in products 126 and 130 is valid English and stays. | Answers use the corrected name. |
| 4 | Names contain accents ("Aço", "Elétrico"). | `name_search`: lowercase, accent-free copy of the name. | "violao aco" finds "Violão Aço". |
| 5 | `status` and `stock_quantity` disagree: product 96 is `active` with stock 0, product 136 is `discontinued` with stock 13, product 130 is `coming_soon` with stock 2. | `is_sellable = status == "active" and stock_quantity > 0` (61 of 65 products). | Stock alone never makes a product offerable. |
| 6 | Categories 6, 7 and 8 (woodwinds, brass, orchestral strings) have no products. | `categories.product_count` (0 for these). | The agent says the store has none right now and never invents items. |
| 7 | `specs` is a JSON string of text values, and `strings` means a count for guitars and basses but a material for acoustic guitars and ukuleles. | Parsed and typed: `string_count` (int), `string_type` (`nylon` or `steel`), `has_electronics` (bool), numeric strings become ints. | Reliable filters by string type and electronics. |
| 8 | 25 promotion rows, only 4 active (products 90: 18%, 94: 8%, 121: 20%, 127: 10%). | `promotions.is_active` as boolean and `products.active_promo_percent` from active rows only. | Expired promotions are never offered. |
| 9 | Stored order totals disagree with the sum of their items for orders **3** (3,450 vs 3,498) and **20** (1,400 vs 1,488). | `total_brl` stays the source of truth. `items_total_brl` and `total_mismatch` are derived. | The agent reports the stored total and does not recompute it. |
| 10 | Order totals show no PIX or promotional discount, and items carry no unit price. | No correction. Historical prices are never recomputed. | The agent reports order totals as stored. |
| 11 | Shipped and delivered orders (8) have tracking code and estimated delivery. The other 12 have both empty. Notes exist only on the 4 cancelled orders. | Empty values become `NULL`. A tracking code not matching `BR` + 9 alphanumerics + `BR` (policy 5.3) is dropped. | The agent never invents dates or codes. |
| 12 | `status` and `payment_method` are English or technical values. | PT-BR labels: `status_pt` (Entregue, Enviado, Confirmado, Pendente, Cancelado) and `payment_pt` (PIX, Cartão de débito, Boleto, Cartão de crédito em Nx). An unknown value stops the load. | Customer-ready wording. |
| 13 | Orders 3, 4 and 14 contain products that are not sellable today (130 coming soon, 96 out of stock). Order 3 was delivered in 2025-11 although product 130 was created as `coming_soon`. | `orders.has_unsellable_item`. Reported as stored. | The agent does not comment on current availability of items inside an order. |
| 14 | Orders 9, 16 and 18 use installments below the policy minimum (6x of R$58.32, 6x of R$71.65, 12x of R$38.32). | `orders.installment_rule_violation`. Kept as stored. | The installment rules apply to new purchases, not to past orders. |
| 15 | Customers 11 and 43 share the e-mail `bruno.martins@jmail.com`. Order 10 belongs to customer 11. | `customers.email_is_shared`. E-mail is normalized (lowercase), phone is reduced to its digits. | **Identity check (enforced and tested in the tools slice):** the e-mail or phone is checked against the customer **of that order**. If the e-mail given belongs to more than one customer, the agent requires the phone before releasing any data. Customers are never identified by name. |
| 16 | Dangling references or unknown status or payment values. | `DataValidationError` stops the load. | Bad data fails loudly instead of producing wrong answers. |

## Assumptions about the business data

- The data is a snapshot: the latest order is from 2026-03-22, so "pending" orders are months old. The
  agent reports status and dates as stored and does not compute delays against today's date.
- `orders` has no sales channel, no real delivery date and no shipping fee. The 7-day return window
  (policy 4.1, online purchases) is estimated from `estimated_delivery`.
- Installments up to 3x: the manual says "no minimum, except below R$ 50". This is read as a minimum of
  R$ 50 per installment.
- **1x on a credit card is a payment in full**: no minimum installment applies (1x has no minimum). Up
  to 3x the minimum is R$ 50 per installment (our reading of the ambiguous rule), 4x-6x R$ 80 and 7x-12x
  R$ 100.
- **Installment cents** go to the first installment, as the real store does: R$ 1.000 in 3x is 1x R$ 333,34 +
  2x R$ 333,33. Installments always add up to the total exactly (tested over thousands of amounts), and the
  tool states each value so the grounding check matches what the customer sees. Money is `Decimal`,
  rounded half-up to the cent.
- **Payment options are per product, with a quantity** (`calculate_payment_options(product_name,
  quantity=1)`): PIX eligibility depends on that product's promotion, and the combined-payment rule
  (above R$ 2.000) uses the total with the quantity. Totals are exactly unit price x quantity.
  **Carts with several different products are out of scope**: the agent calculates item by item and says the
  cart is closed by the team (listed under known limitations and "with more time").
- The store sells instruments only. Accessories are politely redirected (policy 1 and 7.1).
- The store operates in America/Campo_Grande (UTC-4); the current date and time injected in the prompt
  use this time zone.
- The free Google AI Studio tier may use submitted content to improve Google products. All data here is
  fictional, so nothing real is sent (LGPD care); a real deployment would use a paid tier.

## Tool decisions (slice 4)

- **Only sellable products are ever searched or offered**; a product with positive stock that is
  discontinued or coming soon is described honestly by `get_product_details` but never listed.
- **Price filters compare the final price** (promotion included), so a R$ 549 ukulele at 20% off fits a
  R$ 500 budget.
- **Name lookup is fuzzy but strict.** Accents, case, punctuation and word order are ignored ("ohana ck 20",
  "CK20" and "Ohana CK-20" are the same product). A name that fits several products ("Yamaha") returns
  candidates so the agent asks which one; a name that fits none is reported as not found. Nothing is guessed.
- **Alternatives for an unavailable product** come from the same category, ranked by same string material,
  then same electronics, then nearest final price (three at most).
- **Order identity.** The e-mail or phone is checked against the customer **of that order**. Phones lose
  formatting and a leading country code; e-mails are lowercased. A shared e-mail alone never releases an
  order: the phone is required. Every other failure returns one generic answer, identical for an unknown
  order and for wrong details.
- **Escalations** are appended to `escalations.jsonl` (one JSON line each, store time zone) and the
  customer is told the 24 business-hour response window of policy 7.3. The summary the agent writes is
  stored as given, so it should avoid unnecessary personal data.

## Policy text and prompt decisions (slice 5)

- **Extraction.** `pypdf`'s default mode returns one word per line (1,755 lines of 1.0 word), which
  destroys headings, lists and tables. The `layout` mode (201 lines, 8.7 words per line) keeps them. The
  text is then cleaned in code, **without losing any rule, value or deadline**:
  - page footers ("Página N"), the header repeated on every page and the cover line "Documento interno —
    Uso restrito" are dropped;
  - tables become simple lines: two-column tables read `Segunda a Sexta-feira: 09:00 às 18:00`, wider
    ones keep their column names (`PAC (Correios): Prazo Estimado 5 a 12 dias úteis; Rastreamento Sim;
    Seguro Incluído`); the generic header rows ("Dia | Horário", "Campo | Informação") are dropped;
  - bullets become `- ` lines, numbered steps and subsection titles stay on their own line;
  - lines that the layout wrapped in the middle of a sentence are joined, and blank lines are kept only
    before each of the ten section titles (70 blank lines became 10; 242 lines became 136).
- **How losslessness is checked.** A test compares the cleaned text with the PDF as `pypdf` extracts it
  by default: every figure (hours, days, prices, percentages) must still appear as many times as in the
  PDF, and every distinct word must still appear, except an explicit list of allowed drops (the footer word
  "Página", the "uso restrito" cover line and the two generic first-column table headers "Informação" and
  "Modalidade"). Another test pins the critical facts one by one: hours, phones, the 7/30/90-day
  deadlines, installment rules, shipping (R$ 500 / R$ 35), PIX 5% and the non-exchangeable items.
- **What the cleanup is worth, measured.** The manual went from 3,012 to **2,942 tokens (-70, -2.3%)** on
  Gemini's tokenizer, and the whole system prompt from 4,280 to 4,210. On Groq's tokenizer the system
  prompt went from 4,046 to 3,978 tokens (-1.7%). It is a cleaner text, not a much shorter one:
  the manual is made of real sentences, and the blank lines and line breaks that were removed were cheap.
- **Typo fixed in the text:** the e-mail written with an accent (`contato@empóriodamusica.com.br`).
- **Phone conflict** is settled in the prompt rules (landline for calls, WhatsApp number for WhatsApp), and
  the prompt says its rules prevail over the manual on conflicts, so the manual text itself is left as is.
- **Opening hours are computed in code** (`hours.py`), not by the model: policy 7.1 requires giving the
  return time when closed, and "are you open now?" must not depend on the model doing date math. The
  prompt receives the current weekday, date, time and status.
- **Holidays.** The manual says the store is closed on holidays. Only **national holidays with a fixed
  date** are modeled (1 Jan, 21 Apr, 1 May, 7 Sep, 12 Oct, 2 Nov, 15 Nov, 20 Nov, 25 Dec). Movable ones
  (Carnival, Good Friday, Corpus Christi) and state or city holidays are not, because they need a calendar.
  The prompt tells the agent to state the rule and say the team confirms the date for any other holiday.
- **Prompt layout.** Persona, current moment, rules, service flow, examples, then the manual last between
  delimiters. The examples contain no real prices, so the model cannot parrot them. The static parts come
  first and the manual last, which suits provider-side caching when it is available.
- **Size.** Per section, on Gemini's tokenizer: persona 46, current moment 52, rules 893, service flow 62,
  examples 188 and the manual **2,942**, for a **system prompt of 4,210 tokens** (about 0.4% of the
  1,048,576-token context window), plus **950 tokens** for the six tool schemas that travel in every call:
  **5,160 tokens per model call**. The manual is 70% of the prompt, so putting it whole in the prompt (CAG)
  fits with a wide margin but is what decides the speed on the Groq free tier (8,000 tokens per minute).
  The examples are only 188 tokens and were left as they are.
- **Why not shrink further.** Two model calls per minute on Groq would need under about 3,400 tokens per
  call, which is impossible without removing manual content or giving up the whole-manual-in-the-prompt
  decision. That trade-off is left open on purpose; it is discussed only if the slowness blocks the evals.

## Agent decisions (slice 6)

- **One prompt per model call.** The system prompt is rebuilt before every model call, so the date and the
  opening status stay true in a long conversation.
- **Memory** is per conversation (`thread_id`), kept in process (`InMemorySaver`). The history is trimmed to
  the latest 40 messages and always starts on a customer message, so a tool result is never separated from
  the request that produced it. The CLI uses a new thread per run and per `/novo`.
- **Retry.** Up to 4 retries with exponential backoff (4 s, 8 s, 16 s, 32 s, with jitter; about a minute, the span of a per-minute token window) for per-minute
  rate limits, HTTP 429/500/502/503/504 and network errors. Anything else (bad request, invalid key) fails at
  once. The Google client's own retries are switched off so retrying happens in one visible, tested place.
- **Daily quota** is detected from the error text ("requests per day") and is never retried. The Gemini free
  tier measured about 20 requests per day per model.
- **Reserve models** (`LLM_FALLBACK_MODELS`) take over when the main model fails for any reason, including a
  503; retry wraps the whole chain. They share the same prompt and rules.
- **When everything fails** the customer sees either an apology with the WhatsApp number (transient errors
  after the retries) or a daily-limit message with the WhatsApp number (quota). Other errors (for example an
  invalid key) are reported by type, with a hint to check `.env`, and the chat goes on.
- **Prompt rules learned from the real model:** no promises the manual does not make; installment plans are
  copied exactly as the tool wrote them; `escalate_to_human` is called before saying a complaint was
  registered, with the order number asked afterwards; the opening status is mentioned only when relevant.
- **Providers.** Gemini is the reference model and Groq the development model; `LLM_MODEL` alone selects
  one. The Groq errors carry the HTTP status in `status_code` (Google's in `code`), and the retry reads both:
  without that, a Groq 429 escaped the retry in the first real run.
- The `gpt-oss` models put a narrow no-break space (U+202F) after "R$". The grounding check must normalize
  U+202F and U+00A0 before comparing amounts.
- The store time zone is a business fact (`hours.py`), not an environment setting, so `.env.example` no
  longer lists `STORE_TIMEZONE`.

## Policy manual notes (used from the policy slice on)

- Text extraction with `pypdf` returns one word per line, so the text must be re-joined before it goes
  into the prompt.
- **Phone conflict.** Section 1.2 and the header give (67) 3341-4444, and section 1.2 labels it
  "Telefone / WhatsApp"; section 7 says the WhatsApp channel is (67) 3321-4500. **Rule:** (67) 3341-4444 is
  the store's landline (calls) and (67) 3321-4500 is the WhatsApp number, because section 7, the section
  about remote service, is the more specific source for that channel.
- The e-mail is written once with an accent (`contato@empóriodamusica.com.br`); the header version
  `contato@emporiodamusica.com.br` is used.
- The manual mentions "more than 300 instruments"; the dataset has 65 products.
