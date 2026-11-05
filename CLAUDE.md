# Claude Code working rules

## Roles
- Main session (Sonnet): plans, edits code, runs tests.
- `search`, `grep`, `docs` subagents (Haiku, read-only): use these instead of
  broad file sweeps or long doc reads in the main session. Ask for short answers.
- `advisor` subagent (Opus, read-only): only via `/consult`, only at checkpoints.

## Checkpoints - run /consult at exactly these moments
- Before locking in a plan for a non-trivial change (more than ~2 files or any
  design decision). Skip for small, obvious edits.
- After the same error happens twice (a hook will also remind you). Do not
  try a third variation of the same fix first.
- Before declaring a multi-step task done.
Do not consult the advisor at any other time.

## Keep usage low
- Read only the parts of files you need; prefer Grep over reading whole files.
- Don't re-read files you've already read this session unless they changed.
- Keep subagent and advisor prompts compact; give paths, not pasted file contents.
- Be concise in replies; don't restate plans or diffs the user has already seen.
