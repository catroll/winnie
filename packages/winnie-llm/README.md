# winnie-llm

The single module through which Winnie calls a model vendor.

Every chat completion, tool-calling turn, recording-file transcription and live
caption session is built here, and every vendor API key comes from this
package's `.env`. A capability package receives a client or a small protocol; it
never reads a key, names a key variable, or sends a vendor request itself.

## What it owns

| Concern | Module |
|---|---|
| Credentials and `.env` loading | `winnie_llm.keys` |
| OpenAI-compatible chat and tool calls | `winnie_llm.chat` |
| Recording-file ASR and streaming captions | `winnie_llm.asr` |
| Supplier / model / feature bindings | `winnie_llm.registry` |
| Shared model limits | `winnie_llm.runtime` |

The registry persists the supplier and feature-binding state that the admin
console edits (`data/ai.yaml` by default). It selects a supplier model per
feature and builds the matching client; it stores no secret.

## Credentials

Copy `.env.example` to `.env` (gitignored, mode `0600`) and fill in the keys you
use. The supplier id determines the variable:

| Supplier | Variable |
|---|---|
| `deepseek` | `DEEPSEEK_API_KEY` |
| `openai` | `OPENAI_API_KEY` |
| `anthropic` | `ANTHROPIC_API_KEY` |
| `qwen` | `DASHSCOPE_API_KEY` |
| `volcengine` | `VOLCENGINE_ASR_API_KEY` |
| any other id | `<SUPPLIER>_API_KEY` |

A process-environment variable of the same name overrides the file, which keeps
one-off runs and CI possible without a second managed location. `WINNIE_LLM_ENV_FILE`
points the loader at a different file; the API tests use it to stay isolated from
the checkout's real keys.

Restart the API after editing `.env`: a running process does not re-read it.
