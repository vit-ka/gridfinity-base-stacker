
<!-- review-gate-docs:start -->
## Code review gate

This repo runs a two-stage review gate as a Claude Code `Stop` hook. It runs
**only while armed** for a workstream (so it stays out of the way on ordinary
edits). When armed and the working tree is dirty, the gate first runs a cheap
**Claude (Sonnet)** review that verifies the active OpenSpec change and blocks
the session on actionable defects; once that is clean it runs the expensive final
reviewer — **Codex**, or **Claude Opus** only when Codex is rate-limited
(`scripts/review-gate-hook.sh` → `scripts/review-gate.sh`). A clean final pass is
cached against the diff. The scripts are kept in sync from the shared checkout at
`${REVIEW_GATE_HOME:-~/Projects/openspec-codex-gate}` on each run — edit them
there, not here.

**Starting work?** Arm the gate for your change (the `/openspec-apply-change`
flow does this for you):

```sh
scripts/review-gate start [change]   # arm (defaults to the most recent change)
scripts/review-gate stop             # disarm (end the workstream)
```

**Codex usage limit?** The gate does not stall: it falls back to **Claude Opus**
as the final reviewer and re-probes Codex automatically once the limit resets.
Force a retry sooner with `scripts/review-gate resume`.

**Working manually?** Disable the gate while you commit by hand:

```sh
scripts/review-gate off      # disable until you turn it back on
scripts/review-gate status   # gate state + current review mode
scripts/review-gate on       # re-enable
```

For a single stop only, prefer the one-shot bypass: `SKIP_REVIEW_GATE=1`.
<!-- review-gate-docs:end -->
