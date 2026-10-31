# apps/api 调试经验

OpenAI / Anthropic 透明代理落地时的踩坑与约定。

## 配置

1. **不用 `.env`**：路由与选项写在 `config.yaml`；本地覆盖用 `config.local.yaml`（gitignore，可放 `proxy_api_key`）。
2. **厂商 API Key 不进配置文件**：只透传客户端 `Authorization` / `x-api-key`。
3. **用路径区分上游**：`/deepseek-openai/`、`/qwen-openai/`、`/openai/`、`/anthropic/`。
4. **协议与路径必须匹配**：openai 路由无 `/v1/messages`；anthropic 路由无 `/v1/chat/completions`。

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
