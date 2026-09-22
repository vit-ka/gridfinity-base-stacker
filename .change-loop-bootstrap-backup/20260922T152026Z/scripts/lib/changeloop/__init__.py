"""changeloop — shared constants for the $change loop helper.

Managed file: change-loop-managed-file. — shared constants for the $change loop helper.

Single source of truth for provider identity, budget, steps, and exit codes
used by scripts/change-loop and its library modules.
"""

PROVIDERS_ALL = ("codex", "claude", "muse", "grok", "gemini")
PROVIDERS_VERIFIED = ("muse", "codex", "claude")
PROVIDERS_DECLARED = ("grok", "gemini")

# The five-verdict allowance is FIXED and cumulative per change across plan
# and code review. It is a constant, not configuration: no environment
# variable can raise it.
MAX_ROUNDS = 5

# Finding blocks when its severity number is <= threshold.
# Default threshold 2 blocks P0-P2; P3 never blocks.
THRESHOLD = 2
SEVERITIES = ("P0", "P1", "P2", "P3")

VERDICT_STATUSES = ("clean", "blocking", "error", "limit")

# Loop step vocabulary. Author completion advances to the matching
# review-pending step; clean review verdicts advance plan->code->done;
# blocking verdicts route back to the author step; errors/limits/exhaustion
# park in handoff.
STEPS = ("plan", "plan-review", "code", "code-review", "done", "handoff")

REVIEW_STEPS = ("plan-review", "code-review")

# Exit codes: 0 = clean / handoff recorded (stop is allowed),
#             2 = blocking findings recorded, 1 = misuse / misconfiguration,
#             3 = change locked by another run.
EXIT_OK = 0
EXIT_MISUSE = 1
EXIT_BLOCKING = 2
EXIT_LOCKED = 3


def is_blocking_severity(sev, threshold=THRESHOLD):
    try:
        return SEVERITIES.index(sev) <= threshold
    except ValueError:
        return False
