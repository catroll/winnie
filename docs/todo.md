# Winnie TODO

Prioritized follow-ups for the personal-assistant backend. Keep entries concrete
enough to start without re-reading the design. The milestone view and completion
status are in [plan.md](./plan.md).

## P0 — first real meeting and bilingual fixture

> `VOLCENGINE_ASR_API_KEY` is configured in the gitignored `apps/api/.env`
> for this checkout. Restart the API after changing it (`make restart`):
> `uvicorn --reload` watches Python files, not the environment file.

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
- [x] Smoke-test the internal LLM path with a real `WINNIE_LLM_API_KEY`:
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

## P2 — accounts and security

- [x] Add login rate limiting: failures are counted per username, the account
      is locked temporarily after the configured threshold, and login returns
      `429` with `Retry-After`.
- [x] Add password change; the current password is required and every session
      is revoked.
- [x] Add password reset from the host: `python -m winnie_api.cli.reset_password`
      prompts or generates a password and revokes every session.
- [ ] Add email-based password reset if multi-user deployment needs it.
- [ ] Add account management/admin endpoints only when multi-user deployment
      is real; keep single-user registration closed by default.
- [x] Review token storage on Android: the bearer token is encrypted with an
      AES/GCM key held in the Android Keystore, not in plain preferences.
      Certificate pinning is not needed for the local MVP; revisit when the
      server is public.

## P2 — clients and realtime

- [x] Android client MVP in `apps/android`: sign in, record a 16 kHz mono WAV,
      upload, request transcription, meeting list, transcript and minutes views.
      Verified against the local backend with fake providers, including a
      JVM integration test that runs the same HTTP client the app uses.
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

- [x] Admin CRUD for integrations and feature bindings, with gateway routes
      derived from the registry. Changing a provider or binding applies on the
      next model call; the arq worker re-reads the file by mtime.
- [x] Seed the registry from `config.yaml` on first start, validate kind/model
      compatibility when a binding is saved, and fall back to the YAML value for
      unbound features. Covered by `apps/api/tests/test_ai_admin.py` and a
      Firefox run of create → bind → delete.
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

## P3 — operational polish

- [x] Per-month LLM cost view in the console (`/usage`), built from the new
      usage ledger; it prefers provider-reported costs and estimates from
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
