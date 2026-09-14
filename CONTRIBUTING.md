

<!-- change-loop-docs:start -->
## Change loop

This repo runs plan→plan-review→code→code-review through the host-invoked
`$change` loop (no hooks). Coordinate one change with per-role provider choice:

```sh
scripts/change-loop init <change> <plan-writer> <plan-reviewer> <coder> <code-reviewer>
scripts/change-loop persist-input <change> --prose "..."
scripts/change-loop run-plan [--model M] [--effort E] [--fresh] <change>
scripts/change-loop run-plan-review [--model M] [--effort E] [--fresh] <change>
scripts/change-loop run-code [--model M] [--effort E] [--fresh] <change>
scripts/change-loop run-code-review [--model M] [--effort E] [--fresh] <change>
scripts/change-loop status <change>
```

Each role takes `codex | claude | muse | grok | gemini`, optionally with an
explicit model (`provider:model`, for example
`scripts/change-loop init abc-test muse:muse-spark-1.3-contributor codex:gpt-6-astra muse:muse-spark-1.3-contributor codex:gpt-6-astra`).
The description is accompanying prose, never a positional argument.
`grok`/`gemini` are declared-only until their CLI contracts are verified —
selecting one fails fast instead of invoking. Only selected providers run:
findings, errors, limits, and exhaustion never fall back to another
provider. Every invocation needs an explicit model (init mapping or
`--model`); a missing model refuses before invocation — never a silent
default or fallback. Every invocation also carries an explicit reasoning
effort (reviewers `medium`, authors `high`, each overridable via
`--effort`; never an ambient CLI default), and the helper prints the
selected provider/model/effort plus one token-usage line per invocation.
Matching change/role/provider/model/effort resumes its dedicated session
(`--fresh` deliberately mints a new one); a resume the CLI cannot find
records an actionable incomplete handoff with the stored session
preserved — never a silent fresh session, never a fallback provider.

One cumulative allowance of five blocking (P0–P2) verdicts is shared across
plan and code review; the fifth stops the loop with no sixth invocation.
Clean results, errors, and limits consume nothing. Verdicts append to the
committable `.change-loop-history/` store; transient state lives under
`.change-loop/` (git-ignored). Per-invocation token usage appends to a
per-change usage log with cumulative totals, kept apart from the budget. A
step succeeds only with a clean reviewer verdict against current content —
helper exit zero alone is never proof. An error/limit handoff parks the
in-flight action: only it may run next. Only explicit user authorization
for `scripts/change-loop reset-rounds <change> --confirm` permits a fresh
budget.

All OpenSpec artifact work goes through the `openspec` CLI (`new`, `status`,
`instructions`, `validate --strict`, `archive`); helpers never scaffold change
directories or embed artifact templates. Run
`scripts/sync-change-loop.sh` to refresh managed copies from the canonical
checkout (explicit host invocation only, never from hooks).
<!-- change-loop-docs:end -->
