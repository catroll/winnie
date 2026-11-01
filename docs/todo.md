# Winnie TODO

Prioritized follow-ups for the personal-assistant backend. Keep entries concrete
enough to start without re-reading the design. The milestone view and completion
status are in [plan.md](./plan.md).

## P0 — first real meeting and bilingual fixture

> `VOLCENGINE_ASR_API_KEY` is configured in the gitignored
> `packages/winnie-llm/.env` for this checkout; `winnie-llm` is the only reader.
> Restart the API after changing it (`make restart`): `uvicorn --reload` watches
> Python files, not the environment file.

- [x] Verify the new-console API-key auth against the live endpoint: a valid
      `X-Api-Key` is accepted, while a bogus one returns
      `HTTP 401, code=45000010, message=Invalid X-Api-Key`.
- [x] Enable the Doubao recording-file service for the API key's project, then
      smoke-test `VolcengineASRProvider` on real audio. Model line 2.0
      (`volc.seedasr.auc`) submits, polls and returns segments for both
      Mandarin (`大家好，今天我们讨论一下产品发布的计划。请把预算和时间表发给我。`)
      and English; usage reports the audio duration. Model line 1.0
      (`volc.bigasr.auc`) is still `HTTP 403, code=45000030` for this account,
      so 2.0 is now the default. Streaming 2.0
      (`volc.seedasr.sauc.duration`) upgrades with `101 Switching Protocols`;
      streaming 1.0 is still not granted.
- [x] Give segments a language before relying on translation. The 2.0 response
      has no per-utterance language, and its file-level LID
      (`request.enable_lid` → `result.additions.lid_lang`) mislabels a Mandarin
      TTS clip as `speech_dia_nan`, so it is not used. The pipeline now uses a
      script detector (Latin/CJK/kana/hangul, 40% dominance) when ASR omits the
      label; short segments inherit the previous language, and the first segment
      falls back to `local_language`. A real language-ID model remains an
      option for accented or heavily code-switched audio.
- [ ] Record the first real meeting: upload, transcribe, translate, minutes,
      export. Fix provider payload or timestamp assumptions found there.
- [x] Smoke-test the internal LLM path with a real `DEEPSEEK_API_KEY`:
      two-line translation preserved order and length, structured minutes
      returned topics/action items, and usage was 109 and 365 tokens. An invalid
      model returned `RuntimeError` with the provider's HTTP 400 message. Prompt
      quality, cost and long-transcript behavior still need the bilingual
      fixture and a real meeting.
- [ ] Add a 10–30 minute bilingual fixture (Chinese, English, code-switching,
      names, numbers) under a local-only fixture directory, never in Git.

## P1 — ASR vendor evaluation

> Doubao 2.0 now works, so this is no longer gated on the ASR vendor. It still
> needs the bilingual fixture and the first real meeting before a fair
> comparison.

- [ ] Build a small evaluation harness that runs the same audio through each
      `ASRProvider` and stores raw results plus metrics.
- [ ] Candidates: Aliyun Tingwu, Tencent Cloud ASR, local `faster-whisper`.
- [ ] Metrics: Chinese CER and English WER, language-switch handling, segment
      timestamp drift, speaker diarization quality, p50/p95 latency, cost per
      audio hour, data residency and retention terms.
- [ ] Test noisy rooms, remote calls, accented speech and long meetings; do not
      pick a vendor from a feature list.
- [ ] Record the decision and the measurements in `docs/decisions.md` or this
      file before adding a second adapter.
- [ ] Only after evaluation: add the winning adapter behind `ASRProvider` and
      keep Volcengine selectable.

## P1 — validate LLM translation and minutes

- [x] Implement an LLM `Translator` adapter that translates non-local segments
      in batches and preserves segment order.
- [x] Implement an LLM `Summarizer` adapter returning the existing
      `MinutesDraft`, with shape validation and invalid-highlight filtering.
- [x] Add retry/backoff for transient provider failures; retryable HTTP
      statuses and transport errors are retried, while 4xx responses fail fast.
- [ ] Measure latency, token usage and quality on the bilingual fixture; write
      `usage_records` for each call and compare with the fake baseline.
- [ ] Decide whether translation uses the same model as summarization or a
      dedicated translation API.
- [x] Add chunking/map-reduce for transcripts that exceed one model context:
      split by rendered characters, summarize each chunk, merge the partials,
      and record the summed token usage.
- [ ] Move prompts into versioned files (`meetings/prompts/`) once the wording
      stabilizes; `prompt_version` is already stored with minutes.

## P1 — production data and jobs

- [x] Split the monolithic backend into `winnie-core`, `winnie-gateway`,
      `winnie-meetings` and `winnie-knowledge` packages; `apps/api` is now the
      thin host that registers capabilities. Boundary tests forbid capability
      imports of the host or of sibling implementations.

- [x] Add Alembic migrations for the initial schema; `alembic check` is clean
      and the test suite verifies upgrade, downgrade and startup without
      `auto_create_tables`.
- [x] Rehearse the migration path on PostgreSQL 16: seed the initial schema,
      upgrade to head, verify all rows survive, and run register/create/upload/
      transcribe/minutes with `auto_create_tables: false`. A production
      backup/restore rehearsal is still required before deployment.
- [x] Verify the arq/Redis path end-to-end: the API enqueues, a separate
      `WorkerSettings` process transcribes/uploads, and polling reaches `ready`.
- [ ] Make arq the default once every deployment runs a worker; keep `inline`
      for local demos and tests.
- [ ] Add a storage adapter for object storage (private bucket, short-lived
      URLs) while keeping local disk as the default.
- [x] Define audio retention: `audio_retention_days` purges only audio for
      terminal meetings, the meeting API already purges on delete, and the arq
      cron plus `python -m winnie_api.cli.cleanup_audio` cover deployments
      without a worker.
- [x] Add a session cleanup job for expired `auth_sessions`; the arq worker
      schedules it daily and a standalone CLI is available for cron.

## P1 — phone data

The sync and the confirmed-write path are implemented and unit-tested; a real
handset has not run them yet. These are the next concrete steps.

- [ ] Run the client on a physical phone and verify every permission path:
      runtime grants, the notification-access grant, "allow all the time"
      location, a vendor calendar provider insert/update/delete, and
      WorkManager surviving a reboot and a process kill.
- [x] Add a phone page to the admin console: device cards with scopes and
      counts, per-kind tabs with filters, a cross-kind search, the assistant
      action queue, and revoke/restore/delete. Verified in headless Firefox in
      light, dark and mobile layouts.
- [ ] Poll approved commands on a short interval separate from the data sync,
      so a confirmed calendar change or SMS does not wait up to 30 minutes.
- [ ] Report SMS delivery: the current result means "submitted to the carrier".
      Use the `SmsManager` sent/delivered `PendingIntent`s and report the final
      state, and show a clear error when `SEND_SMS` is revoked.
- [ ] Propagate SMS and call-log deletions and edits. The `_ID` cursor cannot
      see them; a periodic `_ID` snapshot diff against `GET /devices/{id}/ids`
      would, at a few hundred KB for a large history.
- [ ] Expand recurring calendar events through `Instances` while keeping a
      stable event id for writes, so the assistant can answer "what is on
      Thursday" for a weekly meeting.
- [ ] Move message/notification search to FTS if a device accumulates years of
      data; `LIKE` is fine for the current volumes but does not scale.
- [ ] Decide on a long-lived device token. Background sync currently dies with
      the 30-day account session and records "session expired"; a scoped,
      revocable device credential would fix it without weakening account auth.
- [ ] Evaluate encryption at rest for the phone tables (SQLCipher or encrypted
      body columns + a derived search index) before recommending the feature to
      anyone who syncs SMS over a public network.
- [ ] If the owner wants it, capture MMS subject and part metadata without the
      media itself.

## P1 — push notifications

The stream and the Android foreground service are implemented and tested; a
handset has not run them yet.

- [ ] Run the foreground service on a real phone through a full day: Doze,
      a reboot, a process kill, a network switch between Wi-Fi and cellular, and
      a vendor task killer. Record what actually survives; the boot receiver and
      `START_STICKY` are guesses until then.
- [ ] Add Redis pub/sub for the hub before running more than one API worker, or
      pin the deployment to one worker in the operations guide.
- [ ] Add notification actions (mark done, snooze) and a deep link into the
      relevant conversation; tapping currently only opens the app.
- [ ] Feed the weather watcher and meeting/news completion into the push
      service. The weather watcher already has a webhook; a `Notifier` there
      removes the need for an owner-run webhook receiver.
- [ ] Per-device read/delivery state if a second phone is ever used; today the
      cursor is per app installation.
- [ ] Quiet hours and per-kind preferences beyond Android's channel controls.
- [ ] Consider a short "priority" notification path for a reminder that arrives
      while the phone is in Doze; without a system push service the only option
      is an exact alarm, which needs its own justification.
- [ ] An end-to-end test that runs the Android service against a real server on
      an emulator once KVM is available.

## P2 — accounts and security

- [x] Add login rate limiting: failures are counted per username, the account
      is locked temporarily after the configured threshold, and login returns
      `429` with `Retry-After`.
- [x] Add password change; the current password is required and every session
      is revoked.
- [x] Add password reset from the host: `python -m winnie_api.cli.reset_password`
      prompts or generates a password and revokes every session.
- [ ] Add email-based password reset if multi-user deployment needs it.
- [ ] Add account management/admin endpoints; the scope and sequencing are in
      the admin-console section below. Keep single-user registration closed by
      default until the role model lands.
- [x] Review token storage on Android: the bearer token is encrypted with an
      AES/GCM key held in the Android Keystore, not in plain preferences.
      Certificate pinning is not needed for the local MVP; revisit when the
      server is public.

## P2 — admin console: i18n and user management

> The console is English-only and has one implicit role. The shell, navigation
> and every page carry hardcoded copy, and a hidden menu is not access control.
> Sequence the work: role model and admin APIs first, then the Users page, then
> i18n over the final menu structure.

### Internationalization

- [x] Choose the frontend i18n layer before touching pages: evaluate
      `i18next` + `react-i18next` and `react-intl` against React 19 / Vite 8 /
      TypeScript 7, pin the maintained release, and record the reason in
      [decisions](./decisions.md). Do not hand-roll a dictionary or reload the
      page to switch language.
- [x] Extract every user-facing string under `apps/admin/src` into
      `src/i18n/locales/en.json` and `zh-CN.json`: navigation, page titles,
      cards, table headers, empty/loading/error states, form labels, help text,
      validation messages and action confirmations. API payloads, status codes
      and stored data stay English.
- [x] Add a language control in the header next to the theme menu (`en` /
      `zh-CN`): persist the choice in `localStorage`, default from
      `navigator.languages`, switch immediately without reload, update
      `document.documentElement.lang`, and format dates/numbers with `Intl`.
- [x] Add a key-coverage check (script or test) that fails when `zh-CN` misses a
      key or a component contains a hardcoded visible string; run it in
      `npm run build` or CI.
- [x] Verify all routes in Chinese and the switch back to English with the
      browser; a 390 px Chinese run also checked the mobile shell with no body
      overflow. Static key coverage is enforced by `npm run check:i18n`.

### User management

- [x] Add an explicit `admin` / `user` role to `users` with an Alembic
      migration. The first registered user becomes admin, later registrations
      are users, and an auth-disabled local deployment acts as admin. For an
      existing table, promote the oldest active user and document the backup.
- [x] Enforce the role server-side: `UserRead`, login and `GET /auth/me` return
      it, and an `admin_required` dependency protects every `/api/v1/admin/*`
      route with `403` for non-admins. Never rely on hidden menu items for
      security.
- [x] Classify the existing API surface before gating it: operator-only routers
      (AI registry, gateway route inspection, document-index admin, news, global
      usage, users) require admin; meetings, chat, tasks, skills and account
      remain owner-scoped. Add a test that a normal user gets `403` on every
      operator route, not only on the new Users endpoint.
- [x] Treat the cached role as UI state only: refresh `/auth/me` on load and
      after a `403`, and drop the Administration UI immediately when the server
      no longer grants the role. The API is the authority on every request.
- [x] Add admin user APIs: list/search with pagination; create a user with a
      generated temporary password shown once; reset a password (revoke all
      sessions); activate/deactivate (deactivation revokes sessions); change
      display name and role. Refuse to demote or deactivate the last active
      admin, or to deactivate the caller's own account.
- [x] Add **Administration → Users**: table with username, display name, role,
      status, created date and last session; search; create and reset-password
      dialogs; role/status actions; and a clear `403` state when a non-admin
      opens the route directly.
- [x] Emit one structured log line per role/status/password change (`actor`,
      `target`, `action`, `result`); never log a password or hash.
- [x] Tests: every admin route rejects a user, last-admin and self-deactivation
      guards, session revocation after deactivate/reset, pagination/search, and
      admin/user menu visibility with a guarded route.

### Menu information architecture

- [x] Replace the flat `NAVIGATION` array with one metadata source
      (`to`, `label`, `icon`, `group`, `adminOnly`) rendered through
      AdminLTE's `nav-header`/treeview, so i18n, menu rendering and route guards
      cannot drift.
- [x] Define the two groups:
      **Workspace** — Chat, Meetings, Phone data, Scheduled tasks, Skills,
      Account.
      **Administration** — Dashboard, Schedule, Users, AI providers, Gateway
      routes, Document index, News. (LLM usage lives in a tab of AI providers,
      not as a menu entry.)
- [x] Hide the Administration group for non-admin users; keep Account available
      to everyone. Guard each admin route, not only its link, and choose a safe
      non-admin landing page (Chat or a workspace overview) instead of rendering
      the admin Dashboard at `/`.
- [x] Verify both roles in the browser: admin sees both groups and lands on
      the Dashboard; a normal user sees only Workspace, lands on Chat, and gets
      the 403 panel on a direct admin route. The mobile shell was checked at
      390 px; the project still has no component-test runner.

## P2 — clients and realtime

- [x] Android client MVP in `apps/android`: sign in, record a 16 kHz mono WAV,
      upload, request transcription, meeting list, transcript and minutes views.
      Verified against the local backend with fake providers, including a
      JVM integration test that runs the same HTTP client the app uses.
- [x] Android sign-in and shell redesign: remembered username and password
      (Keystore-sealed, explicit opt-out, forgettable), a password eye, and a
      four-tab bottom-bar shell (Chat, Meetings, Phone, Account) instead of four
      text actions sharing one app bar.
- [x] Assistant chat in the Android client: Markdown rendering, cross-channel
      conversation history, collapsed tool output, resumed conversation. The
      wire contract and the renderer are tested; the device run is open.
- [x] 5-second change-checked phone sync: persisted per-scope tokens, content
      observers waking the loop, faster scopes on cheap probes, slower scopes on
      their own cadences, and a full-run fallback at WorkManager's 15-minute
      floor. Rule-level tests cover the decision, not the device.
- [x] English and Simplified Chinese UI: all user-facing strings in resources,
      system-locale selection, per-app language on Android 13+, and a test that
      keeps the key and format-argument sets identical.
- [ ] Run the chat, remembered-password, 5-second sync and live-caption flows
      on a physical phone; the current build predates that run, and the sync
      loop's battery cost is unmeasured.
- [ ] Streaming assistant replies, chat attachments and voice input on Android.
- [ ] Android client follow-ups: foreground-service recording so a backgrounded
      app keeps recording, export/share, offline cache, and automatic
      token-refresh handling.
- [x] Exercise the file-first flow from a physical phone against the LAN API:
      login, create, upload and transcribe. The first attempt exposed the
      missing server `VOLCENGINE_ASR_API_KEY`; the same meeting reached `ready`
      after the key was fixed. The app can read the resulting transcript.
- [ ] Run the live-caption build on a phone and fix the device-specific issues
      (mic permission UX, long-recording stability, cleartext/TLS
      configuration).
- [x] Realtime WebSocket ASR for live captions, reusing the final file-first
      pipeline for the finished recording. The backend proxies PCM to Doubao
      SAUC 2.0 over `/api/v1/meetings/{id}/stream`; the Android recorder shows
      provisional captions and still uploads the WAV for the canonical
      transcript. Remaining: foreground-service streaming, reconnect, streaming
      usage records, and incremental translation.
- [ ] Speaker diarization and per-speaker labels in transcript/minutes.
- [ ] Cross-meeting semantic search over knowledge notes (not raw transcripts).

## P2 — AI provider administration

- [x] Admin CRUD for suppliers, endpoints, model catalogs and feature
      bindings, with gateway routes derived from endpoints. Changing a
      supplier/model/binding applies on the next model call; the arq worker
      re-reads the file by mtime.
- [x] Seed gateway routes and the Volcengine ASR defaults on first start,
      leave model features unbound, validate kind/model compatibility when a
      binding is saved, and require an explicit model. There is no YAML or fake
      fallback. Covered by `apps/api/tests/test_ai_admin.py` and a Firefox run of
      create → bind → delete.
- [x] Remove `llm`, `assistant.model`, `meetings.translator` and
      `meetings.summarizer` from `config.yaml`; the registry is the only model
      choice. Move timeout/token/chunk tuning into the registry (`runtime`) and
      expose it in the console.
- [x] Separate gateway exposure from internal credentials: every
      OpenAI/Anthropic integration has a `gateway` flag, and a keyless provider
      cannot serve an internal feature. A `version: 1` file is migrated in place
      and keeps its old `internal-llm` record as a normal integration.
- [x] Group protocol endpoints under their supplier in the console (DeepSeek →
      one supplier). Features bind to a **model**, not an endpoint; the supplier
      API is resolved from the model. Models are synced from the supplier
      `/models` API or edited with **Models**, and supplier marks use the WLB
      vendor-icons set.
- [ ] Add an Anthropic Messages adapter for internal features if there is a real
      need to bind the assistant/translation/minutes to an Anthropic endpoint.
      DeepSeek's OpenAI-compatible endpoint already covers them today, so this is
      not on the critical path.
- [ ] Add a **Check** action per integration that performs one cheap call
      (`GET /models`, or a one-token chat) and reports the provider's HTTP
      response. "Key set" in the environment is not proof that the key works or
      that the model exists; the missing-key path is tested, the wrong-key path
      is not.
- [ ] Decide how the gateway route table should behave with more than one API
      process. Today only the process that handled the admin write refreshes its
      table; a TTL reload or a shared revision would remove the single-worker
      constraint. Keep this out of the provider-selection path until a
      multi-worker deployment is real.
- [ ] If entering keys in the browser becomes a requirement, design an encrypted
      secret store and a master-key lifecycle first. The current deliberate
      choice is environment-only, documented in `docs/decisions.md`; do not add
      a plaintext column as a shortcut.

## P2 — news production follow-ups

> The pipeline is usable end to end with the fake model. These items are what
> stands between "works" and "trustworthy on a real batch".

- [ ] Run a real `news.write` model over a full batch and compare it with the
      fake drafts on the same material: factual misses, invented numbers,
      clickbait, length and cost. Record the measurement in
      [docs/news.md](./news.md) before tuning prompts.
- [ ] Move a run into the arq worker. Today it executes in the API process, so a
      restart loses it and leaves its record `running`; add a startup sweep that
      marks abandoned runs `failed`.
- [ ] Render the draft Markdown in the console with a sanitizer, or keep the
      source view and document why. Do not add a renderer without tests for
      script/iframe injection through article text.
- [ ] Add per-draft regeneration and per-stage rerun from the console; the run
      directory already holds every stage document.
- [ ] Report source health (last success, consecutive failures, cache age) so a
      dead feed is visible before it silently empties a batch.
- [ ] Add a publication hand-off (Markdown to a chosen directory or a webhook)
      with an explicit human confirmation; keep publishing out of the pipeline
      until then.

## P3 — operational polish

- [x] Per-month LLM cost view in the console (`/ai?tab=usage`), built from the
      new usage ledger; it prefers provider-reported costs and estimates from
      `usage.prices`.
- [ ] Per-meeting ASR cost view from `usage_records`; ASR is not part of the LLM
      ledger, and streaming ASR still writes no row.
- [ ] Structured logs for `meeting_id`, `job_id`, stage and provider latency.
- [x] Health/readiness checks: `/healthz` is liveness and `/readyz` probes the
      database plus Redis when the arq backend is configured.
- [ ] Deployment guide: PostgreSQL, Redis, reverse proxy, backups, restore
      rehearsal.
- [ ] Keep adding to `docs/decisions.md` for every provider or architecture
      choice that is not obvious from the code. The Android client entries are
      recorded there.

## P3 — weekly schedule tool

The owner's schedule is Markdown in the WLB tree; `packages/winnie-schedule`
reads and edits it. Two follow-ups are deliberately open:

- [ ] `week create`: render a new week skeleton from the package instead of
      pointing at WLB `tools/weekly.py`. Only worth doing if the WLB generator
      goes away — two generators would drift, and the routine items in the
      skeleton are the owner's, not Winnie's.
- [ ] Edit continuation lines. `--text` rewrites the first line and `--note`
      appends, but a nested list or a fenced block still has to be edited by
      hand. A line-range selector would close that, at the cost of a second way
      to address an event.

- [ ] Create a week from the console. The page can edit any existing week but
      answers 404 for one that does not exist yet, because the skeleton is the
      WLB generator's job. If that generator is retired, this endpoint and the
      CLI would both need it.
