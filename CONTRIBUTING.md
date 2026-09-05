
## Code review gate

This repo runs a two-stage review gate as a Claude Code `Stop` hook: when an
OpenSpec change is in progress and the working tree is dirty, the gate first
runs a cheap **Claude** review over the uncommitted changes and blocks the
session on actionable defects; once Claude is clean it runs the costly
**Codex** full review (`scripts/codex-gate-hook.sh` → `scripts/codex-gate.sh`).
A clean Codex pass is cached against the diff, so Codex is not re-run until the
changes move on. The scripts are kept in sync from the shared checkout at
`${CODEX_GATE_HOME:-~/Projects/openspec-codex-gate}` on each run — edit them
there, not here.

**Codex usage limit?** When Codex reports its subscription/usage limit the gate
does not stall: it announces a **Claude-only mode** (loud banner + macOS
notification), keeps gating on the Claude stage alone, and re-enables Codex
automatically once the limit resets. Force it back sooner with
`scripts/codex-gate resume`.

**Working manually?** Disable the gate while you commit by hand:

```sh
scripts/codex-gate off      # disable until you turn it back on
scripts/codex-gate status   # gate state + current review mode
scripts/codex-gate on       # re-enable
scripts/codex-gate resume   # leave Claude-only mode now
```

For a single stop only, prefer the one-shot bypass: `SKIP_CODEX_GATE=1`.
