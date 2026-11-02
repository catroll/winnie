# apps/api 调试经验

OpenAI / Anthropic 透明代理落地时的踩坑与约定。

> 2026-11-01：网关改为字节级透传 + Winnie 密钥，厂商密钥由服务端注入。
> 本页其余内容仍然成立；下面两条已按新实现改写。

## 配置

1. **不用 `.env` 存路由选项**：路由与选项写在 `config.yaml`；本地覆盖用 `config.local.yaml`（gitignore）。
2. **厂商 API Key 不进配置文件**：从 `packages/winnie-llm/.env` 读取，由网关注入到上游请求头。
3. **用路径区分上游**：`/deepseek-openai/`、`/qwen-openai/`、`/openai/`、`/anthropic/`。
4. **协议与路径不再绑定**：每个路径都原样转发；`protocol` 只决定注入 `Authorization` 还是 `x-api-key`。

## 上游兼容

1. **base_url 有的带 `/v1`、有的不带**：`Route.api_base()` 统一补齐。
2. **thinking 模型**流式可能只有 `reasoning_content`。
3. **Anthropic 流式**看 `content_block_delta` 的 `text_delta` / `thinking_delta`。
4. **Anthropic `max_tokens` 必填**。

## 日志

1. 按路由：`data/logs/{route}/`。
2. ask 头部必须含 **client_url**（客户请求本代理的地址）与 **upstream_url**（真实上游调用地址）。
3. 流式断开用 `finally` 兜底。
4. ask 记完整 JSON；不记密钥明文。

## 运行

- `uv run uvicorn winnie_api.main:app`
- 改 `config.yaml` 后需重启（或 `--reload`）；`GET /routes` 核对。

## API contract checks (2026-10-30)

1. **Assert against the response schema, not the ORM model.** `MeetingRead`
   deliberately omits `audio_key`; transcript segments expose
   `detected_language` and `translation_status`, not `language` or `status`.
2. **Signal:** the first end-to-end run reached `status=uploaded` and
   `status=ready`, then crashed with `KeyError: 'audio_key'`; the next run failed
   with `KeyError: 'language'`. The HTTP path was correct and the assertions were
   reading implementation names.
3. **Check:** before writing E2E assertions, read `meetings/schemas.py` or
   `app.openapi()["components"]["schemas"]` and use those keys. Keep sensitive
   fields such as `audio_key` out of the response model and, if needed, add a
   contract test that proves they stay out.

## arq/redis pool shutdown (2026-10-31)

1. **Signal:** the first real arq E2E completed the meeting (worker log showed
   `process_meeting_job ... 0.27s`), but stderr ended with
   `RuntimeWarning: coroutine 'Redis.close' was never awaited` from
   `jobs/queue.py`.
2. **Do not trust `inspect.iscoroutinefunction` for deprecated wrappers.**
   `ArqRedis.close` is decorated, and introspection reported `False` even
   though redis-py 5.3.1 defines `async def close(...)`; the warning is the
   authoritative signal.
3. **Fix:** `await pool.aclose()` in `ArqJobQueue.enqueue`; `close()` is a
   deprecated alias for `aclose()`. The fake-pool test in
   `tests/test_queue.py` asserts `aclose` is called, so a synchronous close
   cannot silently return.
4. **Verification:** Redis 8 + `arq winnie_api.jobs.worker.WorkerSettings`
   returned `arq status: ready` and three transcript segments; the same run
   printed `PONG` from `redis-cli`.

## SQLite schema drift (2026-10-31)

1. **Signal:** login worked, then `GET /api/v1/meetings` returned 500 with
   `sqlite3.OperationalError: no such column: meetings.audio_deleted_at`. The
   model and Alembic head both had the column; the local database did not.
2. **Cause:** `database.auto_create_tables` creates missing *tables*, not missing
   columns. This database was created before the `track_audio_deletion`
   migration, so it never gained the column and had no `alembic_version` row.
3. **Check:** `alembic current` printed no revision while `alembic heads` printed
   `d0b5c759ab65`. Treat that combination as a database created outside
   Alembic, not as "no migrations needed".
4. **Fix:** back up the database, add the column with
   `ALTER TABLE meetings ADD COLUMN audio_deleted_at DATETIME`, then
   `alembic stamp head`. Do not run `upgrade head` from base: the other tables
   already exist and the initial migration would fail on the first
   `CREATE TABLE`.
5. **Verification:** `alembic check` reports
   `No new upgrade operations detected.` Re-run the failing endpoint after the
   repair; a clean migration state alone is not proof that the query works.

## Optional overrides must be resolved before they reach collaborators (2026-11-01)

1. **Signal.** Every assistant turn returned
   `502 model call failed: 'NoneType' object has no attribute 'chat'`, while the
   assistant started cleanly and `/status` reported the configured model.
2. **Cause.** `build_assistant_service` gained optional `model` and `model_name`
   parameters for tests. It resolved them into `resolved_model`, but the
   `AssistantAgent` and `MemoryBuilder` were still constructed with the raw
   `model` parameter. With no override that value is `None`, so only the
   production path broke; a test that injected a model would have passed.
3. **Check.** Search the factory for the raw parameter after introducing a
   resolved value:
   `grep -n 'model=' packages/winnie-assistant/src/winnie_assistant/factory.py`.
   Every collaborator that needs a model must receive the resolved object.
   The API-level test fixture (`create_app` → `assistant_client`) is the guard:
   it exercises the default path, not the injection seam.
4. **Verification.** `POST /api/v1/assistant/chat` returned a reply and the
   console chat rendered it after the fix; the assistant suites pass.


## A stale uvicorn reloader and the IPv4/IPv6 admin proxy (2026-11-01)

1. **Signal.** `GET /healthz` on `http://127.0.0.1:8000` timed out with no
   bytes, while `ss -ltnp` still showed `:8000` listening and `ps` still showed
   the `uvicorn ... --reload` process. The admin dev server's `/healthz` proxy
   returned 502.
2. **Cause.** The reload child had crashed importing `winnie_chat`, which was
   added to `apps/api/pyproject.toml` but not yet installed in the venv. The
   reloader kept the listening socket and did not restart the child; `ps` showed
   `<defunct>` under the uvicorn pid.
3. **Check.** `ps -eo pid,ppid,stat,wchan:20,args | grep -E 'uvicorn|<defunct>'`
   showed the parent sleeping in `hrtimer_nanosleep` and a `<defunct>` child.
   A one-off `uvicorn` with the same `WINNIE_CONFIG` on port 8021 started and
   answered `/healthz`, proving the code and configuration were fine and the
   reloader was stale.
4. **Fix.** `uv sync` installed `winnie-chat`; `make stop` cleared the stale
   reloader. A detached `uvicorn` was started on `0.0.0.0:8000` for the local
   admin. Verify `curl http://127.0.0.1:8000/healthz`, the admin proxy
   `http://127.0.0.1:5173/healthz`, and `GET /api/v1/assistant/skills` returning
   401 (route present, auth required).
5. **Second trap: host family mismatch.** `make api` uses `API_HOST ?= ::`,
   while the admin Vite proxy defaults to `http://127.0.0.1:8000` (IPv4). On
   this host the `::` listener was IPv6-only: `curl -6 http://[::1]:8000` worked
   and `curl -4 http://127.0.0.1:8000` timed out, so the proxy returned 502.
   Check both families with `ss` and `curl -4`/`-6`; either bind the API to
   `0.0.0.0` for the local admin or set `VITE_API_PROXY_TARGET` to
   `http://[::1]:8000` when the API stays on `::`. A listener on one family does
   not prove the proxy's family.
6. **Follow-up.** The live recovery process was started without `--reload`.
   Restore the normal `make api` reload path only after `uv sync` and a clean
   `/healthz` check; a reload child that crashes on import leaves a listening
   socket that looks healthy from `ss` but serves nothing.

## A throwaway config inherits every path you did not override (2026-11-01)

1. **Signal.** A disposable API for a browser check used a minimal
   `config.yaml` with `auth.enabled: false`, `assistant.root` and `news.root`
   under `.tmp/`, and no `database.url`. It worked, so it looked isolated.
   Enabling auth on the same config made the first registration fail with
   `registration is closed; an account already exists` — the API had been
   reading the owner's real `data/winnie.db`.
2. **Why it stayed hidden.** With auth and meetings both disabled, the host
   builds no runtime at all (`if not (auth.enabled or meetings.enabled):
   return None`), so no database was opened. The false signal was "the config
   has no database section, therefore it has no database".
3. **Check.** Before starting a disposable instance, list every path key and
   assert the target, not the intent:

   ```bash
   grep -nE 'url|root|path' .tmp/demo/config.yaml
   # then, after startup, confirm the files it created are under .tmp:
   find .tmp/demo -newermt '-1 min' -type f | head
   ```

   In this case `data/winnie.db` had a recent mtime, which the check would have
   shown immediately.
4. **Fix.** Set every path explicitly in a disposable config — `database.url`,
   `log_root`, `ai.path`, `assistant.root`, `news.root`, and any capability
   root — using absolute paths under `.tmp/`. Never rely on a default path
   being harmless because the capability that uses it is disabled.
5. **Generalisation.** A test/demo configuration is judged by the files it
   touches, not by the keys it lists. Any capability that is enabled later will
   resolve its default against the real checkout, so a throwaway config must
   override defaults, not delete sections from them.

## A `ValidationError` raised after FastAPI's own validation is a 422, not a 500 (2026-11-01)

1. **Signal.** Every `POST /api/v1/phone/devices/{id}/sync` with
   `kind=calendar` answered `500 Internal Server Error`. The only clue was the
   traceback ending in
   `pydantic_core._pydantic_core.ValidationError: 1 validation error for CalendarItem`.
2. **Cause.** FastAPI validates the request *envelope* (`SyncRequest`) and
   returns 422 by itself, but the per-kind items are validated one layer deeper,
   inside `PhoneService.sync` (`ITEM_MODELS[kind].model_validate(item)`). The
   route only caught `PhoneError`, so the `ValidationError` escaped to the ASGI
   error middleware — a 500 with a server-side traceback and no field name.
3. **Why it matters beyond the status code.** A 500 says "the server is broken":
   the client cannot act on it, and the Android sync classified it as a dead
   network and aborted the run. A 422 carrying
   `String should have at most 64 characters (timezone)` is the whole fix
   instruction.
4. **Fix.** Catch `ValidationError` in the route and answer 422 with the first
   `loc` path:

   ```python
   except ValidationError as exc:
       raise _invalid_item(exc) from None
   ```

5. **Check.** Any handler that calls `model_validate` on user input *after*
   FastAPI's own validation needs this clause. Exercise it with one deliberately
   bad item (`{"external_id": "1", "timezone": "x" * 200}`) and assert 422 plus
   the field name — `test_a_rejected_item_is_422_not_500`.

   **Trap: `--reload` does not watch the packages.** `make api` runs uvicorn
   with `--reload`, and it watches `apps/api` only
   (`INFO: Will watch for changes in these directories: [.../apps/api]`). The
   phone code lives in `packages/winnie-phone`, so editing it changes nothing
   until the server restarts — a live request kept returning the old 500 while
   the source on disk was already fixed. Touch a watched file to force a reload
   (`touch apps/api/src/winnie_api/main.py`), then confirm with a real request
   against `127.0.0.1:8000`, not with the file you just edited.

6. **Generalisation.** When a request body is validated in two layers, both
   layers must map their failures to a client-visible 4xx. The inner layer has
   no idea it is behind HTTP, so the route owns the mapping; leaving it out
   turns a one-field typo into an opaque crash.

## A key is not a supplier API: model sync needs the internal endpoint (2026-11-01)

**Signal.** `DASHSCOPE_API_KEY` is set in `packages/winnie-llm/.env` and works
against the vendor (`GET https://dashscope.aliyuncs.com/compatible-mode/v1/models`
returns `200` with 184 entries), yet the Qwen supplier in the console has
`models: []` and the card reads `No supplier API; add one with Add route/API.`
There is no **Sync models** button to press, so nothing reports a failure.

**Cause.** Sync resolves its endpoint through
`ProviderRegistry._internal_integration(state, vendor_id, ("openai",))`, which
skips every integration with `gateway: true`. The seeded Qwen records
(`qwen-openai`, `qwen-anthropic`) are both **gateway routes**, so the call can
only raise:

```
AIProviderError: supplier 'Qwen' has no OpenAI-compatible API endpoint
```

The console never shows that message: `_vendor_view` projects `kind` from the
internal endpoint, so without one `vendor.kind` is `''`, and AIPage renders the
sync button only for `vendor.kind === 'openai'`. A missing record therefore
removes the affordance instead of failing it.

**Why it keeps happening.** `BLANK_FORM.gateway` is `true`, and `formForKind`
preserves that for `openai`/`anthropic`. A user who clicks **Add route/API**,
picks OpenAI and pastes the DashScope base URL creates one more *gateway route* —
the exact record sync ignores — and the card still says "No supplier API".

**Check.** Reproduce without HTTP, on a copy of the state file:

```bash
cp data/ai.yaml .tmp/qwen-sync-check/ai.yaml
apps/api/.venv/bin/python - <<'PY'
from pathlib import Path
from winnie_llm.keys import ApiKeys
from winnie_llm.registry import ProviderRegistry
reg = ProviderRegistry(Path(".tmp/qwen-sync-check/ai.yaml"), keys=ApiKeys.load())
print([(i.name, i.gateway) for i in reg.state().integrations if i.vendor == "qwen"])
PY
```

Then add the internal endpoint (`gateway=False`, kind `openai`, base_url
`https://dashscope.aliyuncs.com/compatible-mode/v1`) and `sync_models("qwen")`
returns 184 models on the same key.

**Lesson.** A credential answers "may I call", not "where do I call". Both are
separate records here, and the one that carries the URL is the one the UI
silently withholds an action for. When a capability is *absent* rather than
failing, look for a projection (`vendor.kind`) that gates its affordance and ask
which record fills that field.

## An OpenAI-compatible base_url is not always `/v1` (2026-11-01)

**Signal.** Adding Zhipu as a supplier produced `404` from
`https://open.bigmodel.cn/api/paas/v4/v1/models`, and the chat client would
have posted to `.../v4/v1/chat/completions`. The vendor is OpenAI-compatible,
but its API root is `/api/paas/v4`.

**Cause.** Two helpers hard-coded the suffix instead of asking whether the base
was already versioned: `sync_models` used
`f"{base}/models" if base.endswith("/v1") else f"{base}/v1/models"`, and
`_openai_base` appended `/v1` unless the base ended in exactly `/v1`.

**Fix.** One rule, one helper — `_VERSIONED_BASE = re.compile(r"/v\d+$")` in
`ProviderRegistry._openai_base`, used by both the sync URL and the chat client.
A base ending in any version segment (`/v1`, `/v4`) is the root; anything else
gets `/v1`. Guards: `test_model_sync_treats_a_versioned_base_as_the_root`,
`test_chat_client_keeps_a_versioned_base_as_the_root`,
`test_chat_client_appends_v1_to_an_unversioned_base`.

**Lesson.** "OpenAI-compatible" constrains the *shape* of the routes
(`/chat/completions`, `/models`), not the version segment in front of them. Any
helper that appends a path to a user-supplied base URL must decide what the
base already contains, and that decision belongs in one function used by every
caller — otherwise the second caller keeps the old assumption.

## A synced phone row is not evidence that the assistant wrote it (2026-11-01)

**Signal.** "I asked for a 13:00 reminder today, and one phone ended up with an
entry for 10/10 15:57–16:57." The synced table does contain such a row, so it
looks like corroboration:

```
device 5c146cec…  external_id 112  title 叫我起床
starts_at 2026-10-10 07:57:29Z  ends_at 08:57:29Z  timezone Asia/Shanghai
created_at 2026-11-01 00:02:11Z  updated_at == created_at
```

**What decides it is `phone_commands`, not the data table.**

```bash
sqlite3 data/winnie.db \
  "select device_id,status,error,payload from phone_commands;"
```

Both proposals and the one dispatched command carry
`starts_at = 2026-11-01T13:00:00+08:00`, target `e33cdb45…` (TAS-AL00), and the
dispatched one is `failed: No writable calendar is available on this phone.`
The row above belongs to the *other* device, was inserted at the phone's first
calendar sync 4 h 44 min **before** the turn, was never updated afterwards, and
no conversation ever proposed its title. Three independent facts, each of which
alone rules the assistant out: no command for that device, a payload time the
row does not match, an insertion that predates the request.

**The tell is in the timestamp.** `15:57:29` — odd minute *and* seconds — is a
clock reading taken at creation time ("now"), not a parsed request
(`13:00:00`). `PhoneCommandRunner.createEvent` writes only
`payload["starts_at"]`, so a seconds-precision start cannot have come from a
command; it came from whatever created the row on the device. The title is the
same argument: a write of ours carries the proposed title.

**Why nothing landed at all.** `propose_command` without a `device_id` targets
the most recently seen phone (`_require_device`,
`order_by(last_seen_at desc)`), `phone_calendar_create` has no device
parameter, and the PENDING text does not name the device either. So the request
silently went to TAS-AL00 — the phone with zero calendar rows and no writable
calendar — while `calendar()` and `search()` aggregate *every* device. Reads are
a union; a write lands on one arbitrary phone, invisibly. The failure was
correct; the targeting was not observable.

**Lesson.** A deferred write is audited in the command table (payload, status,
error) plus the push record of the result, never in the synced data table. To
judge "the assistant created this entry", compare its `starts_at` with the
command payload and its device with the command's `device_id`; on a mismatch the
real finding is what the command failed with. Corollary for the product: a
multi-device surface must name the device it is about to touch in the same
breath as the proposal, otherwise the owner cannot even falsify the claim.

## A `timezone` keyword argument shadows `datetime.timezone` (2026-11-01)

**Signal.** A chat request for today's schedule came back with one tool broken
and the rest of the turn intact: `phone_status` reported the sync time and
`calendar=45`, but the calendar read answered
`ERROR: AttributeError: 'zoneinfo.ZoneInfo' object has no attribute 'utc'`.
The assistant then had to tell the owner it could not read their calendar while
describing data it had just read — a single-tool failure dressed up as a
service outage.

**Cause.** `ToolBox.__init__` takes `timezone: ZoneInfo | None`, which shadows
the `datetime.timezone` module imported at the top of the same file. Its
default clock then read

```python
self.clock = clock or (lambda: datetime.now(timezone.utc))
```

so `timezone` was the `ZoneInfo` argument, and the attribute access only failed
when the lambda *ran*. Every read tool calls `self._now()`; `phone_status` and
`phone_apps` do not, which is exactly the split the chat showed.

**Why the suite missed it.** Every test built the toolbox with
`clock=lambda: NOW`, so the default lambda was never invoked — the injected
clock is what a test needs for determinism, and it is also what hides a broken
default. The tool wrapper renders any exception as `ERROR: <type>: <message>`,
so the failure reached the model as a string to paraphrase, not as a traceback
to inspect.

**Fix.** `self.clock = clock or (lambda: datetime.now(UTC))`. `datetime.UTC`
(since 3.11, and the package requires 3.11+) is the same object as
`timezone.utc` but is a different name, so an argument called `timezone` cannot
capture it. Renaming the keyword was rejected: `timezone=` is the repo-wide
name for this parameter on `WeatherService`, `GoldWatch`, `AssistantScheduler`
and the rest.

**Check.** `test_calendar_reads_with_the_default_clock` builds the toolbox with
a `ZoneInfo` and **no** clock, asserts `toolbox.clock()` is UTC-aware, and runs
`phone_calendar` end to end. Against the old line it fails with the reported
`AttributeError: 'zoneinfo.ZoneInfo' object has no attribute 'utc'`.

**Generalisable.** A parameter named after a module it shadows turns a
module-level idiom into a runtime error that no import-time check sees. When a
function binds a name that also names an import, every `<name>.<attr>` inside
it refers to the argument; prefer the stdlib alias (`UTC`) or a differently
named parameter, and make at least one test exercise the default path rather
than the injected clock.

## Memory that the prompt never reads is not memory: three gates between a fact and an answer (2026-11-01)

**Signal.** The owner said "记录到本地记忆，没有指定位置的情况下……默认武汉的天气", the
assistant answered "已记到本地记忆（procedures）" — and the next two questions
("明天有雨么？") were both answered from the configured 上海 forecast. The last
reply even said "这个预报是按配置里的**上海**查的，不是你常用的武汉", which is a
memory being read and then ignored.

**The write path was fine.** `~/Documents/Mine/Winnie/profile/procedures.md` held
`- [2026-11-01] 用户未指定地点时，若可获取经纬度，则按该经纬度查询当地天气；否则默认查询武汉的天气。 <!-- source: chat:20261101T150006Z-03f48b -->`
The failure is entirely on the read side, and there are three independent gates.

**Gate 1: only three categories reach the prompt.**
`winnie_assistant/memory.py` defines
`PINNED_CATEGORIES = frozenset({"identity", "preferences", "constraints"})`, and
`MemoryStore.digest()` yields only `entry.pinned` entries. `AssistantAgent.chat`
passes exactly `self.memory.digest()` as `memory_digest`, so a `procedures`,
`people`, `projects`, `domain`, `resources` or `episodes` fact is in **no**
model call of **any** turn. The `remember` tool schema
(`tools.py`, `"name": "remember"`) enumerates all ten categories with no marker
for the pinned three, so the model put a *preference* — "default location when
none is given" — into `procedures` and nothing objected.

**Gate 2: the only read path is a literal-phrase FTS search, and that path lags.**
`profile/` is in the file tools' protected set, so the model cannot list or read
it; the sole route is `search_documents` → `winnie-index`. Two separate problems:

- *Phrase, not concept.* `winnie_index/tokenize.py` builds one quoted FTS5
  phrase per query segment out of overlapping CJK bigrams. Against the live
  daemon on the same corpus: `{"query": "默认位置"}` returns
  `Winnie/profile/procedures.md`, while `{"query": "哪里的天气"}` returns only the
  conversation and not the memory. The owner's sentence — "如果我没有指定位置，
  应该获取哪里的天气" — is exactly the paraphrase that matches nothing: four
  `search_documents` calls returned `No local document matched that query.` and
  the assistant told the owner "没有找到任何关于'默认天气位置'的记录".
- *Five-minute lag.* `httpapi._sweep_loop` reindexes only every
  `config.scan_interval` (300 s); the per-root `watch: true` is written to
  `roots.watch` and **never read** — there is no inotify code in the package
  although `config.py`'s sample comment advertises "inotify acceleration".
  Catalog evidence: `Winnie/profile/procedures.md` mtime `15:03:07` →
  `indexed_at 15:06:20`; the two conversation files written 15:00–15:03 were all
  stamped `15:06:20`. A fact remembered at 15:00:08 is unfindable for up to five
  minutes, and the owner asked 73 seconds later.

**Gate 3: the tool cannot honour the memory even when the prompt has it.**
`weather_forecast` is declared with `"properties": {}` and
`additionalProperties: false`; `ToolBox._weather_forecast()` calls
`self.weather.check()`, which reads `config.weather.location`. The default
location is therefore a deployment setting with no per-turn input, and a
correctly stored "use 武汉" rule changes nothing about the answer. This is the
part that matters most: it makes the other two gates unobservable from inside a
conversation, because reading the memory and not reading it produce the same
reply.

**Generalisable.** "The assistant remembers" is a chain of four independent
contracts, and each can fail silently: the category→prompt mapping, the
retrieval path's query language, index freshness, and the tool's own inputs.
Before claiming a memory worked, check the last link — a tool with no parameter
for the remembered value cannot be steered by memory at all, and it will keep
producing plausible answers that look like they ignored the prompt.

**Checks to add, in the order they would have caught this.**
1. A test that `remember(category, text)` then `build_system_prompt()` contains
   the text for the category in use, or that `remember` refuses a category whose
   entries never reach a prompt. Today neither holds for `procedures`.
2. A `search_documents` test with a paraphrase, not the stored wording:
   `"哪里的天气"` must find the 武汉 entry.
3. A freshness test: write a memory file, then require it to be searchable
   without waiting for the sweep — either an explicit reindex call on write or a
   sweep interval short enough to state in the contract.
4. `weather_forecast` must accept `location` (or `lat`/`lon`) with the config as
   the default, and a test must prove a per-call location wins.

## A vendor's successful answer can carry a failure, and a bare `RuntimeError` is a 500 (2026-11-01)

1. **Signal.** Every `POST /api/v1/assistant/transcribe` answered
   `500 Internal Server Error`; the traceback ended in

   ```
   File ".../winnie_llm/asr/volcengine.py", line 161, in _raise_for_status
   RuntimeError: Volcengine ASR query failed: HTTP 200, code=20000003,
   message=[Normal silence audio] Handle response: no valid speech in audio
   ```

2. **Cause.** Two layers disagreed about what an error is. The adapter signalled
   *every* vendor condition — a real failure, a missing key, a successful query
   whose body holds no text — as a bare `RuntimeError`. The route caught
   `AIProviderError`, `httpx.HTTPError` and `ValueError` only. So nothing caught
   "the recording held no speech", and FastAPI's error middleware turned it into
   a 500 with a server traceback. The route's own docstring already promised
   `422 means the vendor answered with no speech`; the code below it could never
   produce one.

3. **Why it matters beyond the status code.** The client shows the same sentence
   for 422 and 502, but a 500 says "the server is broken". The real condition was
   the opposite: the vendor answered successfully, the server was fine, and the
   *recording* was empty. Nobody looking at a 500 can act on that.

4. **Fix.** The speech port now owns two error types —
   `ASRError` for "the provider call failed" and `ASRNoSpeech` for "the provider
   decoded it and heard nothing", both `RuntimeError` subclasses so existing
   `except RuntimeError` handlers (the meetings pipeline) keep working. The
   Volcengine adapter raises `ASRNoSpeech` for `code=20000003` and for a
   successful body with no segments, and `ASRError` everywhere else. The route
   maps `ASRNoSpeech → 422`, `ASRError → 502`, `AIProviderError → 503`,
   `ValueError → 400`, and logs one line with the upload's size and content type,
   because the byte count is the only trace a silent turn leaves behind.

5. **Check.** Grep the route's `except` list against what the adapter can
   actually raise — they are different files and nothing enforces the contract:

   ```bash
   grep -n "raise " packages/winnie-llm/src/winnie_llm/asr/volcengine.py
   grep -n "except " apps/api/src/winnie_api/assistant_voice.py
   ```

   Then exercise the "successful but empty" answer, not just the obvious failure:
   `test_a_silent_recording_is_not_a_provider_failure` scripts a `200` with
   `X-Api-Status-Code: 20000003`, and `test_a_silent_turn_is_a_client_error`
   asserts the route answers 422. A test suite that only mocks a transport error
   never reaches this branch.

   **Trap, again.** `make api`'s `--reload` watches `apps/api` only. This change
   touches `packages/winnie-llm`, so the running server kept returning the old
   500 until a reload happened for another reason; confirm with a real request
   against `127.0.0.1:8000`, and check the process start time against the file
   mtime (`ps -o lstart -p <uvicorn child>`, `stat -c %y <file>`).

6. **Generalisation.** A vendor's HTTP 200 is not a successful call: the status
   lives in a header (`X-Api-Status-Code`) and `20000003` is a *verdict about the
   audio*, not a failure. An adapter must translate that vocabulary into a small
   typed set the host can map onto status codes, or every vendor condition above
   the success code becomes an opaque 500 at the edge.

**Fixed (2026-11-01).** All four checks above now exist, and the gates they guard
are closed:

- `PINNED_CATEGORIES` gained `procedures`, and the category decides pinning —
  a stale `pinned: false` in an older file can no longer remove a rule from every
  prompt (`test_procedures_is_pinned_even_when_the_file_says_otherwise`).
- `MemoryStore.recall` matches the unpinned notes to the incoming message and puts
  the top six in the turn's prompt, so a paraphrase no longer depends on the model
  choosing to search (`test_recall_matches_a_paraphrase_of_a_stored_note`,
  `test_the_agent_feeds_recalled_memory_into_the_turn`).
- `remember` and the distiller post the file they wrote to the daemon's new
  `POST /admin/index` (`test_reindex_path_indexes_one_file_without_a_sweep`,
  `test_remember_indexes_the_note_immediately`). The sweep remains the backstop,
  and `scan.watch` is still inert — do not trust it.
- A strict miss is retried as an OR and labelled (`loose: true`), and the tool
  text says a miss is not proof of absence, so "nothing matched" can no longer
  become "it was never recorded" (`test_a_paraphrase_misses_strictly_and_hits_
  when_loose`, `test_a_miss_says_it_is_not_proof_of_absence`).
- `weather_forecast` takes `location`/`latitude`/`longitude`
  (`test_weather_tool_passes_the_location_through`, plus a live check that
  `check(location="武汉")` forecasts 武汉 while `check()` forecasts 上海).

**Lesson worth keeping after the fix.** Each gate was individually reasonable and
each had a test: pinned-vs-unpinned was the documented contract, the phrase
behaviour was the documented tokenizer, the sweep lag was the documented
interval. The regression was in the *composition* — no test asked "does a fact the
owner just asked to remember appear in the very next answer?". That question is
now four assertions in four files, which is the only form of "read the memory"
that a future refactor cannot quietly undo.

## An in-process Alembic run disabled every logger in the process (2026-11-01)

**Signal.** A test asserted that a warning carrying the provider's `infocode`
reaches the log. It passed alone and failed in the full suite with an empty
message list — the handler was attached to the right logger, at the right level,
and the record never arrived.

**Cause.** `apps/api/migrations/env.py` called `fileConfig(config.config_file_name)`,
and `logging.config.fileConfig` defaults to `disable_existing_loggers=True`: it
sets `disabled = True` on every logger that already exists in the process and
never restores it. `tests/test_migrations.py` runs Alembic in-process, so every
logger imported before it — `winnie_phone.geocode` included — was silently muted
for the rest of the session. Nothing raised: `logger.warning(...)` just stops
emitting.

**Check.** Ask the logger, do not reason about the handler:

```python
logging.getLogger("winnie_phone.geocode").disabled   # True here
getattr(logging.getLogger("winnie_phone.geocode"), "handlers", None)
logging.root.manager.disable                          # 0: not logging.disable()
```

**Fix.** `fileConfig(..., disable_existing_loggers=False)` in `env.py`. The
Alembic CLI runs in its own process, so this never showed up in production — but
any embedder that migrates at startup, any script, and any test run would lose
every log line it wrote afterwards, which is exactly the kind of silence that
makes a later bug undiagnosable.

**Generalisable.** A logging configuration is process-global state with no
uninstall. Two rules follow: a library or a migration must never reconfigure the
root logger's existing set (`disable_existing_loggers=False`, or leave it alone),
and a test that asserts on log output should attach its own handler to the exact
logger it cares about instead of trusting the root capture — the failure then
points at the code, not at whatever reconfigured logging earlier in the session.

## A `Settings()` fixture that names no database syncs into the owner's data (2026-11-01)

**Signal.** A test that had passed for weeks started failing with
`assert 0 == 1` on the number of provider calls — no exception, no schema error,
just an address arriving without anyone buying it.

**Cause.** `Settings.database.url` defaults to `sqlite+aiosqlite:///../../data/winnie.db`,
and the fixture built `Settings(log_root=..., routes={}, phone=...)` without
overriding it. The endpoint test therefore registered a device, synced a fix and
wrote an address into the owner's real phone data — and the row it left behind
(`Pixel`, `fix-1`, 39.996/116.48) became a legitimate reuse candidate for the next
run, which is why the new cache turned a passing test into a failing one. Two
bugs, one visible: the litter and the leakage.

**Check.** A test fixture that touches the app must name its own database, and this
grep is the audit:

```bash
grep -rn 'Settings(' apps/api/tests | grep -v 'database='
```

**Fix.** `geocode_settings` now builds
`DatabaseConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'geocode-api.db'}")`.

**Generalisable.** A default that points at production data makes "I forgot a
field" indistinguishable from "I read production", and a fixture that overrides
*most* of a config object is the usual shape of that mistake. A cached answer is
also a very effective detector: it makes the leftover state visible, because the
second run stops doing the work the first one recorded.

## A stored RRULE can be a private dialect, and a failed lookup becomes a fact (2026-11-01)

**Signal 1.** A calendar read with a window wide enough to reach December reports
`圆圆生日` on 2026-11-12. The event is on the phone on 2026-12-20. Nothing errors;
the row parses, the expansion returns one date, and that date is simply not the
event's date.

**Cause.** A Chinese calendar provider stores a lunar birthday as a Gregorian
`DTSTART` plus an RRULE whose `BYMONTH`/`BYMONTHDAY` are the **lunar** month and
day — a private convention layered on a standard field. `dateutil.rrule` reads
it as Gregorian, as it must. Confirmed on ten of the eleven yearly birthday rows
in `data/winnie.db`; `琪琪的生日` is genuinely Gregorian, so the rule cannot be
"mismatch means lunar".

**Check.** Compare both candidates for the start date before trusting the
expansion:

```bash
sqlite3 -header -column data/winnie.db \
  "select title, starts_at, timezone, recurrence from phone_calendar_events
   where recurrence != '' and title like '%生日%';"
```

```python
from borax.calendars.lunardate import LunarDate
ld = LunarDate.from_solar_date(2024, 12, 12)  # LunarDate(2024, 11, 12, 0)
# (11, 12) is the rule's BYMONTH/BYMONTHDAY -> the rule is lunar.
```

If `(BYMONTH, BYMONTHDAY)` equals the lunar pair, the rule is lunar; if it equals
the solar pair, it is Gregorian. A test table with the eleven real rows is the
only form of this check that survives a refactor.

**Signal 2.** The same day, the owner asked "圆圆生日是什么时候？" and the answer
was "手机通讯录、短信、日历里也没有匹配到这个名字". The turn contains **no tool
call**. The reply restated `profile/people.md`, where an earlier failed search
had been distilled into a fact.

**Cause.** Distillation treats a conversation sentence as knowledge, including
"the assistant could not find X". That negative then arrives through recall —
which is designed to be trusted — and pre-empts the search that would have
contradicted it. The memory is not stale; it was never a fact.

**Fix.** Never distill a negative lookup. Store what the owner stated or a tool
returned with a citation; a miss is a property of one query, not of the world.
The existing line about 圆圆 is removed, and the distillation prompt names the
case.

**Generalisable.** Two rules. A standard field can carry a private dialect, so a
value that parses is not a value that means what the parser thinks — compare it
against an independent derivation before it reaches a user. And a "not found"
must never be persisted as a fact: the next reader cannot tell it apart from a
verified negative, and the recall path is built to trust what is stored.


## `--reload` watches `apps/api`, so a change under `packages/` is not live (2026-11-01)

**Signal.** A behaviour changed in `packages/winnie-phone` kept the old result in
the running server while the same call in a one-off script did the new thing.

**Cause.** `uvicorn --reload` watches the working directory it was started in
(`apps/api`); the editable packages live in `packages/` and are outside it. The
reloader also keeps the parent environment, so a `.env` edit is equally invisible
until a real restart. `make status` reports `healthy` in both cases — a listening
port is not evidence that the code serving it is current.

**Check.** Compare the server child's start time with the file it should have
loaded:

```bash
ps -o pid,lstart,args -p "$(ss -ltnp | sed -n 's/.*pid=\([0-9]*\).*/\1/p' | head -1)"
stat -c '%y' packages/winnie-phone/src/winnie_phone/service.py
```

If the file is newer than the process, the process is not running that file.

**Fix.** `make restart SERVICES=api` after any change under `packages/`, or after
editing `apps/api/.env`. Restarting one service keeps the admin and index
processes alone.

## A rule stored in memory is a rule for every path that acts, not just the one with a model (2026-11-02)

**Signal.** The day after the memory read path was fixed, the 07:00 push still
said `天气提醒 · 上海` and the owner asked "为什么是上海的温度？" — while the chat
turn that followed correctly used the phone's coordinates.

**Cause.** Two paths act on the same stored rule: the chat turn (model reads the
prompt) and the scheduled watcher (no model, deterministic). The fix had been
applied to the first: `AssistantAgent` recalled `procedures.md:14`, the model
called `phone_location` and then `weather_forecast(latitude=…, longitude=…)`. The
watcher calls `service.check()` with no arguments and titles the notice from
`config.weather.location`, so it kept the configured 上海 forever.

**Check.** When a behaviour is described by an owner preference, grep for every
call site of the action, not every place the model reads the preference:

```bash
grep -rn "service.check()\|config.location" packages/winnie-assistant/src
```

Two hits — the chat tool and `WeatherWatch._check` — and only one had been taught
the rule.

**Fix.** `WeatherWatch._place` asks the phone for its latest fix and passes the
coordinates plus a readable label (`武汉市江夏区`, from city + district) when the
fix is newer than `weather.phone_fix_max_age_minutes` (120); otherwise the
configured place. The notice title, the webhook `location` and
`.state/weather.json:last_location` all report the place the forecast was for.

**And the second-order lesson.** A default in configuration is not a fact about
the owner: 上海 was the deployment's fallback, and both the model and the owner
mistook it for the owner's city. Anything a push says on its own — before any
question is asked — has to resolve the same live inputs as an answer, or it will
be wrong at exactly the moment the owner notices.

**Tests.** `test_the_scheduled_check_follows_a_fresh_phone_fix` (arguments and
title), `test_a_stale_fix_falls_back_to_the_configured_place`,
`test_a_broken_phone_does_not_cost_the_notice`,
`test_follow_phone_can_be_turned_off`, `test_the_place_label_prefers_city_and_district`.

## A memory citation is a promise about a line, and the parser was counting in a different file (2026-11-02)

**Signal.** Cleaning up `profile/procedures.md` showed the digest claiming the
default-city rule was at `procedures.md:8` while the file had it at line 13.

**Cause.** `_parse_file` numbers entries from `body`, i.e. after
`split_front_matter` removed the four header lines and the `# Category` heading.
The prompt asks the model to "cite path:line", and the number it was given
pointed four to five lines too high — at the top of the section, not the fact.
The index did not have this bug (it returns real file lines), so the two
citation sources disagreed about the same file.

**Fix.** The offset is computed once
(`len(text.split("\n")) - len(body.split("\n"))`) and added to every entry's
line. `test_entry_lines_are_file_lines_not_body_lines` pins it.

**And the cleanup itself exposed a dedupe bug.** `append` compared
`normalize(text)` — whitespace collapsed, lowercased — so "若可获取经纬度，则…"
and "若可获取经纬度则…" were two facts, and the distiller wrote the same rule
three times in three phrasings. Dedupe now compares `_fact_key`, which also drops
punctuation (`test_a_punctuation_variant_is_the_same_fact`). Near-duplicate
phrasing beyond punctuation is still possible; the storage-level guard is
approximate on purpose, and the `pinned` digest budget is what makes a
duplicate costly.

**Generalisable.** Any `path:line` you emit is checked by a human or a model
against the file, so the line must be computed in file coordinates, not in
whatever substring the parser happened to hold. And an "already remembered"
check that compares exact strings will keep a corpus of paraphrases; if the
writer is a language model, compare a normalised form — punctuation and
whitespace carry no fact.

## A delivery channel that writes to the same SQLite file locks the engine out (2026-11-02)

**Signal.** `proactive tick failed` with `sqlite3.OperationalError: database is
locked` while sending the evening digest, and no digest row and no push event
existed afterwards. The traceback ended inside the push channel's `send`.

**Cause.** The engine held one session with an open write transaction — the
digest row had been inserted and flushed — and then called the push channel,
which opens its own connection to write `push_notifications`. SQLite allows one
writer; the second connection waited out the busy timeout and raised. Nothing
was wrong with the push service or the digest query.

**Check.** The failed digest left no row (the old code rolled back), so the
evidence is the log's final frame plus an empty `proactive_items` digest query
and a missing `push_notifications` row for the same minute. Reproduce with a
channel that writes to the same database in a test; the test passes only if the
engine commits before calling out.

**Fix.** `_deliver` commits the proactive session before it invokes a channel,
and a digest whose delivery fails is deleted rather than left planned, so the
next tick can retry the slot. The general rule is not about digests: **a channel
is I/O, and a database write transaction must not be held across it.** If the
channel writes to the same store, the open transaction is a lock you are holding
against yourself.

**Also learned the same day.** The API was down from 01:30 to 22:15 because the
process that owned it was gone; the 09:00 birthday reminder never fired, and on
restart the still-planned item would have fired at 22:00. Dispatch now expires a
P0/P1 item older than `catch_up_hours`; a late reminder is not a reminder. A
self-hosted reminder engine is only as reliable as the process running it.

## A mailbox you send to yourself proves SMTP, not delivery (2026-11-02)

**Signal.** The email rung reported success — `receipt: smtp ninedoors@126.com` —
and the message appeared in the account's Sent folder with a server-assigned
`Message-Id`, yet the INBOX still held 46 messages and Junk still held 2. Read
literally, that says the mail left and never arrived.

**Cause.** 126 (Coremail) archives a message addressed to the account's own
address in Sent and does not loop it back into INBOX. Nothing was lost: the SMTP
`250`, the Sent copy and its `Message-Id` are the whole proof a self-addressed
test can produce, and they are exactly what the channel can observe. "It is not
in INBOX" is therefore not a delivery failure for this test — only a second
mailbox settles inbox delivery.

**Check.** Ask the account where the message is, not where you expected it:

```
imaplib.Commands['ID'] = ('NONAUTH', 'AUTH', 'SELECTED')
M = imaplib.IMAP4_SSL('imap.126.com', 993)
M.login(user, authorization_code)
M._simple_command('ID', '("name" "winnie" "version" "0.1" "vendor" "catroll")')
M.select('"&XfJT0ZAB-"')      # the Sent folder, modified UTF-7
```

The `ID` command is not optional on 126: without it `SELECT` answers
`NO [b'SELECT Unsafe Login. Please contact kefu@188.com for help']` for every
folder, which reads like "IMAP is disabled for this account" or "the
authorization code is wrong" and is neither — it is the client-identification
requirement NetEase imposes. `imaplib` has no `ID` verb, so it must be registered
before use, and the failure without it is a `SELECT` error, not a login error.

**Second trap, same afternoon.** An `.env` that gains `WINNIE_SMTP_PASSWORD`
changes nothing until the API is restarted: `make api` sources the file into the
shell that execs uvicorn, and `--reload` watches `*.py` only. The channel kept
answering `WINNIE_SMTP_USER / WINNIE_SMTP_PASSWORD are not set` while the file
plainly contained both. Confirm the *process*, not the file:

```bash
tr '\0' '\n' < /proc/$(pgrep -f 'uvicorn winnie_api' | head -1)/environ \
  | grep -c '^WINNIE_SMTP_PASSWORD='
```

**Generalisable.** A delivery test only covers the half it can observe. When the
sender and the recipient are the same account, the receiving side's archive is
the only record; a missing copy in the expected mailbox is a property of the
provider's routing, not of the send. Pick the second mailbox for the delivery
assertion, and use the sender's Sent folder for the "did it leave" assertion.
