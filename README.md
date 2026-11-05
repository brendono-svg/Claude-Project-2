# Claude-Project-2

## Claude Code setup: Sonnet builds, Haiku explores, Opus reviews

Works on a Claude Pro/Max subscription login - no API key or advisor feature needed.

| Piece | File | What it does |
| --- | --- | --- |
| Main model | `.claude/settings.json` | Session runs on Sonnet |
| Explorers | `.claude/agents/{search,grep,docs}.md` | Read-only Haiku subagents |
| Advisor | `.claude/agents/advisor.md` | Read-only Opus subagent, gets a short brief only |
| `/consult` | `.claude/skills/consult/SKILL.md` | Builds the brief and calls the advisor |
| Repeat-error guard | `.claude/hooks/repeat_error.py` | Zero-token hook: on the 2nd identical tool failure, tells Claude to stop and `/consult` |
| Rules | `CLAUDE.md` | When to consult, plus usage-saving habits |

Requires `python3` on your PATH for the hook. If Opus isn't available on your
plan, change `model: opus` in `advisor.md` to `sonnet`.

Alternative with no setup: `/model opusplan` uses Opus in plan mode and Sonnet
when executing.
