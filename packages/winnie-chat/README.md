# winnie-chat

The chat engine behind the Winnie assistant. It owns the model loop, the skill
registry and the system-prompt assembly. It is not a capability package and
does not know how conversations are stored, where memory lives or how commands
are executed.

- `SkillManifest`/`Skill` describe one bundle of tools, prompt instructions and
  permissions. The assistant registers web, document-index, memory, task and
  local-execution skills.
- `SkillRegistry` persists which skills are enabled, filters unavailable and
  scheduled-unsafe skills, and resolves a tool name to its owning skill.
- `ChatEngine` runs the bounded model/tool loop. A tool call only reaches a
  skill that is enabled for the turn; a disabled skill is not in the schemas
  and its tools are rejected if the model hallucinates one.
- `prompt.build_system_prompt` combines the persona, time, memory digest,
  pending confirmation and the instructions of the enabled skills.

The assistant composes this engine with its conversation store, memory,
scheduler and `winnie-local` executor. The admin skills page reads the same
registry through the assistant API.
