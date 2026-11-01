# winnie-news

**A seven-stage pipeline that turns many news sources into a traceable draft
and a dated digest.**

`winnie-news` is the news capability of [Winnie](../../README.md). It collects
reports from RSS, Tencent News and tophub-style hot lists, cleans and extracts
them, groups reports of the same real-world event, searches for missing
coverage, writes one draft per event, reviews that draft before it can be
published, and assembles the drafts that passed into a briefing. Every stage
emits a versioned JSON document and keeps the URL, source and fetch time of
every fact, so a published sentence can be traced back to the article it came
from.

The module is usable as a library, from its stage command-line entry points, and
through the Winnie API and console.

## Pipeline

| Stage | Directory | Input | Output | Status |
|---|---|---|---|---|
| 1 Material | `material/` | RSS, Tencent hot list, tophub | `winnie.news.material/v1` | Implemented |
| 2 Clean | `clean/` | material items | `winnie.news.clean/v1` | Implemented |
| 3 Aggregate | `aggregate/` | clean items, event store | `winnie.news.event/v1` | Implemented |
| 4 Search | `search/` | event cards | `winnie.news.search/v1` | Implemented |
| 5 Generate | `generate/` | event coverage, search leads | `winnie.news.draft/v1` | Implemented |
| 6 Review | `review/` | draft, event material | `winnie.news.review/v1` | Implemented |
| 7 Digest | `digest/` | reviewed drafts | `winnie.news.digest/v1` | Implemented |

Stage 4 is not a serial blocker: with no search provider configured the
pipeline records that and drafts from the material alone. Stages 5 and 6 form
the only loop — a failed review returns concrete feedback to the writer, twice
by default, and then the draft is marked `needs_human` instead of being
published. Stage 7 adds no model call: it keeps the drafts whose review passed,
cuts them to a window and a count, and renders them.

## Output contracts

- **Draft:** `title`, `content` and `tags` are the publishable text — about 200
  Chinese characters, plain prose in paragraphs, no link, no outlet name and no
  source list. `brief` and `category` feed the digest; `sources` and
  `uncertainties` are the audit trail and never appear in the text.
- **Review:** rule checks (length, paragraphing, tag hygiene, category, text
  hygiene, invented numbers) plus one model review against the same contract;
  only a draft that passes both is digest-eligible.
- **Digest:** the last two calendar days, at most 30 items newest first, in the
  four sections 国内要闻 / 国际要闻 / 财经科技 / 体育文娱, rendered as standard
  sections, a morning/evening one-liner list or a Markdown table.

Both contracts are the owner's editor plugins, quoted verbatim into the prompts:
the vendored `SKILL.md` files ship under `src/winnie_news/prompts/` and their
sha256 hashes are pinned by a test, so a retold rule fails instead of drifting.
Every rule with a mechanical consequence is also enforced in code;
[docs/news.md](../../docs/news.md) records which rule is a plugin rule and which
is a pipeline rule.

## Running

The package is part of the Winnie workspace. Run it through the API (`make api`,
then the console's News page), or drive a stage directly:

```bash
cd packages/winnie-news
PYTHONPATH=src python -m winnie_news.material.fetcher --type rss --rss-limit 2 -o /tmp/material.json
PYTHONPATH=src python -m winnie_news.material.rss --list
PYTHONPATH=src python -m winnie_news.material.tencent -o /tmp/hot.json
```

A full offline pipeline run uses the deterministic fake model:

```python
from winnie_news.config import NewsConfig
from winnie_news.fake import FakeNewsLLM
from winnie_news.service import NewsService

service = NewsService(NewsConfig(enabled=True, root="/tmp/news"), llm=FakeNewsLLM())
await service.startup()
run = await service.run_now()
```

## Data and providers

- The material contract is defined in
  [`src/winnie_news/common/material.py`](src/winnie_news/common/material.py).
  Items have a fixed field set; source-specific fields live in `extra`.
- The model is a port. The host binds three AI features — `news.extract`,
  `news.write`, `news.review` — in the console, and the pipeline never sees a
  vendor name or an API key.
- Search is a port too. The host passes the assistant's search provider; without
  one the stage is skipped rather than failing.
- The registry of feeds and sources ships with the package as
  `material/sources.json` and `material/rss_sources.json`.

## Design notes

- **Rules before models.** Duplicate links, navigation text and empty items are
  dropped without a model call; models do extraction, judgement and writing,
  never a full pairwise comparison.
- **The event store is a file.** Recall uses an in-memory inverted index rebuilt
  from `events.json`; there is no database or vector store to keep in sync.
- **Numbers are checked mechanically.** The review stage flags any number in the
  draft that appears nowhere in the material — the one fact check that cannot be
  talked around.
- **The text carries no source apparatus; the JSON does.** A URL in the
  publishable text fails review, while the same URL in `sources` is what makes
  the sentence traceable. The two requirements are not in conflict because they
  apply to different documents.
- **Article text is untrusted.** Prompts pass it as JSON in an envelope and tell
  the model to ignore instructions inside it; every model reply is parsed as
  JSON and validated against a schema.

The repository-level design, contracts and operating notes are in
[docs/news.md](../../docs/news.md).

## Limits

- The tophub and Tencent sources depend on their public endpoints and, for
  Tencent, on a local `tencent-news-cli`; a single failing source never fails a
  batch.
- The shipped RSS list enables only the six `chinanews.com.cn` feeds; the
  international and Google News entries are disabled because they timed out
  when the list was checked. Enable what your network reaches in
  `material/rss_sources.json`.
- The tophub daily digest currently answers "the endpoint returned no data";
  the failure is isolated per source.
- Aggregation quality depends on the extractor model; a cheap model that returns
  few actors or places produces more separate events, and a digest can then show
  two items about one event.
- The digest's window and count are configuration; there is no per-request
  window yet. The digest covers one run, not the whole event store.
