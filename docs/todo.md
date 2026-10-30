# Winnie TODO

Prioritized follow-ups for the personal-assistant backend. Keep entries concrete
enough to start without re-reading the design.

## P0 — verify the Doubao path with a real account

- [ ] Smoke-test `VolcengineASRProvider` against a real recording and the
      account's current Doubao big-model endpoint. Confirm the submit/query
      paths, `X-Api-*` headers, `audio.format` values and status codes against
      the account console/docs. The adapter was written from the documented
      contract and is covered by mocked HTTP tests, not a live call.
- [ ] Record the first real meeting: upload, transcribe, translate, minutes,
      export. Fix provider payload or timestamp assumptions found there.
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

## P1 — real translation and summarization

- [ ] Implement an LLM `Translator` adapter that translates non-local segments
      in batches, preserves segment order and retries transient failures.
- [ ] Implement an LLM `Summarizer` adapter returning the existing
      `MinutesDraft`; keep prompts in `meetings/prompts/` with a version.
- [ ] Measure latency, token usage and cost on the bilingual fixture; write
      `usage_records` for each call.
- [ ] Decide whether translation uses the same model as summarization or a
      dedicated translation API.

## P1 — production data and jobs

- [ ] Add Alembic migrations and turn `database.auto_create_tables` off for
      production.
- [ ] Switch the default job backend to arq/Redis once a worker is deployed;
      keep `inline` for local demos and tests.
- [ ] Add a storage adapter for object storage (private bucket, short-lived
      URLs) while keeping local disk as the default.
- [ ] Define audio retention and deletion: configurable retention window,
      purge audio on meeting delete, keep minutes/notes if desired.
- [ ] Add a session cleanup job for expired `auth_sessions`.

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
