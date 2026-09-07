
<!-- review-gate-docs:start -->
## Code review gate

This repo runs a two-stage review gate as a Claude Code `Stop` hook. It runs
**only while armed** for a workstream (so it stays out of the way on ordinary
edits). When armed, the gate reviews the active OpenSpec change whether the work
is committed or not: it first runs a cheap **Claude (Sonnet)** review that
verifies the change and blocks the session on actionable defects; once that is
clean it runs the expensive final reviewer — **Codex**, or **Claude Opus** only
when Codex is rate-limited (`scripts/review-gate-hook.sh` → `scripts/review-gate.sh`).
A clean final pass is cached against the change **content** — a git tree hash of
the working state, committed and uncommitted alike — so committing already-reviewed
work does not trigger a re-review and an unchanged change returns instantly from
cache. The scripts are kept in sync from the shared checkout at
`${REVIEW_GATE_HOME:-~/Projects/openspec-codex-gate}` on each run — edit them
there, not here. The hooks are wired via `$CLAUDE_PROJECT_DIR` so they resolve from
any working directory; a change to how the hooks are wired reaches this repo only on
a re-bootstrap (the `.claude/settings.json` wiring is not auto-synced, unlike the
scripts).

**Starting work?** Arm the gate for your change (the `/openspec-apply-change`
flow does this for you):

```sh
scripts/review-gate start-auto-review [change]   # arm (defaults to the most recent change)
scripts/review-gate stop-auto-review             # disarm (end the workstream)
```

Arming is scoped to implementation work: the `UserPromptSubmit` hook arms on
`/openspec-apply-change` and **disarms** on every other OpenSpec workflow command
(`/openspec-explore`, `/openspec-propose`, `/openspec-archive-change`,
`/openspec-update-change`, `/openspec-sync-specs`), so exploring, planning, or
finalizing does not trigger reviews. A plain prompt leaves the armed state
unchanged. Re-run `/openspec-apply-change` to re-arm after a detour.

**Prefer a skill?** These controls are also a Claude Code skill, so you can run
them without the `scripts/` path: type `/review-gate <subcommand>` (for example
`/review-gate status` or `/review-gate reset-cooldown`) and it forwards to
`scripts/review-gate`; a bare `/review-gate` prints this cheat sheet.

**Want to review right now?** Run a fresh review on demand (it ignores the pass
cache), without waiting for a stop:

```sh
scripts/review-gate start              # full round now (cheap Sonnet → final reviewer)
scripts/review-gate start 2            # only the final reviewer (skip the cheap stage)
scripts/review-gate start <change>     # review a specific change (even if archived)
```

`start` targets the armed change (or the most recent one) unless you name a
change; a named change is a one-shot that does not change the armed workstream.

**Review history.** Every stage verdict is appended to a **committable** store at
`.review-gate-history/` (one Markdown file per verdict: change, stage, verdict,
timestamp, and the rendered findings). It is deliberately NOT git-ignored, so it
is committed as part of the repo record, and it is excluded from the content
fingerprint — appending or committing a verdict never triggers a re-review. Browse
it:

```sh
scripts/review-gate status              # ...now also shows total retained + latest result per stage
scripts/review-gate history             # list retained results newest-first
scripts/review-gate history <index>     # print the full report for one result
```

**Codex usage limit?** The gate does not stall: it falls back to **Claude Opus**
as the final reviewer and re-probes Codex automatically once the limit resets.
Force a retry sooner with `scripts/review-gate reset-cooldown` (which resets all
rate-limit cooldowns; `resume` remains as a deprecated alias).

**Working manually?** Disable the gate while you commit by hand:

```sh
scripts/review-gate off      # disable until you turn it back on
scripts/review-gate status   # gate state + current review mode
scripts/review-gate on       # re-enable
```

For a single stop only, prefer the one-shot bypass: `SKIP_REVIEW_GATE=1`.
<!-- review-gate-docs:end -->
