# winnie-llm

The single module through which Winnie calls a model vendor.

Every chat completion, tool-calling turn, recording-file transcription and live
caption session is built here. A capability package receives a client or a small
protocol; it never reads a key, names a key variable, or sends a vendor request
itself.

## What it owns

| Concern | Module |
|---|---|
| Supplier / model / feature bindings, and the stored supplier keys | `winnie_llm.registry` |
| The env backup the console mirrors keys into | `winnie_llm.keys` |
| OpenAI-compatible chat and tool calls | `winnie_llm.chat` |
| Recording-file ASR and streaming captions | `winnie_llm.asr` |
| Shared model limits | `winnie_llm.runtime` |

The registry persists the supplier and feature-binding state that the admin
console edits (`data/ai.yaml` by default). It selects a supplier model per
feature, reads the supplier key stored with that model's supplier, and builds
the matching client.

## Credentials

A supplier key is stored on its supplier record in the registry and written by
the admin console. `winnie_llm.keys` keeps the backup copy in `.env`
(gitignored, mode `0600`) under the variable the supplier id determines:

| Supplier | Backup variable |
|---|---|
| `deepseek` | `DEEPSEEK_API_KEY` |
| `openai` | `OPENAI_API_KEY` |
| `anthropic` | `ANTHROPIC_API_KEY` |
| `qwen` | `DASHSCOPE_API_KEY` |
| `volcengine` | `VOLCENGINE_ASR_API_KEY` |
| any other id | `<SUPPLIER>_API_KEY` |

The registry wins: editing only `.env` does not change a key the app uses. The
file is still read in two cases — a key that predates version 7 is imported
once, and a gateway-only deployment (`ai.enabled: false`) has no registry. A
process-environment variable of the same name overrides the file for that
gateway path, which keeps one-off runs and CI possible; internal calls ignore
it. `WINNIE_LLM_ENV_FILE` points the backup at a different file, and the API
tests use it to stay isolated from the checkout's real keys.

A key change needs no restart: the registry is re-read when its modification
time changes, and the console refreshes the live gateway table at the same time.
