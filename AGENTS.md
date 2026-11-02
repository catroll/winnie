# Agent notes — winnie

Rules for AI agents working in this checkout. What the product does is in
`docs/`; this file only covers how to work here.

## Public internet access needs no confirmation

`curl` and the `web_search` / `web_fetch` tools are whitelisted for reading
public data. Do not ask for approval before a read-only HTTP request, and do not
hedge about whether it is allowed. Still cite the URL you used and when.

The whitelist covers reads. It does not license posting to a third-party
service, spending money or sending mail — those still need an explicit request.

## Weather lookups go through the cache, never straight to the API

Model context is discarded when a conversation ends, so a forecast fetched today
is invisible tomorrow. The cache on disk is what makes a repeat question cheap
and honest about how old its answer is.

Always use the helper; a hand-written `curl` to `api.open-meteo.com` writes
nothing to the cache and hands the next session a blank slate.

```sh
python3 scripts/weather-cache.py --place 武汉             # today
python3 scripts/weather-cache.py --place 武汉 --days 7    # a week
python3 scripts/weather-cache.py --lat 30.59 --lon 114.31 --days 3
python3 scripts/weather-cache.py --place 上海 --hourly    # hourly series too
python3 scripts/weather-cache.py --force --place 武汉     # ignore the cache once
python3 scripts/weather-cache.py --status                 # what is cached, and how old
python3 scripts/weather-cache.py --purge                  # drop expired entries
```

No place named: the project default is 武汉 (`docs/decisions.md`); do not quietly
substitute the machine's own location.

### What is stored, and for how long

`.cache/weather/cache.json` — gitignored, created on first use. One entry per
question, keyed by coordinates rounded to 2 decimals (~1 km), the forecast
window, the local date and the timezone. Two phrasings of the same place and
window share one entry; "today" can never be answered with yesterday's forecast
because the local date is part of the key.

| Entry | TTL | Why |
| --- | --- | --- |
| place → coordinates (geocoding) | 7 days | coordinates do not move |
| today (`--days 1`) | 30 min | the window people re-ask within the hour |
| 2–3 days | 3 h | model output updates a few times a day |
| 4–7 days | 6 h | same |
| 8–16 days | 12 h | same, and past two days it is mostly noise |

The helper prunes expired entries on every write and caps the file at 200
entries, so it does not grow forever.

### Quoting the result

The helper prints one JSON object. Put its `cache.note` into the reply, in the
user's language — for example "数据取自 12:03，8 分钟前，有效期 30 分钟". Never
present a cached value as if it were live.

- `cache.hit: true` — served from disk; no network call was made.
- `cache.stale: true` — the entry expired and the live request failed, so the
  payload is old. Say so, and retry before the user relies on it.

### When the cache is wrong

`--force` bypasses it for one call. If `.cache/weather/cache.json` is ever
corrupt the helper starts from an empty cache rather than failing; delete
`.cache/weather/` to reset it completely.

## Other time-sensitive public data

Copy the pattern — a keyed entry, an explicit TTL, and a provenance note — rather
than re-fetching silently. Gold prices, exchange rates and news all change
during a conversation; a cache that states its age beats a fresh-looking number
with no timestamp.

## The product's confirmation gate is code, not prompt

`winnie-local`'s `LocalExecutor` decides whether a local action runs. A
whitelisted read-only action now runs immediately; anything else is reviewed and
then waits for the owner's "确认" in a later turn. Remembered preferences reach
the model as prompt text only, so a note in `profile/` saying "don't ask me"
cannot suppress the gate — that is exactly the bug that made the owner repeat
himself three times. To change what gets confirmed, change the policy or its
config, never the wording of the prompt.

## Never write secrets into the cache

It is plain JSON on disk (outside git). No tokens, no auth headers, no mailbox
credentials.
