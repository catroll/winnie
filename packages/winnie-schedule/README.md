# winnie-schedule

**Read and edit the weekly schedule that lives as Markdown in the WLB `work/` tree.**

The schedule is one file per week (`work/2026/Q4/10/202610-05-11.md`), one
`### MM/DD 周X` section per day, one checkbox per event. That is a good format
for a human and a poor one for tooling: a question like "what is left this week"
or an edit like "move Thursday's review to Friday" means walking headings and
rebuilding prefixes by hand.

`winnie-schedule` closes that gap without taking ownership of the files. It
parses a week into days, events, tags and times, and it edits a file by splicing
the affected lines: everything it did not change stays byte-identical. The
Markdown stays the source of truth, and a hand edit and a tool edit can coexist.

## What it does

| Command | Result |
| --- | --- |
| `show <date>` | the week, or one day, as a readable list |
| `parse <date>` | the whole week as JSON: days, events, tags, times, links, warnings |
| `list <date> --pending` | only what is not done |
| `add <date> "text"` | a new event in that day's section, optionally timed and tagged |
| `update <id>` | change the text, time, tags or checkbox state, or move it to another day |
| `delete <id>` | remove the event and the lines that belong to it |
| `weeks` | every week file, with its range and layout |
| `skill install` | copy `SKILL.md` into `~/.agents/skills/winnie-schedule` |

Every edit accepts `--dry-run`, which prints a unified diff and writes nothing.

## Quick start

The package is standard-library only, so it runs on the system interpreter.

```bash
WS=~/Projects/Mine/winnie/packages/winnie-schedule
sc() { PYTHONPATH="$WS/src" python3 -m winnie_schedule "$@"; }

sc list today --pending
sc add 2026-10-07 "评审邮件模板" --time 14:00~15:00 --weekday
sc update 202610-05-11#10/05#2 --done
sc delete --week 2026-10-05 --match "临时事项" --dry-run
```

Or through the repository: `make schedule ARGS="list today"`.

The tree is found at `~/Projects/Mine/WLB`; override it with `--root` or
`WINNIE_SCHEDULE_ROOT`.

## Boundaries

- **It does not create week files.** The skeleton (routine items, calendar
  block, badge definitions) belongs to the WLB generator `tools/weekly.py`,
  which fills in the current and next three weeks. Adding an event to a week
  that does not exist yet fails with the expected path instead of inventing one.
- **Files without `### MM/DD` day sections are read-only.** That is every week
  before 2024: they parse, they are listed, they are never rewritten.
- **It never re-serialises a file.** The parser keeps the source lines and the
  model is a view with line ranges, so an edit can only touch the lines it
  means to touch.
- **It reports, it does not repair.** A day heading that disagrees with its date,
  or an event whose `MM/DD` tag contradicts its section, comes back as a warning
  in `parse --json`. Silently fixing those would hide a human mistake.

## Documentation

- [`SKILL.md`](SKILL.md) — the format and the editing rules, written for an
  agent that has to touch the schedule (Chinese, like the schedule itself).
- [`docs/decisions.md`](../../docs/decisions.md) — why the editor splices lines
  instead of rendering the model, and why week creation stayed in WLB.

## Tests

```bash
make schedule-test
```

The suite covers layout arithmetic, the parser's grammar, every mutation, the
CLI's exit codes, and — when the WLB checkout is present — a read-only pass over
every week file in it: all of them must parse, and parsing then rendering must
return the original bytes.
