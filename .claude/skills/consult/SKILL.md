---
name: consult
description: Get a second opinion from the Opus advisor subagent at a checkpoint - before locking a plan, after the same error twice, or before declaring a task done. Use only at those three moments.
---
Consult the `advisor` subagent. It cannot see this conversation, so write it a
compact brief (aim for under 250 words) instead of pasting history:

1. **Checkpoint**: plan / repeated error / done-check
2. **Goal**: one sentence
3. **Current approach or plan**: bullets
4. **Evidence**: the exact error text, or the test/command output that shows status
5. **Files**: the 1-5 paths the advisor should read (not the whole repo).
   For a done-check, list every changed file instead (`git diff --name-only`).
   For a done-check, Evidence must also include the end-to-end run of any outside
   service the change touches, or state plainly that it wasn't run and why.
6. **Question**: the one decision you want judged

Then:
- Delegate to the `advisor` subagent with that brief.
- Apply its advice yourself; summarize to the user in 1-2 lines what changed.
- If its verdict is `stop`, pause and tell the user why before doing more.

$ARGUMENTS
