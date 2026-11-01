# Model gateway

Winnie's gateway exposes OpenAI- and Anthropic-compatible routes at
`/{route}/...`. It forwards every path and method unchanged, and it is the only
place a model request is authenticated against a credential Winnie issued: the
caller presents a **Winnie gateway key**, and the gateway injects the **vendor
key** that belongs to the route. A client never sees a vendor credential.

There is no protocol translation. An OpenAI path goes to an OpenAI-compatible
upstream; an Anthropic path goes to an Anthropic-compatible one. Routes are
configured in the console (Admin → AI providers) and appear on the Gateway
routes page; `config.yaml` seeds them and is the fallback for a gateway-only
deployment.

## Requirements

1. **Path routing**: the first segment after `/` selects an upstream.
2. **Caller credentials**: a Winnie key from `data/gateway-keys.yaml`, sent as
   `Authorization: Bearer …`, `x-api-key`, or `x-winnie-key`.
3. **Vendor credentials**: resolved per route from
   `packages/winnie-llm/.env` (`DEEPSEEK_API_KEY`, `OPENAI_API_KEY`, …) and
   injected as `Authorization: Bearer` (OpenAI) or `x-api-key` (Anthropic).
4. **Transparent forwarding**: every path, method, query string and header that
   is not listed under [What is not forwarded](#what-is-not-forwarded) reaches
   the upstream unchanged; the upstream's status, body and headers come back
   unchanged.
5. **Local observability**: one ask/answer pair per route under `data/logs/`,
   and one line per call in the usage ledger with the key that made it.

## Keys

A gateway key is a Winnie credential, not a vendor key. Create one on the
console's **API keys** page (Workspace); the secret (`wsk_…`) is shown once and
only its SHA-256 digest is written to `data/gateway-keys.yaml` (mode `0600`).

| Operation | HTTP |
|---|---|
| List your keys (no secret, no digest) | `GET /api/v1/gateway/keys` |
| List every account's keys (administrator) | `GET /api/v1/gateway/keys?scope=all` |
| Create a key for yourself | `POST /api/v1/gateway/keys` |
| Revoke your key | `DELETE /api/v1/gateway/keys/{id}` |

**A key belongs to the account that created it.** Every signed-in account can
list, create and revoke its own keys without an administrator; the API scopes
the list to the caller and refuses a revoke outside it (404, so it does not
reveal another account's key). An administrator may list every key and revoke
any of them, which is how access is removed when an account goes away. A key
without an owner was created before ownership existed; only an administrator can
see or revoke it.

Because every active account can mint a key, every active account can spend the
supplier credit those routes use. That is the intended shape for a personal
deployment with registration closed; an open-registration instance must treat
"can sign up" as "can call the models", and revoke the account or its key
otherwise. Per-account route limits beyond the key's own allowlist are not
implemented.

A key may be limited to a set of route names. An empty list allows every route,
which is the convenient default for a personal deployment; a restriction makes a
key that belongs to a phone or a build agent harmless when it is copied
somewhere else.

Revoking is immediate and keeps the record: the usage ledger can still say which
key made an old call. There is no expiry time — revoke when it is no longer
needed. A lost key is replaced, not recovered.

The file is re-read when its mtime changes, so a key created in the console
works on the next request, and a second process sharing the file sees it
without a restart.

## Errors a caller can see

| Status | Meaning |
|---|---|
| `401` | No key, or a key that is invalid/revoked. Also returned when a vendor key is sent by mistake. |
| `403` | The key is valid but its allowlist does not include this route. |
| `404` | The path prefix is not a configured route. |
| `503` | The route exists but its vendor key is missing from `packages/winnie-llm/.env`. |

Bodies follow the route's protocol: `{"error": {"message", "type"}}` for OpenAI,
`{"type": "error", "error": {"type", "message"}}` for Anthropic, so vendor SDKs
surface them without special handling. `401` also carries `WWW-Authenticate:
Bearer`.

## Upstream credentials

Each route names a supplier. The key comes from the same environment the
internal assistant and meeting pipelines use, so there is one place to rotate
it:

| Supplier | Variable |
|---|---|
| `deepseek` | `DEEPSEEK_API_KEY` |
| `openai` | `OPENAI_API_KEY` |
| `anthropic` | `ANTHROPIC_API_KEY` |
| `qwen` | `DASHSCOPE_API_KEY` |

Editing the file requires an API restart; the process loads it at startup.
Rotating the key therefore cuts over every route for that supplier at once,
which is the point of one variable per supplier.

## What is forwarded

* the method and the path (`GET /v1/models`, `POST /v1/embeddings`,
  `POST /v1/messages`, `POST /v1/chat/completions`, `DELETE /v1/files/…`, …);
* the raw query string;
* every request header except the ones listed below;
* the request body byte for byte;
* the upstream response status, body and headers, including request ids,
  rate-limit headers and `Retry-After`.

Streaming is recognised by `Content-Type: text/event-stream` and passed through
chunk by chunk; an SSE frame is never re-serialised.

## What is not forwarded

| Header | Why |
|---|---|
| `Authorization`, `x-api-key`, `x-winnie-key` | They carry the caller's Winnie key. The vendor credential replaces them. |
| `openai-organization`, `openai-project` | Account scoping does not apply to a server-held key. |
| `cookie` | A browser session must not travel to a vendor. |
| `host`, `content-length`, `transfer-encoding` | Framing is recomputed by this side. |
| `accept-encoding` (request) | The body is re-sent decoded so it can be inspected; `content-encoding` and the stale `content-length` are dropped from the response. |
| Hop-by-hop headers (`connection`, `keep-alive`, `te`, `upgrade`, …) | They describe one socket, not a forwarded request. |

A route prefix named `api`, `routes`, `healthz`, `readyz`, `docs`, `redoc` or
`openapi.json` is reserved: it is never resolved as a gateway route, so a
supplier endpoint cannot shadow the host's own paths.

## Configuration

`apps/api/config.yaml`:

```yaml
gateway_keys_path: ../../data/gateway-keys.yaml

routes:
  deepseek-openai:
    protocol: openai
    base_url: https://api.deepseek.com
    vendor: deepseek
```

When `ai.enabled` is true the admin registry is authoritative and these route
entries are only the first-start seed; the console edits the live table. When it
is false, this block is the live table, which keeps a gateway-only deployment
working without a database. A route without `vendor`, or whose supplier has no
key in the environment file, answers `503` rather than forwarding unauthenticated
traffic.

## Logs and usage

`data/logs/{route}/` holds a `*-ask.txt` / `*-answer.txt` pair per call. The ask
file records the client URL, the upstream URL, the parsed body, and metadata
including `key_id`; neither the Winnie secret nor the vendor key is written.

Each call also appends one line to `data/usage/` with token classes, latency and
the estimated cost, attributed to `key_id` in `detail`. See
[usage.md](./usage.md).

## Discovery

`GET /routes` (administrator session) lists the live table:

```json
{
  "routes": [
    {
      "name": "deepseek-openai",
      "protocol": "openai",
      "base_url": "https://api.deepseek.com",
      "api_base": "https://api.deepseek.com/v1",
      "path_prefix": "/deepseek-openai/",
      "vendor": "deepseek",
      "api_key_env": "DEEPSEEK_API_KEY",
      "credential_present": true
    }
  ]
}
```

It never includes a credential.

## Calling it

Create a key on the API keys page, put the vendor key in
`packages/winnie-llm/.env`, then point any OpenAI-compatible client at
`http://[::1]:8000/{route}`:

```bash
curl -s 'http://[::1]:8000/deepseek-openai/v1/chat/completions' \
  -H "Authorization: Bearer $WINNIE_GATEWAY_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"model":"deepseek-chat","messages":[{"role":"user","content":"ping"}]}'
```

An Anthropic SDK sends `x-api-key`, which is also accepted:

```bash
curl -s 'http://[::1]:8000/anthropic/v1/messages' \
  -H "x-api-key: $WINNIE_GATEWAY_KEY" \
  -H 'anthropic-version: 2023-06-01' \
  -H 'Content-Type: application/json' \
  -d '{"model":"claude-sonnet-4-5","max_tokens":64,"messages":[{"role":"user","content":"ping"}]}'
```

## Smoke test

```bash
cd apps/api
uv run python scripts/smoke_test.py
```

The script reads a Winnie key from `WINNIE_GATEWAY_KEY` (or asks for one) and a
vendor key from `packages/winnie-llm/.env`.

## Limits

* One API process owns the live route table; a route added in the console is
  visible to that process immediately and to a worker on its next restart.
* A route's upstream is a base URL, not a proxy chain: there is no per-route
  timeout, retry or rate limit. The HTTP client has one 300-second timeout for
  every model call.
* A key is a bearer credential with an optional route allowlist. There is no
  per-key quota or spend cap; the ledger reports what each key used.

Debugging experience is in [LESSONS-api.md](./LESSONS-api.md).
