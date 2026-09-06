---
name: review-gate
description: Run the repo's review-gate controls without typing the script path. Use when the user invokes /review-gate or asks to arm/disarm, enable/disable, check status, browse review history, reset a rate-limit cooldown, or start an on-demand review of the two-stage code-review gate.
allowed-tools: Bash(scripts/review-gate:*), Bash(bash scripts/review-gate:*)
license: MIT
compatibility: Requires the review-gate managed scripts (scripts/review-gate) provisioned by openspec-codex-gate's bootstrap-repo.sh.
metadata:
  author: openspec-codex-gate
  version: "1.0"
---

Path-free front-end for the review gate's control script. When the user invokes
`/review-gate [subcommand] [args]`, run the repository-local control script and
relay what it prints.

## What to do

1. Run the control script from the repository root, passing the user's subcommand
   and arguments through **verbatim** (do not add, drop, reorder, or reinterpret
   flags):

   ```sh
   scripts/review-gate <subcommand> [args...]
   ```

   If `scripts/review-gate` is not present at the repo root, fall back to
   `bash scripts/review-gate <subcommand> [args...]`. The script resolves its own
   repo root from its location, so it works regardless of the current directory.

2. With **no subcommand** (a bare `/review-gate`), run `scripts/review-gate help`.

3. Relay the script's stdout and stderr to the user and preserve its exit code —
   report a non-zero exit rather than treating it as success. Do not summarize away
   findings or errors; show what the script said.

## Notes

- This skill is only a forwarder. `scripts/review-gate` is the single source of
  truth for the subcommands and their behavior — do not reimplement them here.
- Available subcommands (see `review-gate help` for the current list):
  - `start [change] [stage]` — run a review now (fresh; ignores the pass cache)
  - `start-auto-review [change]` / `stop-auto-review` — arm / disarm a workstream
  - `on` / `off` — enable / disable the gate
  - `status` — gate state, armed workstream, mode, latest result per stage + count
  - `history [index]` — browse retained review results (newest-first, or one in full)
  - `reset-cooldown` — reset rate-limit cooldowns (`resume` is a deprecated alias)
  - `help` — the useful-commands cheat sheet
- Arming also happens automatically via the `UserPromptSubmit` hook on
  `/openspec-apply-change`; this skill is for running the controls by hand.
