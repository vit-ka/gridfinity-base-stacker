
## Code review gate

This repo runs a Codex review gate as a Claude Code `Stop` hook: when an
OpenSpec change is in progress and the working tree is dirty, Codex reviews the
uncommitted changes and blocks the session on actionable defects
(`scripts/codex-gate-hook.sh` → `scripts/codex-gate.sh`). The scripts are kept
in sync from the shared checkout at
`${CODEX_GATE_HOME:-~/Projects/openspec-codex-gate}` on each run — edit them
there, not here.

**Working manually?** Disable the gate while you commit by hand:

```sh
scripts/codex-gate off      # disable until you turn it back on
scripts/codex-gate status   # check current state
scripts/codex-gate on       # re-enable
```

For a single stop only, prefer the one-shot bypass: `SKIP_CODEX_GATE=1`.
