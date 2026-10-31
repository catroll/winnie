# News production pipeline

Last updated: 2026-11-01.

`packages/winnie-news` turns many news sources into a reviewed draft. This
document records what the pipeline promises, how the stages are wired, and what
is deliberately not done yet. The package [README](../packages/winnie-news/README.md)
covers the module itself.

## Status

| Stage | Contract | Status |
|---|---|---|
| 1 Material | `winnie.news.material/v1` | Done: RSS, Tencent hot list, tophub daily/node lists |
| 2 Clean | `winnie.news.clean/v1` | Done: rules, LLM extraction, second filter |
| 3 Aggregate | `winnie.news.event/v1` | Done: cards, inverted recall, scoring, gray-zone review |
| 4 Search | `winnie.news.search/v1` | Done: query construction, dedupe, classification |
| 5 Generate | `winnie.news.draft/v1` | Done: sourced draft, JSON and Markdown |
| 6 Review | `winnie.news.review/v1` | Done: rules plus one model review, bounded retry |

Verified end to end against real sources with a test-only fake supplier: RSS
feeds with 60 material items, cleaned items, events, drafts and reviews; a
deliberate failure (unbound model) left a `failed` run; source health recorded
`ok` and `error` per feed; a draft regenerated from revision 1 to 2. The console
and the assistant skill operate the same service.

Not verified: a real (non-fake) writing model on a full batch. Provider calls
are exercised by the existing integration tests, but draft quality with a
production model is unmeasured.

## Design goals

1. **Each stage does one thing.** The previous stage's JSON is the next stage's
   input, so any stage can be rerun from disk.
2. **Rules before models.** Duplicate links, navigation text, empty items and
   hard conflicts (type family, location, time window) are decided without a
   model. Models extract, judge gray zones and write; they never compare every
   pair.
3. **Traceable to the article.** Every decision keeps the material id, source,
   URL, publication time and the card it was based on. A draft cites only URLs
   that existed in its input.
4. **Bounded retries.** Generate and review form the only loop. It stops after
   `news.max_review_retries` and marks the draft `needs_human`.
5. **Article text is untrusted input.** Prompts embed it as JSON in a
   `<material>`/`<event>` envelope and state that instructions inside it are
   data; every reply is parsed as JSON and validated by a schema.
6. **A file is the database.** The event store and each run are readable JSON
   plus Markdown under `data/news`.

## Stages

### 1 Material (`winnie_news.material`)

One document per source, items flattened into `items` with `extra.source_type`,
`source_name` and `source_id`. Sources are the RSS list in
`material/rss_sources.json`, the Tencent hot list (through the local
`tencent-news-cli`), and tophub daily and node lists in `material/sources.json`.

- A source failing is recorded in `sources[].error` and never fails the batch.
- Cache: `news.material.cache_dir` (default `cache/material` under the news
  root), `max_age_seconds` (default one hour), one file per source.
- Fetchers are synchronous `urllib` code and run in a worker thread; the retry
  loop treats a 200 response with an unparsable body as a failure.

### 2 Clean (`winnie_news.clean`)

Rule pass, then one extraction call per item, then a second filter.

Rules drop empty titles, link-only titles, navigation/advert text, too little
text, items with neither a body nor a link, duplicate canonical URLs, duplicate
normalised titles, and (optionally) items older than `clean.max_age_days`.

The extractor returns title, summary, language, event type hint, entities,
locations, event time, key numbers, claims, topic keywords and a quality
verdict. `clean.min_quality_score` drops thin items; `clean.max_items` bounds the
cost of a batch and is applied by rank and recency.

### 3 Aggregate (`winnie_news.aggregate`)

Each clean item becomes an event card: fixed taxonomy, time, actors, locations,
action, object, key numbers, keywords, summary.

- Recall uses an inverted index over type, type family, actors and keywords.
  Hard filters reject a candidate on a type-family conflict, a location
  conflict (two different countries; a country and its city is a hierarchy) or a
  time gap beyond the type's window (corporate 3 days, sports 2, disaster 7,
  policy 14, judicial 30).
- Scoring combines actor (0.30), object (0.20), keyword (0.20), number (0.15),
  time (0.10) and type (0.05), with inverse-document-frequency weights so a
  shared rare actor counts and a shared common word does not.
- Above `aggregate.auto_threshold` **and** with an anchor (shared rare actor,
  shared number, or shared object) the article merges without a model call.
  Between the two thresholds one verification call decides: `same` merges,
  `different` starts a new event, `uncertain` starts a new event with status
  `needs_review`. Below `gray_threshold` a new event starts.
- An article already in the store is skipped, so rerunning an overlapping batch
  cannot attach it twice.

### 4 Search (`winnie_news.search`)

Queries are built from the card (actor + action + object, then with location,
then actor + keywords), at most `search.queries_per_event`. Results are
deduplicated by canonical URL, classified as `known` (already an article of the
event), `new`, or `suspect` (snippet shorter than `min_snippet_chars`), and only
`new` results are offered to the writer.

The provider is the assistant's (`assistant.search`), so there is one search
integration to configure. `provider: fake` makes the stage offline; no provider
at all is recorded as a skipped stage rather than a failed run.

### 5 Generate (`winnie_news.generate`)

The writer receives the event card, every article (title, source, URL, time,
summary, excerpt, key numbers) and the search leads as leads, never as facts. It
returns title, subtitle, body, sources and uncertainties as JSON.

The generator filters sources against URLs that existed in the input; invented
links are removed and reported as an uncertainty. If the model attributes
nothing, the draft keeps the event's own articles as sources and says so.
An empty title or a body under 80 characters is a stage error, because nothing
useful can be reviewed.

### 6 Review (`winnie_news.review`)

Rule checks:

- **Format:** title length, body at least 200 characters, at least one source
  with an `http(s)` URL.
- **Fact:** every number in the body appears somewhere in the material. This
  cannot prove a sentence true; it does prove a figure was invented, converted
  or computed.
- **Completeness:** a single-source event with no uncertainty note, or one
  cited source when the event has several.
- **Style:** clickbait phrases, an exclamation mark in the headline, formulaic
  prose, first-person opinion when commentary is off.

Then one model review returns problems and feedback. A draft passes only when
the model passed it **and** no high-severity rule problem exists. A failed
review returns concrete feedback to the writer; after the retry budget it
becomes `needs_human` and nothing is published.

## Run lifecycle

A run is one directory under `data/news/runs/<run-id>/`:

```text
run.json                 # status, per-stage status/count/detail, stats, error
materials.json           # step 1 output (documents + flattened items)
clean.json               # step 2
events.json              # this run's event batch and per-article decisions
search.json              # step 4
drafts/<event_id>.json   # step 5, machine format
drafts/<event_id>.md     # step 5, the text a person edits
reviews/<event_id>.json  # step 6
```

The long-lived `data/news/events.json` holds events across runs; its indexes are
rebuilt on load. A run ends `done` when every draft passed, `partial` when some
draft needs a person or an event failed, and `failed` when a stage raised.

Only one run executes at a time. `POST /api/v1/news/runs` returns 202 and the
run continues in the request process; with the `arq` job backend that execution
still happens in the API process, which is a known limitation for a future
worker.

Because the process is the executor, a restart abandons an in-flight run. On the
next startup the service marks every run still `running` as `failed` with
`the service restarted while this run was in progress`, and its unfinished
stages as `failed` or `skipped`. A run therefore never stays "running" for ever
and the console never shows progress that will not happen.

### Source health

Each material batch records per-source outcomes in `data/news/source_health.json`:
last status, last success, last error and the consecutive failure count. That
turns "this batch has no items from feed X" into "feed X has failed 12 times
since Tuesday". The console's Sources card lists failing sources first, and the
record survives runs and restarts.

### Regenerating one draft

`POST /api/v1/news/runs/{run_id}/drafts/{event_id}/regenerate` repeats only the
write/review loop for one event, reusing the run's stored search results and
event. The new draft's revision is the previous revision plus one, and both the
JSON and the Markdown are replaced. A running batch is refused (409): both paths
write the event store and the drafts directory.

## Configuration

`apps/api/config.yaml`, block `news` (defaults shown):

| Key | Default | Meaning |
|---|---|---|
| `enabled` | `true` | Mount `/api/v1/news` and start the service |
| `root` | `../../data/news` | Runs, drafts and `events.json`; gitignored |
| `language` | `zh` | Language drafts are written in |
| `material.source_types` | `[]` | Empty means every enabled source |
| `material.rss_feed_ids` | `[]` | Empty means every configured feed |
| `material.rss_limit` | `null` | Per-feed item cap |
| `material.cache_dir` | `cache/material` | Relative to the news root |
| `material.max_age_seconds` | `3600` | Cache lifetime |
| `material.workers` | `5` | Concurrent collectors |
| `clean.max_content_chars` | `6000` | Truncation for the model call only |
| `clean.min_quality_score` | `0.3` | Drop extractions below this |
| `clean.max_items` | `200` | Batch cost bound |
| `clean.concurrency` | `3` | Parallel extraction calls |
| `aggregate.auto_threshold` | `0.85` | Merge without a model above this |
| `aggregate.gray_threshold` | `0.55` | Verify between the thresholds |
| `aggregate.window_days` | `7` | Fallback time window per event type |
| `search.queries_per_event` | `3` | Search budget |
| `search.results_per_query` | `5` | Results per query |
| `writing.target_chars` | `800` | Requested body length |
| `max_review_retries` | `2` | Feedback rounds before `needs_human` |
| `keep_runs` | `50` | Older runs pruned after a successful run |

`news.language` overrides `news.writing.language`; the latter is the default when
the generate stage is used outside the pipeline.

### Model bindings

The pipeline asks for a purpose, and the console binds each purpose to a
supplier model:

| Feature | Purpose |
|---|---|
| `news.extract` | Clean extraction and event cards, plus gray-zone verification |
| `news.write` | Draft writing |
| `news.review` | Draft review |

A fresh registry leaves every model feature unbound, news included. Add a
supplier and its API key in the console, sync its models, then bind all three.
Rebinding takes effect on the next run, without a restart. See
[ai-providers.md](./ai-providers.md).

A binding is not the same as a usable model. `GET /api/v1/news/status` reports
each news purpose with its supplier, model and missing key, and `ready` is false
when any of the three is unbound or its supplier key is absent. Starting a run
in that state returns `503` naming the exact variable to set — before a single
source is fetched, instead of failing at the first extraction ten seconds
later. A service without a readiness probe (a test double) is treated as ready.

## HTTP API

All routes require the bearer token when accounts are enabled.

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/news/status` | Readiness, per-feature model usability, counts, active run |
| POST | `/api/v1/news/runs` | Start a run (`202`); `409` if one is active |
| GET | `/api/v1/news/runs` | Recent runs |
| GET | `/api/v1/news/runs/{id}` | One run with stage status and stats |
| GET | `/api/v1/news/runs/{id}/drafts` | Draft summaries and reviews |
| GET | `/api/v1/news/runs/{id}/drafts/{event_id}` | Draft JSON and review |
| GET | `/api/v1/news/runs/{id}/drafts/{event_id}/markdown` | The Markdown draft |
| GET | `/api/v1/news/sources` | Per-source health across runs |
| POST | `/api/v1/news/runs/{id}/drafts/{event_id}/regenerate` | Rewrite and re-review one draft |
| GET | `/api/v1/news/events` | Stored events with coverage |
| GET | `/api/v1/news/events/{event_id}` | One event with its articles |

## Assistant and console

The assistant registers a `news` skill with `news_status`, `news_run`,
`news_run_status`, `news_drafts` and `news_read_draft`. The skill is
scheduled-safe, so a task can produce a morning batch; its instructions forbid
reporting a draft as published and require the run id and event id.

The console page `/news` shows the four counters, the run list, one run's stage
badges and drafts, and the selected draft with its review. It polls while a run
is running. The draft is rendered as Markdown, safely: `marked` parses it and
the tokens become React elements, so nothing is inserted as HTML. Raw HTML in a
draft is shown as text, images are not loaded (a remote pixel would report the
reader's IP), and link targets are limited to http(s), mailto and relative URLs.
Headings are shifted down two levels because the page owns `h1` and the card
title `h2`.

## Known limits

- A run executes in the API process. A restart loses an in-flight batch; startup
  then marks its record `failed` rather than leaving it `running`, but the work
  is gone. The `arq` backend does not yet move news into the worker.
- Draft quality depends on `news.write`; the fake provider produces a
  deterministic, repetitive draft for demos only.
- The shipped RSS list enables only the six `chinanews.com.cn` feeds. The BBC,
  NYT, Washington Post, FeedX and Google News entries are present but
  `enabled: false`: every one of them timed out from this network (curl exit 28)
  when the list was checked. Enable what your network can reach in
  `material/rss_sources.json`; a disabled entry costs nothing and documents the
  source.
- The tophub daily digest currently answers "the endpoint returned no data"
  every run. The failure is isolated and reported per source, but that source
  contributes nothing.
- Publication is out of scope. The pipeline ends at a reviewed Markdown file
  under `data/news`; sending it anywhere is a later capability.
- Regeneration covers one draft, not one stage: re-cleaning or re-aggregating a
  batch would have to reconcile the shared event store and is not exposed.
- The console renders the Markdown subset a draft uses (headings, paragraphs,
  emphasis, links, lists, blockquotes, code, tables). Raw HTML and images are
  intentionally shown as text rather than rendered.

## Follow-ups

Tracked in [todo.md](./todo.md): a worker-backed run, a real-model quality
comparison over the same batch, and a publication hand-off.
