

<!-- change-loop-instructions:start -->
## Change loop workflow

Use the OpenSpec CLI for change creation, status, instructions, and validation.
Coordinate implementation with the $change skill: parse exactly five arguments
(change name plus plan-writer, plan-reviewer, coder, code-reviewer — each
`provider` or `provider:model`), take the
description from accompanying prose and/or agreed exploration context (ask
before invoking when nothing usable exists; never invent scope), persist input
via `scripts/change-loop persist-input`, and drive each step with the matching
helper action in the selected provider session (every invocation needs an
explicit model: init mapping or `--model`; missing models refuse before
invocation, never default or fallback; every invocation also carries an
explicit effort: reviewers medium, authors high, or `--effort`; the helper
prints provider/model/effort and one token-usage line per invocation).
Never do role work in the host
session, never fall back to another provider, and never treat helper exit zero
as review evidence. The five-verdict budget is cumulative per change across
plan and code review; at exhaustion stop and wait for user direction. Only
explicit user authorization for `reset-rounds <change> --confirm` permits that
reset. See CONTRIBUTING.md for controls, shared state, and skill setup.
<!-- change-loop-instructions:end -->
