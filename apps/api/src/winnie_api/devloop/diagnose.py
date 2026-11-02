"""Read-only diagnosis: one bounded evidence bundle, one model call, one answer.

The prompt is deliberately narrower than "fix this". A diagnosis names the most
likely origin, the cause in one sentence and the next read-only inspection step.
Anything that writes, restarts or deletes is out of scope here — that is the
repair phase, which is a separate, gated action.
"""

from __future__ import annotations

import json
import re
from typing import Any

SYSTEM_PROMPT = """You are a read-only diagnostician for Winnie, a self-hosted \
personal assistant. The backend is Python: FastAPI in `apps/api`, capability \
packages in `packages/`, tests beside them. The console is React in `apps/admin`.

You receive one error incident and the evidence collected around it: the \
incident itself, the repository state, recent log tails, and the health of the \
configured services.

Rules:
- Work only from the evidence. Do not name a component, file or cause that the \
evidence does not support.
- This is diagnosis only. Do not propose code changes, restarts or deletions in \
`next_steps`; propose read-only commands and inspections.
- Prefer the innermost application frame over a framework frame.
- Answer in English.

Answer with one JSON object and nothing else:
{"component": "assistant | gateway | mail | index | console | host | unknown",
 "file": "path/from/repo/root.py",
 "line": 0,
 "cause": "one or two sentences naming the mechanism, not the symptom",
 "confidence": 0.0,
 "next_steps": ["a read-only command or inspection", "at most three"],
 "notes": "what would change this diagnosis, or an empty string"}

Use "" and 0 when the evidence does not identify a file or a line. Confidence \
is between 0 and 1. Do not wrap the object in prose or a code fence."""


def build_messages(evidence: dict[str, Any]) -> list[dict[str, str]]:
    """The exact messages sent to the model, kept testable."""
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": "Evidence:\n" + json.dumps(evidence, ensure_ascii=False, default=str),
        },
    ]


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)
_OBJECT = re.compile(r"\{.*\}", re.DOTALL)


def parse_answer(text: str) -> dict[str, Any] | None:
    """Parse the model's JSON, tolerating a fence or surrounding prose.

    Returns ``None`` when no object can be recovered; the caller keeps the raw
    text so a bad answer is still visible to the owner instead of vanishing.
    """
    if not text:
        return None
    cleaned = _FENCE.sub("", text).strip()
    candidates = [cleaned]
    match = _OBJECT.search(cleaned)
    if match is not None and match.group(0) != cleaned:
        candidates.append(match.group(0))
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return _normalise(parsed)
    return None


def _normalise(parsed: dict[str, Any]) -> dict[str, Any]:
    try:
        confidence = float(parsed.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    try:
        line = int(parsed.get("line") or 0)
    except (TypeError, ValueError):
        line = 0
    steps = parsed.get("next_steps")
    if isinstance(steps, str):
        steps = [steps]
    if not isinstance(steps, list):
        steps = []
    return {
        "component": str(parsed.get("component") or "unknown")[:80],
        "file": str(parsed.get("file") or "")[:400],
        "line": max(0, line),
        "cause": str(parsed.get("cause") or "")[:2000],
        "confidence": min(1.0, max(0.0, confidence)),
        "next_steps": [str(step)[:400] for step in steps[:3]],
        "notes": str(parsed.get("notes") or "")[:1000],
    }
