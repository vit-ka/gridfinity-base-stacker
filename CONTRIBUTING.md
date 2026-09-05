
## Code review gate

This repo runs a two-stage review gate as a Claude Code `Stop` hook. It runs
**only while armed** for a workstream (so it stays out of the way on ordinary
edits). When armed and the working tree is dirty, the gate first runs a cheap
**Claude** review of the change and blocks the session on actionable defects;
once Claude is clean it runs the costly **Codex** full review
(`scripts/review-gate-hook.sh` → `scripts/review-gate.sh`). A clean Codex pass is
cached against the diff, so Codex is not re-run until the changes move on. The
scripts are kept in sync from the shared checkout at
`${REVIEW_GATE_HOME:-~/Projects/openspec-codex-gate}` on each run — edit them
there, not here.

**Starting work?** Arm the gate for your change (the `/openspec-apply-change`
flow does this for you):

```sh
scripts/review-gate start [change]   # arm (defaults to the most recent change)
scripts/review-gate stop             # disarm (end the workstream)
```

**Codex usage limit?** When Codex reports its subscription/usage limit the gate
does not stall: it announces a **Claude-only mode** (loud banner + macOS
notification), keeps gating on the Claude stage alone, and re-enables Codex
automatically once the limit resets. Force it back sooner with
`scripts/review-gate resume`.

**Working manually?** Disable the gate while you commit by hand:

```sh
scripts/review-gate off      # disable until you turn it back on
scripts/review-gate status   # gate state + current review mode
scripts/review-gate on       # re-enable
scripts/review-gate resume   # leave Claude-only mode now
```

For a single stop only, prefer the one-shot bypass: `SKIP_REVIEW_GATE=1`.
