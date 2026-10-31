# LLM usage, cost and provider balances

The console's **LLM usage** page (`/usage`) answers three questions from local
data: what each model call consumed, what it cost, and how much balance the
providers report. This document covers what is recorded, how tokens and costs
are normalized, and the limits of each number.

- Console page: **LLM usage** (`/usage`)
- API prefix: `/api/v1/usage`
- Ledger: `data/usage/usage-YYYY-MM.jsonl` (gitignored)
- Price table: `usage.prices` in `apps/api/config.yaml`
- Provider balances: read from the vendors that expose an endpoint, using the
  server-side key named by each AI integration

## What is recorded

One JSON line per attempted model call. Both internal calls and gateway calls
go into the same ledger, so the page can report one total.

| Source | Capability | Provider |
|---|---|---|
| Internal | `assistant.chat` (replies, tool calls, memory distillation) | AI integration name, or `config:llm` for the YAML fallback |
| Internal | `meetings.translate` | AI integration name |
| Internal | `meetings.summarize` | AI integration name |
| Gateway | `gateway` | Gateway route name (the integration name) |

A record contains:

| Field | Meaning |
|---|---|
| `id`, `at` | Unique id and UTC timestamp |
| `capability`, `source` | Feature key and `internal`/`gateway` |
| `provider`, `model` | Route/integration name and the model the provider reported |
| `status`, `error` | `ok`/`error`; a failed call has no usage |
| `latency_ms` | Wall time of the call, including internal retries |
| `input_tokens` | Uncached prompt tokens |
| `cached_input_tokens` | Prompt tokens served from a provider cache |
| `cache_write_tokens` | Prompt tokens written into a cache (Anthropic) |
| `output_tokens` | Generated tokens |
| `reasoning_tokens` | A subset of output tokens, for display |
| `total_tokens` | Sum of the token classes above |
| `provider_cost_micros` | Cost the provider returned, if it does |
| `detail` | Protocol, stream flag, endpoint, tool-call count |

The ledger is append-only. A malformed line is skipped with a warning instead of
failing the page, and a failed append is logged and swallowed: telemetry must
never break a model call. Deleting old month files is safe and is the retention
policy for now; there is no automatic purge.

This is **not** the gateway's ask/answer log. Those files (`data/logs/<route>/`)
keep request and response bodies; the ledger keeps the numbers and no prompts.
An API key never appears in either.

## Token normalization

The two supported protocols disagree about what "input" means:

- OpenAI's `prompt_tokens` **includes** tokens served from the prompt cache.
- Anthropic's `input_tokens` **excludes** cache reads and cache writes.

The ledger normalizes both to *uncached* input so a single price row applies.
DeepSeek's `prompt_cache_hit_tokens` / `prompt_cache_miss_tokens` are understood
too, as is OpenRouter's `usage.cost`.

A field is `null`, not `0`, when the provider did not report it. In particular:

- A streamed OpenAI response carries usage only when the client asked for it
  (`stream_options: {"include_usage": true}`). Without that, the call appears in
  the page with no token counts and is counted as *no usage*.
- Anthropic always sends input usage in `message_start` and the final output
  count in `message_delta`; the gateway merges the cumulative chunks.
- A failed request (transport error, 4xx or 5xx) has no usage and its cost is
  unknown, not zero.

The page therefore separates three cases: **priced** (tokens plus a price),
**unpriced** (tokens reported, no matching price), and **no usage** (the
provider returned no counts).

## Price table and cost

Prices are configuration, not ledger data: an event stores what the provider
reported, so correcting a price also corrects the history. Costs are kept as
integer micro-units (one millionth of the currency) to avoid float drift.

```yaml
usage:
  currency: USD
  prices:
    deepseek-flash:
      input: 0.30
      output: 1.20
      cached_input: 0.006
    # "provider/model" wins over "model", and "*" is the last resort.
    openrouter/anthropic-claude:
      input: 3.00
      output: 15.00
```

Lookup order is `provider/model`, then `model`, then `*`, case-insensitively.
When a call reports cached input or cache-write tokens, the matching price must
exist; otherwise the call is **unpriced** instead of being charged the full
input rate. That is deliberate: charging cached tokens at the input rate would
overstate a cache-heavy bill by an order of magnitude.

No prices are shipped. Vendor prices change, and some vendors bill peak and
off-peak rates that a single row cannot express, so an unlisted model is shown
as unpriced rather than estimated at a made-up zero. The page prints the exact
YAML block to add.

Cost resolution, in order:

1. A provider-reported cost (`usage.cost`, for example on OpenRouter) wins.
2. Otherwise the local price table applies.
3. Otherwise the call is shown as unpriced with the reason.

Provider-reported costs are assumed to use `usage.currency`. The shipped default
is USD, which matches the vendors that return a cost.

## Provider balances

A balance is a property of the *account and key*, not of a model call. The
console queries it only for integrations whose supplier has a key in
`packages/winnie-llm/.env` (the same key the model calls use; balances are read
through `winnie-llm`'s key store, never a second copy). The adapter is chosen by
the integration's host, because the endpoint is a property of the vendor:

| Vendor | Host | Endpoint | Displayed |
|---|---|---|---|
| DeepSeek | `api.deepseek.com` | `GET /user/balance` | Currency, total, granted and topped-up balance |
| OpenRouter | `openrouter.ai` | `GET /api/v1/credits` | USD remaining (`total_credits - total_usage`), purchased, used |
| Kimi / Moonshot | `api.moonshot.ai`, `api.moonshot.cn` | `GET /v1/users/me/balance` | USD available, voucher and cash balance |
| SiliconFlow | `api.siliconflow.cn`, `api.siliconflow.com` | `GET /v1/user/info` | Total balance; CNY for `.cn`, USD for `.com` |

Any other host is shown as **not exposed** with a neutral explanation; the
provider either has no public balance API (OpenAI, Anthropic, Volcengine ASR) or
the integration points at a proxy. Adding a vendor is one adapter function plus
one host entry in `apps/api/src/winnie_api/usage/balances.py`.

Balances are cached for `usage.balance_ttl_seconds` (default 300). A page load
returns the cache when it is fresh; **Refresh balances** forces a query. Each
provider is queried in parallel with `usage.balance_timeout_seconds`, and one
provider's failure becomes an error on its row, not a failed page.

**Scope.** Balances cover the server-side keys used by internal jobs and the
assistant. A gateway route forwards the client's own vendor key, so proxied
traffic consumes the client's account and is not visible here. The ledger still
records those calls, because usage comes from the response, not from the key.

## HTTP API

All endpoints require the same bearer session as the rest of the console. They
answer `503` when `usage.enabled` is false, so a disabled deployment fails with
an explanation instead of a missing route.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/v1/usage/overview?days=7` | Totals, by-day, by-capability and by-model breakdowns, and the price table |
| `GET` | `/api/v1/usage/records?days=7&limit=25&offset=0` | Recent calls, newest first; filters `capability`, `provider`, `model`, `status` |
| `GET` | `/api/v1/usage/balances` | Cached balance snapshot per configured integration |
| `POST` | `/api/v1/usage/balances/refresh` | Force a fresh query |

`days` is between 1 and 365. The report is computed from the requested range at
read time, so a price change re-prices the same records without a rewrite.

## Configuration

```yaml
usage:
  # Off by default for a programmatic Settings(); config.yaml enables it.
  enabled: true
  # Relative to apps/api/, like every other root.
  root: ../../data/usage
  currency: USD
  prices: {}                       # see above
  balance_ttl_seconds: 300
  balance_timeout_seconds: 8
```

`usage.enabled: false` makes `/api/v1/usage/*` answer `503`; calls made while it
is off are not recorded and cannot be recovered. Restart the API after changing
the block; the price table is read on every request, but the environment and the
listener are not reloaded.

## Limits and non-goals

- **Speech-to-text is not in this ledger.** Meeting ASR writes its own
  `usage_records` rows (audio seconds, characters) in the meeting database. The
  LLM page covers model calls; a per-meeting ASR cost view remains a follow-up.
- **No sharding or database index.** Month files are scanned on read, which is
  fine for one person's call volume and is why the range is capped at a year.
- **No retention job.** Delete old `usage-YYYY-MM.jsonl` files by hand if the
  directory matters; nothing references them.
- **No currency conversion.** The price table has one currency, and a balance is
  shown in the currency the vendor reports. Mixing a CNY price table with a
  USD-reporting vendor would mislabel the cost, so keep the table in the unit
  your providers bill.
- **No per-user attribution.** Winnie is a single-user console; the ledger is
  instance-wide like the gateway logs.
- **Streamed cost is only as good as the usage chunk.** A streamed OpenAI call
  without `include_usage` cannot be priced, and a client that disconnects
  mid-stream is recorded with the partial counts that arrived.

## Verification

- `cd apps/api && .venv/bin/python -m pytest -q` covers token normalization for
  both protocols, price lookup and estimation, ledger month files, disabled
  recording, internal and gateway recording (JSON, OpenAI stream, Anthropic
  stream), the four balance adapters with a mock transport, unsupported hosts,
  missing keys, caching and forced refresh, and the HTTP endpoints.
- A disposable API run (`WINNIE_CONFIG=.tmp/usage-e2e/config.yaml`) forwarded
  real requests through the gateway to a synthetic OpenAI-compatible upstream.
  The console page then showed 18 calls, 131.71K tokens, `0.0410 USD`,
  16 priced / 2 without usage, 8 gateway calls under a feature filter, a
  provider balance error for a missing environment variable, light and dark
  modes, and no horizontal page overflow at 500 px.
