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
