# winnie-local

The local execution layer for the Winnie personal assistant. It owns the only
code that may change files or run programs on the owner's machine.

- `Workspace` resolves every path under configured roots, refuses the
  assistant's own storage, credential directories, `.env`, private keys and
  `.git` writes, backs up overwritten files and writes an audit line.
- `CommandRunner` runs an argv array with a confined cwd, a timeout, capped
  output and a minimal environment that excludes Winnie and vendor keys. It is
  not an OS sandbox.
- `PendingActions` keeps one confirmation wait per conversation. An action can
  only execute in a later turn whose user message is an explicit confirmation.
- `policy.Whitelist` names the read-only operations that skip the LLM review.
- `review.LLMReviewer` asks a model to allow or deny everything else; failures,
  timeouts and malformed answers deny.
- `executor.LocalExecutor` composes those pieces and is the only entry point the
  assistant tools call.

The package does not import `winnie-assistant`; the host injects the review
completion function and the configured paths.
