# Winnie TODO

Prioritized follow-ups for the personal-assistant backend. Keep entries concrete
enough to start without re-reading the design.

## P0 — verify the Doubao path with a real account

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
- [ ] Give segments a language before relying on translation. The 2.0 response
      has no per-utterance language, and its file-level LID
      (`request.enable_lid` → `result.additions.lid_lang`) mislabels a Mandarin
      TTS clip as `speech_dia_nan`, so it is not used. With `language=None`,
      `_process` marks every segment `translation_status="skipped"` and a
      bilingual meeting is never translated. Implement per-segment detection
      (script-based for zh/en, or a real `LanguageDetector`) and record the
      choice.
- [ ] Record the first real meeting: upload, transcribe, translate, minutes,
      export. Fix provider payload or timestamp assumptions found there.
- [ ] Smoke-test the internal LLM path with a real `WINNIE_LLM_API_KEY`:
      translation order/length, structured minutes, token usage and failure
      messages. Mocked tests do not prove prompt quality.
- [ ] Add a 10–30 minute bilingual fixture (Chinese, English, code-switching,
      names, numbers) under a local-only fixture directory, never in Git.

## P1 — ASR vendor evaluation (after Doubao works)

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
- [ ] Add retry/backoff for transient provider failures; the current adapters
      fail the meeting stage immediately.
- [ ] Measure latency, token usage and quality on the bilingual fixture; write
      `usage_records` for each call and compare with the fake baseline.
- [ ] Decide whether translation uses the same model as summarization or a
      dedicated translation API.
- [ ] Add chunking/map-reduce for transcripts that exceed one model context;
      the current summarizer sends the whole transcript in one request.
- [ ] Move prompts into versioned files (`meetings/prompts/`) once the wording
      stabilizes; `prompt_version` is already stored with minutes.

## P1 — production data and jobs

- [x] Add Alembic migrations for the initial schema; `alembic check` is clean
      and the test suite verifies upgrade, downgrade and startup without
      `auto_create_tables`.
- [ ] Rehearse `alembic upgrade head` on a populated PostgreSQL copy, then set
      `database.auto_create_tables: false` in the production config.
- [ ] Switch the default job backend to arq/Redis once a worker is deployed;
      keep `inline` for local demos and tests.
- [ ] Add a storage adapter for object storage (private bucket, short-lived
      URLs) while keeping local disk as the default.
- [x] Define audio retention: `audio_retention_days` purges only audio for
      terminal meetings, the meeting API already purges on delete, and the arq
      cron plus `python -m winnie_api.meetings.cleanup_audio` cover deployments
      without a worker.
- [x] Add a session cleanup job for expired `auth_sessions`; the arq worker
      schedules it daily and a standalone CLI is available for cron.

## P2 — accounts and security

- [ ] Add login rate limiting and a small lockout/backoff policy.
- [ ] Add password change and password reset (email or one-time local CLI).
- [ ] Add account management/admin endpoints only when multi-user deployment
      is real; keep single-user registration closed by default.
- [ ] Review token storage on Android (`EncryptedSharedPreferences`) and
      certificate pinning only if the deployment requires it.

## P2 — clients and realtime

- [ ] Android client: record, upload, meeting list, transcript view, minutes,
      export, offline cache.
- [ ] Realtime WebSocket ASR and incremental translation, reusing the final
      file-first pipeline for the finished recording.
- [ ] Speaker diarization and per-speaker labels in transcript/minutes.
- [ ] Cross-meeting semantic search over knowledge notes (not raw transcripts).

## P3 — operational polish

- [ ] Per-meeting and per-month cost view built from `usage_records`.
- [ ] Structured logs for `meeting_id`, `job_id`, stage and provider latency.
- [ ] Health/readiness checks that include database and queue connectivity.
- [ ] Deployment guide: PostgreSQL, Redis, reverse proxy, backups, restore
      rehearsal.
- [ ] Add a `docs/decisions.md` entry for every provider or architecture choice
      that is not obvious from the code.
