# Winnie API — OpenAI 透明代理

实现说明见仓库根目录 [docs/openai-proxy.md](../../docs/openai-proxy.md)。

## 运行

```bash
cp .env.example .env
uv sync
uv run uvicorn winnie_api.main:app --reload --host 127.0.0.1 --port 8000
```
