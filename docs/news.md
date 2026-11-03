# News production pipeline

Last updated: 2026-11-01.

`packages/winnie-news` turns many news sources into a reviewed draft and a
dated digest. This document records what the pipeline promises, how the stages
are wired, and what is deliberately not done yet. The package
[README](../packages/winnie-news/README.md) covers the module itself.

## Status

| Stage | Contract | Status |
|---|---|---|
| 1 Material | `winnie.news.material/v1` | Done: RSS, Tencent hot list, tophub daily/node lists |
| 2 Clean | `winnie.news.clean/v1` | Done: rules, LLM extraction, second filter |
| 3 Aggregate | `winnie.news.event/v1` | Done: cards, inverted recall, scoring, gray-zone review |
| 4 Search | `winnie.news.search/v1` | Done: query construction, dedupe, classification |
| 5 Generate | `winnie.news.draft/v1` | Done: title/content/tags, brief, category, sourced draft |
| 6 Review | `winnie.news.review/v1` | Done: rules plus one model review, bounded retry |
| 7 Digest | `winnie.news.digest/v1` | Done: window, sections, standard/brief/table render |

Verified end to end against real sources with a test-only fake supplier: RSS
feeds with 60 material items, cleaned items, events, drafts and reviews; a
deliberate failure (unbound model) left a `failed` run; source health recorded
`ok` and `error` per feed; a draft regenerated from revision 1 to 2; a digest
rendered in all three formats, with a shortfall stated when the batch was
smaller than the configured count. The console and the assistant skill operate
the same service.

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
   URL, publication time and the card it was based on. A draft's metadata cites
   only URLs that existed in its input; the publishable text carries none.
4. **Bounded retries.** Generate and review form the only loop. It stops after
   `news.max_review_retries` and marks the draft `needs_human`.
5. **Article text is untrusted input.** Prompts embed it as JSON in a
   `<material>`/`<event>` envelope and state that instructions inside it are
   data; every reply is parsed as JSON and validated by a schema.
6. **A file is the database.** The event store and each run are readable JSON
   plus Markdown under `data/news`.

## Prompt contracts

The writer and reviewer prompts **quote the owner's two editor plugins verbatim**.
`packages/winnie-news/src/winnie_news/prompts/news_drafting.md` and `hot_news.md`
are byte-for-byte copies of the plugins' `SKILL.md` files — including their YAML
frontmatter — with the provenance and sha256 recorded in
[`prompts/README.md`](../packages/winnie-news/src/winnie_news/prompts/README.md).
`winnie_news.skills.quoted_skills()` embeds them under a `<skill name="...">`
envelope; nothing inside is rewritten.

A paraphrase is not a requirement. Retyping a rule in English is where a
requirement quietly changes, and the first version of this prompt did exactly
that: it turned "约200字" into a 120-character hard floor, invented a 40-character
headline limit attributed to the plugin, and restated rules the owner had already
written. The plugin files are therefore the single source, and
`tests/test_skills.py` pins both hashes so an edit to a quoted rule fails the
suite instead of shipping.

The prompt adds only what a plugin cannot state, and labels it as pipeline
integration:

- the JSON envelope and the field names the pipeline parses;
- the field mapping: `title` / `content` / `tags` are the drafting skill's 成稿,
  `category` / `brief` are the hot-news skill's 分类 and 一句话速览, and
  `sources` / `uncertainties` are pipeline metadata that never enter the 成稿;
- this batch's settings: language, style, reader, the owner's position, and
  whether commentary is on.

The brief is the standard format's 条目说明 too, because the plugin defines no
second field for that line; the prompt says so, and the brief has to carry the
core fact, the people, the place and the latest progress the standard format
asks for. The hot-news 检索要求 is not a writer instruction: the material and
search stages perform the retrieval, and the prompt states that the material is
its result so the model does not try to browse.

The mechanical checks quote a plugin rule by name and number whenever they
enforce one, and are labelled as pipeline rules when they are not:

| Check | Source |
|---|---|
| 正文原则上约200字、可扩展到300字左右 | 内容规则 1 (`writing.target_chars` / `max_chars`) |
| 素材不足时可以写短稿 | 内容规则 8 (a short draft is a note, never an error) |
| 按语义分段、段间一个空行 | 内容规则 9/10 (checked at the 200-character target) |
| 成稿无链接、无具名引用、无来源字段 | 来源与引用规则 1–5 |
| 标签最多7个、AI 不用人工智能、党政领导人、泛化标签、同义堆叠 | 标签规则 1、4、5、9、11 |
| 四个分类之一 | hot-news 整理规则 分类 |
| 一句话尽量不超过40字 | hot-news 晨报/晚报 |
| 纯文本、无 Markdown | 输出规则 |
| 立场不被两面讨好稀释 | 立场与表达规则 2/3 |
| 数字必须出现在素材里 | pipeline rule, predates the plugins |
| 标题不超过40字 | pipeline rule, predates the plugins |
| 来源 URL 可用 | pipeline rule, traceability |

Anything not in that table is left to the model review, which receives the same
two quoted files and is told to quote the rule it applies in every problem.

The digest contract is the hot-news skill's own wording:

- 默认最近2天、30条; 按事件时间从近到远; four fixed sections (国内要闻, 国际要闻,
  财经科技, 体育文娱); 各分类独立编号.
- 同一事件不重复计数 is the aggregate stage's merge, not a second title-based
  dedupe in the digest; a duplicate in the digest means aggregation missed the
  merge, and that is information rather than something to hide.
- 不为了凑足条数加入不具备热点性的新闻, so only drafts whose review passed are
  eligible and a shortfall is stated with the real number.

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
returns title, category, brief, content, tags, sources and uncertainties as
JSON; the prompt carries the drafting contract above.

The generator owns the output shape. It normalises the category through
`categories.normalize_category` (falling back to the event type), trims and
deduplicates tags, caps them at 7, and takes the brief from the reply or the
headline. The article is accepted from `content` or the older `body` field, so a
model that has seen an older prompt still works. Sources are filtered against
URLs that existed in the input; invented links are removed and reported as an
uncertainty. If the model attributes nothing, the draft keeps the event's own
articles as sources and says so. An empty title or content under 80 characters
is a stage error, because nothing useful can be reviewed.

The draft renders three ways: JSON for machines, Markdown for a person reading
the console, and a plain-text `title` / `content` / `tags` form for publishing.
None of the three contains a source list or a link.

### 6 Review (`winnie_news.review`)

Rule checks:

- **Format:** title length, content above/below the plugin's 约200字 and
  300字左右, a paragraph break at that target length, a valid category, a brief
  present and 尽量不超过40字, a tag list of the right shape, and at least one
  source with an `http(s)` URL for traceability.
- **Text hygiene:** a URL, a Markdown link, a named-outlet citation or a source
  line in the publishable text, and Markdown structure in the content.
- **Tags:** 标签规则 5 (`人工智能`), 标签规则 9 (a Chinese state leader's
  name), 标签规则 11 (a generic `中国`/`法律`/`经济`/`时事` tag) and 标签规则 4
  (two tags that repeat the same subject).
- **Fact:** every number in the title, content or brief appears somewhere in
  the material. This cannot prove a sentence true; it does prove a figure was
  invented, converted or computed. Tags are excluded because a tag is a label,
  not a claim.
- **Completeness:** a single-source event with no uncertainty note, or one
  cited source when the event has several.
- **Style:** clickbait phrases, an exclamation mark in the headline, formulaic
  prose, first-person opinion when commentary is off, and false-balance hedging
  when a position is set.

Then one model review returns problems and feedback, checked against the same
contract: length, paragraphing, headline, brief, tags, category, the absence of
source apparatus, and whether the requested position (or neutral reporting) was
delivered. A draft passes only when the model passed it **and** no high-severity
rule problem exists. A failed review returns concrete feedback to the writer;
after the retry budget it becomes `needs_human` and nothing is published.

### 7 Digest (`winnie_news.digest`)

The digest is a read of what the run already wrote, not another model call. It
takes the drafts whose review `passed`, dates each item from the event (falling
back to the draft's write time), keeps the last `digest.window_days` calendar
days ending today, sorts newest first, cuts to `digest.max_items`, and groups the
result into the four sections.

Every draft that stays out is counted by reason in `excluded`
(`needs_review`, `out_of_window`, `over_limit`), and a batch smaller than the
configured count adds a note naming the real number. An empty section is shown
as `（本期无条目）` rather than dropped, so a reader can tell "no sports today"
from a rendering bug.

`render(digest, fmt)` produces the three formats the plugin shows:

- standard: the header `以下为 **YYYY年MM月DD日—MM月DD日** 近N天的 M 条热点新闻（…）：`,
  then `### 一、国内要闻` … with `1. **标题**：日期；说明`, numbered per section;
- brief: `📰 早安/晚安，以下是今日要闻速览（YYYY年MM月DD日）：`, then `【国内】` …
  with `1. 一句话速览` (the greeting is chosen by local hour, since the pipeline
  has no user to ask for 晨报 or 晚报);
- table: the plugin's `| 序号 | 日期 | 分类 | 新闻标题 | 核心要点 |`.

The run saves `digest.json` and `digest.md` in `digest.format`; the API can
render the other two from the JSON without rebuilding anything. Regenerating one
draft rebuilds the digest, so the briefing never contradicts a rewritten draft.

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
digest.json              # step 7, items, sections, exclusion counts, notes
digest.md                # step 7, rendered in news.digest.format
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

A *graceful* stop takes a different path: the lifespan `finally` awaits
`NewsService.shutdown()`, which cancels the run task, so the record ends `failed`
with `run cancelled because the service stopped`, the running stage is marked
failed, and the log carries
`news run <id> cancelled during stage '<stage>': the service is shutting down`.
That is the string `Ctrl-C` produces, and the one every dev reload produces:
`make api` passes `--reload` and uvicorn watches its own cwd (`apps/api`, the
default `reload_dirs`), so **any write under `apps/api` during a batch kills that
batch** — including a byte-identical rewrite. Writes under `packages/` and
`data/` are outside the watched tree and do not interrupt a run.

Neither string is a pipeline error; a real stage failure logs
`news run <id> failed` with a traceback instead. To tell a reload apart from a
crash, compare `run.json`'s `updated_at` with the reloader's own line:

```bash
grep -n "WatchFiles detected changes" .cache/logs/api.log | tail -3
```

### Source health

Each material batch records per-source outcomes in `data/news/source_health.json`:
last status, last success, last error and the consecutive failure count. That
turns "this batch has no items from feed X" into "feed X has failed 12 times
since Tuesday". The console's Sources card lists failing sources first, and the
record survives runs and restarts.

### Regenerating one draft

`POST /api/v1/news/runs/{run_id}/drafts/{event_id}/regenerate` repeats only the
write/review loop for one event, reusing the run's stored search results and
event. The new draft's revision is the previous revision plus one, the JSON and
the Markdown are replaced, and the run's digest is rebuilt from all its drafts so
the briefing never contradicts a rewritten article. A running batch is refused
(409): both paths write the event store and the drafts directory.

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
| `writing.target_chars` | `200` | 内容规则 1's 约200字 |
| `writing.max_chars` | `300` | 内容规则 1's 可扩展到300字左右 |
| `writing.stance` | `""` | The position to present; empty means neutral |
| `digest.window_days` | `2` | Calendar days ending today; `0` disables the window |
| `digest.max_items` | `30` | Most items in one digest |
| `digest.format` | `standard` | Format saved as `digest.md` |
| `digest.include_needs_review` | `false` | Include drafts that did not pass review |
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

### Cost per call

The news purposes run **without a chain of thought**. A supplier that thinks by
default would spend the output budget reasoning about a 200-character draft the
pipeline does not want reasoned about: before this, one draft cost 1,882 output
tokens and one review 2,429, of which most were reasoning nobody read, at 4 CNY
per million output tokens. Extraction, writing and review all state
`thinking: disabled`; the assistant does not, because its turns do benefit.
A provider that does not know the field is never sent it.

Two consequences worth knowing before you read a bill:

- **Output tokens are the cost.** Input is nearly free on a warm cache; a batch
  of 130 events costs about 2 CNY in output alone. `clean.max_items` is therefore
  a spending limit, not a fetch limit.
- **An exhausted balance stops the run.** The supplier answers `HTTP 402`, the
  client raises `LLMInsufficientBalance`, and the pipeline aborts instead of
  walking the rest of the batch: the run ends `failed` with that message, not
  `partial` with one identical failure per remaining event. When the console has
  a fresh balance snapshot showing zero, `/api/v1/news/status` reports the
  purpose as not ready and starting a run answers `503` with the amount, so a
  batch is not collected for a run that cannot finish.

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
| GET | `/api/v1/news/runs/{id}/drafts/{event_id}/text` | The plain-text `title`/`content`/`tags` form |
| GET | `/api/v1/news/runs/{id}/digest?format=` | The digest JSON and its rendered text |
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
badges and drafts, the selected draft with its review, and the run's digest. It
polls while a run is running.

The draft is displayed in the drafting plugin's required `title` / `content` /
`tags` form, with a copy button for that output and one for its Markdown
rendering. The digest card renders the stored digest in 标准版 / 晨报·晚报 /
表格 and copies the Markdown source, so the clipboard holds exactly what the
hot-news format defines. Both surfaces are safe by construction: the digest is
Markdown parsed by `marked` and turned into React elements, so nothing is
inserted as HTML, while the draft is plain text inside a `<pre>`. Raw HTML in a
digest is shown as text, images are not loaded (a remote pixel would report the
reader's IP), and link targets are limited to http(s), mailto and relative URLs.
Headings are shifted down two levels because the page owns `h1` and the card
title `h2`. A run from before the digest stage answers 404 and the card says
本次运行没有汇总 instead of showing an error.

## Known limits

- A run executes in the API process. A restart loses an in-flight batch; startup
  then marks its record `failed` rather than leaving it `running`, but the work
  is gone. The `arq` backend does not yet move news into the worker.
- Draft quality depends on `news.write`; the fake provider produces a
  deterministic, repetitive draft for demos only. Its tags are title fragments,
  so a demo digest shows the contract's shape, not editor-quality tags.
- The digest is assembled from one run's drafts, not from the whole event store.
  Two runs on the same day produce two digests; a cross-run briefing would need
  to reconcile their reviews.
- The digest's window and count are configuration. A per-request window (今天,
  本周) and a per-request category filter would have to travel with the run
  record, and are not exposed.
- Duplicate items in a digest mean the aggregate stage did not merge two reports
  of one event. The digest does not deduplicate by title: doing so would hide an
  aggregation miss that a reader should see.
- The standard format's 条目说明 is the same `brief` the one-line format uses.
  The plugin defines no separate field for it, so the brief is written to serve
  both; a long standard entry is not produced.
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
comparison over the same batch, a digest surface in the console and the
assistant, and a publication hand-off.
