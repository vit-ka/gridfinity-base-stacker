

<!-- change-loop-instructions:start -->
## Change loop workflow

Use the OpenSpec CLI for change creation, status, instructions, and validation.
Coordinate implementation with the $change skill: parse exactly five arguments
(change name plus plan-writer, plan-reviewer, coder, code-reviewer — each
`provider` or `provider:model`), take the
description from accompanying prose and/or agreed exploration context (ask
before invoking when nothing usable exists; never invent scope). An existing
change with populated planning artifacts needs no prose: `init` and
`check-input` record those CLI-resolved artifacts as the input context
automatically. Persist input
via `scripts/change-loop persist-input`, and drive each step with the matching
helper action in the selected provider session (every invocation needs an
explicit model: init mapping or `--model`; missing models refuse before
invocation, never default or fallback; every invocation also carries an
explicit effort: all roles default to medium; resolution is `--effort`
override, then saved role effort, then the default. The helper
prints provider/model/effort and one token-usage line per invocation).
Never do role work in the host
session, never fall back to another provider, and never treat helper exit zero
as review evidence. Plan and code review each have an independent five-verdict
budget per change; at exhaustion in either phase stop and wait for user
direction. Only
explicit user authorization for `reset-rounds <change> --confirm` permits that
reset of both allowances. Every role invocation gets fresh, complete
CLI-resolved context (status plus artifact or apply instructions with full file contents,
rebuilt for fresh, retry, and resumed sessions); the coder follows the
openspec-apply-change skill in the selected scope while reviewers stay
read-only. See CONTRIBUTING.md for controls, shared state, and skill setup.
<!-- change-loop-instructions:end -->
