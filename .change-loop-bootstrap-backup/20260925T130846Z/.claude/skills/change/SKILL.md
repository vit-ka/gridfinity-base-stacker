---
name: change
description: Run the host-coordinated plan, plan-review, code, code-review loop for one OpenSpec change with per-role provider choice. Use when the user invokes dollar-change with a change name and four role providers.
---

# $change — host-coordinated change loop

<!-- change-loop-managed-file -->

You are the coordinating host. You NEVER write plan content, emit review
verdicts, write implementation code, or apply fixes yourself. Every role step
runs by invoking an explicit helper action below in the named provider's
session. Do not arm the old review gate and do not install or rely on
`Stop`/`UserPromptSubmit` hooks.

## 1. Parse the invocation

Expected form — exactly five positional arguments:

```
$change <change-name> <plan-writer> <plan-reviewer> <coder> <code-reviewer>
```

Each role takes `codex | claude | muse | grok | gemini`, optionally with an
explicit model: `provider` or `provider:model`.
Example: `$change abc-test muse:muse-spark-1.3-contributor codex:gpt-6-astra muse:muse-spark-1.3-contributor codex:gpt-6-astra`.

- Wrong arity (fewer than five, or a sixth positional argument), a malformed
  change name, or an unknown provider: print the syntax and the provider list,
  create no state, invoke nothing. A sixth positional is NEVER a description.
- A well-formed name that does not exist yet is NOT an error: scaffold it with
  `scripts/change-loop init ...` (which uses `openspec new` via the CLI).
- Every role invocation resolves its model per invocation: a `--model`
  override, then the recorded init mapping, then the pre-configured
  provider default (`CHANGE_LOOP_DEFAULT_MODEL_<PROVIDER>` env override
  first, then the built-in value: `muse` → `muse-spark-1.3`,
  `claude` → `claude-fable-5-1`, `codex` → `gpt-6-astra`;
  `grok`/`gemini` have no default). A missing model refuses BEFORE any
  provider runs: the loop never falls back to another provider or a
  CLI-built-in default. Only explicit models are recorded — a
  provider-default resolution is re-resolved every run and never
  persisted. Every invocation also carries an explicit reasoning effort
  — all four roles default to `medium`. Resolution is per-action
  `--effort`, then saved role effort, then the `medium` default
  (wired as `muse --reasoning-effort`,
  `codex -c model_reasoning_effort=`, `claude --effort`; never an ambient
  CLI default). The helper prints the selected provider/model/source
  (explicit vs. provider default) before each invocation.

Validate by running (it enforces all of the above and records the roles):

```sh
scripts/change-loop init <change-name> <plan-writer> <plan-reviewer> <coder> <code-reviewer>
```

## 2. Establish the description (never invent it)

The description is the accompanying prose in the same user message and/or
prior agreed exploration context, and/or the change's own existing planning
artifacts — never a positional argument. An existing change needs no prose:
`init` and `check-input` automatically record its CLI-resolved planning
artifacts as the input context (reporting that source), so proceed straight
to `run-plan` with no question asked and no scope invented from the change
name.

Persist accompanying prose faithfully (no plan authoring) before the first
plan-writer run — it layers on top of the recorded artifacts:

```sh
scripts/change-loop persist-input <change-name> --prose "..." \
  [--decisions-file PATH]... [--artifact PATH]...
scripts/change-loop check-input <change-name>   # missing input: ask first, invoke nothing
```

Ask what to build BEFORE invoking any provider — never invent scope from
the change name — ONLY when no usable input exists at all: a freshly
scaffolded change (created by `init` because it did not exist yet) with no
prose or exploration context, or an existing change whose artifacts are all
empty or absent.

Later prose or newly agreed exploration decisions supersede the stored input
for subsequent plan-writer rounds (re-run `persist-input`). A substantive
update after plan approval routes the next step back to the plan-writer and
invalidates dependent approvals while keeping sessions and consumed rounds;
an identical re-persist changes nothing.

## 3. Run the sequence

Drive every step with the helper in the selected provider's session, in order.
`grok`/`gemini` are declared-only until verified: selecting one fails fast
with remediation instead of invoking. Never substitute another provider on
findings, errors, limits, or exhaustion — record the incomplete handoff.

```sh
scripts/change-loop run-plan <change-name>            # plan-writer drafts via CLI-resolved templates
scripts/change-loop run-plan-review <change-name>     # plan-reviewer verdict (plan budget: 5)
scripts/change-loop run-code <change-name>            # coder implements the approved plan
scripts/change-loop run-code-review <change-name>     # code-reviewer verdict (code budget: 5)
scripts/change-loop status <change-name>              # explicit status (exit zero is never proof of clean)
scripts/change-loop history --plain <change-name>     # append-only verdict history
```

Every role invocation rebuilds fresh, complete CLI-resolved change context
in the selected store — status plus artifact instructions with full file
contents for planning roles, status plus apply instructions (context files,
tasks, progress, project context, guidance) for implementation roles —
rebuilt for fresh, retry, and resumed sessions alike. Persisted prose and
accepted exploration decisions accompany the live artifacts for every role;
paths alone, summaries, and session memory never substitute for current
contents. The coder follows the `openspec-apply-change` skill in the
selected scope and marks work complete only when fully implemented; a
missing skill is an explicit handoff, never a reduced workflow. Reviewers
stay read-only while receiving equivalent host-resolved criteria, and
context-resolution failures park the in-flight action instead of invoking
any provider on partial context.

Each `run-*` action also accepts `[--model M] [--effort E] [--fresh]`:
`--model` overrides the recorded model for that role (and records it),
`--effort` overrides the role effort for that invocation (and records it),
`--fresh` deliberately mints a new session instead of resuming the stored
one. A changed provider, model, or effort starts a fresh session.

Plan review and code review use separate, dedicated sessions; matching
identity (same change, role, provider, model, effort) resumes its session
across rounds — muse reuses its session id, codex uses `exec resume`, and
claude uses `--resume` in the same workspace with the prompt on stdin. A
resume the CLI cannot find (for example claude answering "No conversation
found") records an actionable incomplete handoff with the stored session
preserved and the budget untouched: retry the parked action, or pass
`--fresh` to deliberately mint a new session. The loop never silently
starts a fresh session in place of a failed resume and never substitutes
another provider. Author sessions are never reused for review. Reviewers
run read-only with hook activity disabled and never modify files: a
reviewer session that writes is a defect, not a verdict. Authors run with
explicit write authority (no bypass flags — the claude author combines
safe mode, which disables hook activity, with an edit-accepting permission
mode plus read/search/edit tools plus shell scoped to coding, git,
openspec, interpreters, test runners, and builds, so headless test/build
commands work); reviewers add shell/filesystem lockdown on top of
read-only review prompts.

Every invocation prints one token-usage line (action, provider, model,
effort, input/cached/output/total; unknown when the provider reports no
counters — unknown is never zero) and persists it to the per-change usage
log with cumulative totals in `status`. Usage never consumes budget.

An error/limit handoff parks the in-flight action: retry exactly that
action next (`status` names it); anything else is refused. A step counts
as successful only with a clean reviewer verdict against the current
content fingerprint, bound to the pending invocation's live session.

Plan and code review each have an independent allowance of five blocking
(P0–P2) verdicts per change. Each blocking verdict consumes only its own
phase's allowance. Clean results, errors, and limits consume nothing. The
fifth blocking verdict in either phase stops all invocations and further
autonomous fixing — hand the preserved findings to the user. Only an explicit
user-authorized reset permits fresh allowances for both phases:

```sh
scripts/change-loop reset-rounds <change-name> --confirm
```

A step counts as successful only with a clean reviewer verdict against the
current content fingerprint. Every verdict is bound to the pending
invocation that produced it (reviewer identity, session, unchanged
plan/code fingerprints, and the input revision it was invoked under): a
verdict for changed content or superseded input is stale and never
approves. Provider errors, limits, exhaustion, and stale
verdicts are incomplete handoffs: stop and return control to the user.

## 4. OpenSpec CLI authority

All artifact work goes through the `openspec` CLI (`new`, `status`,
`instructions`, `validate --strict`, `archive`), respecting configured roots,
stores, and schema. Never scaffold change directories by hand and never embed
artifact templates. A clean verdict approves only when the acceptance gates
hold at record time: every CLI-reported planning artifact done/skipped plus
`validate --strict` green — and for code review, the CLI's own apply
progress shows no remaining tasks (`openspec instructions apply --json`;
no artifact-name or file-name matching anywhere). Acceptance for each plan
round-trip:

```sh
openspec validate <change-name> --strict
```
