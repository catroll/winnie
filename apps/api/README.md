# Winnie API

FastAPI backend for Winnie. It currently ships the transparent LLM gateway;
meeting and knowledge APIs are added incrementally.

- Application entry and composition root: `winnie_api.main:create_app`
- Gateway feature: `src/winnie_api/gateway/`
- Settings: `config.yaml`; local overlay `config.local.yaml` (gitignored)
- Tests: `tests/`, using a fake upstream instead of real vendor keys

## Run

```bash
# Edit config.yaml as needed.
uv sync
uv run uvicorn winnie_api.main:app --reload --host 127.0.0.1 --port 8000
curl -s http://127.0.0.1:8000/routes
```

## Test

```bash
uv run pytest
```

## Smoke test

The same `DEEPSEEK_API_KEY` exercises both protocol routes:

| Route | Protocol | Path |
|-------|----------|------|
| `deepseek-openai` | openai | `/deepseek-openai/v1/chat/completions` |
| `deepseek-anthropic` | anthropic | `/deepseek-anthropic/v1/messages` |

```bash
# scripts/.env contains DEEPSEEK_API_KEY
uv run python scripts/smoke_test.py
uv run python scripts/smoke_test.py --only openai
uv run python scripts/smoke_test.py --only anthropic
```
