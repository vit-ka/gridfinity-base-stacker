"""changeloop.install — shared replacement-install logic.

Managed file: change-loop-managed-file.

Used by bootstrap-change-loop.sh (standalone provisioning) and
migrate-from-gate.sh (replacement half of migration) so both converge onto
the same file set with the same preflight rules:

- Missing destination -> install.
- Destination differs and carries the managed marker -> back up, then
  overwrite (owned-dirty replacement).
- Destination differs WITHOUT the marker -> unrelated collision: preserve
  byte-for-byte, skip, report distinctly. Never overwritten.
"""

import os
import shutil

# Managed replacement set, relative to the repo root, mapped to the
# canonical source relative path.
MANAGED = {
    "scripts/change-loop": "scripts/change-loop",
    "scripts/bootstrap-change-loop.sh":
        "scripts/bootstrap-change-loop.sh",
    "scripts/sync-change-loop.sh": "scripts/sync-change-loop.sh",
    "scripts/migrate-from-gate.sh": "scripts/migrate-from-gate.sh",
    "scripts/lib/changeloop/__init__.py":
        "scripts/lib/changeloop/__init__.py",
    "scripts/lib/changeloop/state.py": "scripts/lib/changeloop/state.py",
    "scripts/lib/changeloop/openspec.py":
        "scripts/lib/changeloop/openspec.py",
    "scripts/lib/changeloop/providers.py":
        "scripts/lib/changeloop/providers.py",
    "scripts/lib/changeloop/install.py":
        "scripts/lib/changeloop/install.py",
    ".agents/skills/change/SKILL.md": ".agents/skills/change/SKILL.md",
    ".claude/skills/change/SKILL.md": ".agents/skills/change/SKILL.md",
}

# Stale managed files from the previous (bash-lib) layout: ours ONLY when
# their bytes carry the managed sentinel — a same-named file without it is
# an unrelated collision (preserved, never removed). Removal backs up
# owned-dirty originals first (callers pass their backup hook).
LEGACY_MANAGED = [
    "scripts/lib/change-loop-state.sh",
    "scripts/lib/change-loop-providers.sh",
    "scripts/lib/change-loop-cli.sh",
]


def legacy_owned(path):
    """True when a stale-layout path is genuinely ours (sentinel-verified).

    Files: the managed sentinel in the head bytes. Directories (defensive;
    the legacy list holds files): any nested file carrying the sentinel.
    """
    if os.path.isdir(path) and not os.path.islink(path):
        for dirpath, _dirnames, filenames in os.walk(path):
            for name in filenames:
                if is_managed_content(os.path.join(dirpath, name)):
                    return True
        return False
    return is_managed_content(path)


def plan_legacy(repo):
    """Split stale-layout paths into (owned_removals, collisions).

    Owned paths carry the managed sentinel and may be removed (backed up
    first by the caller); collisions are unrelated content that must be
    preserved byte-for-byte and reported distinctly.
    """
    removals = []
    collisions = []
    for rel in LEGACY_MANAGED:
        path = os.path.join(repo, rel)
        if os.path.lexists(path):
            if legacy_owned(path):
                removals.append(rel)
            else:
                collisions.append(rel)
    return removals, collisions

# A replacement destination is owned by us only when its bytes carry this
# explicit sentinel (stamped in every managed file header). A same-named
# file without it is an unrelated collision: preserved, never overwritten.
MANAGED_SENTINEL = b"change-loop-managed-file"

GITIGNORE_LINE = ".change-loop/"

LOOP_DOC_START = "<!-- change-loop-docs:start -->"
LOOP_DOC_END = "<!-- change-loop-docs:end -->"
LOOP_GUIDE_START = "<!-- change-loop-instructions:start -->"
LOOP_GUIDE_END = "<!-- change-loop-instructions:end -->"


def files_identical(first, second):
    try:
        if os.path.getsize(first) != os.path.getsize(second):
            return False
    except OSError:
        return False
    try:
        with open(first, "rb") as fh1, open(second, "rb") as fh2:
            while True:
                left = fh1.read(65536)
                right = fh2.read(65536)
                if left != right:
                    return False
                if not left:
                    return True
    except OSError:
        return False


def is_managed_content(path):
    try:
        with open(path, "rb") as fh:
            head = fh.read(65536)
    except OSError:
        return False
    lowered = head.lower()
    return MANAGED_SENTINEL in lowered


def plan_install(repo, canon):
    """Return (installs, collisions).

    installs: [(dest_rel, src_abs)] for missing or owned-dirty destinations.
    collisions: [dest_rel] preserved unrelated content (never overwritten).
    """
    installs = []
    collisions = []
    for dest_rel, src_rel in sorted(MANAGED.items()):
        src = os.path.join(canon, src_rel)
        if not os.path.isfile(src):
            continue
        dest = os.path.join(repo, dest_rel)
        if not os.path.exists(dest):
            installs.append((dest_rel, src))
        elif not files_identical(src, dest):
            if is_managed_content(dest):
                installs.append((dest_rel, src))
            else:
                collisions.append(dest_rel)
    return installs, collisions


def apply_install(repo, installs, backup=None):
    """Atomically install planned files; backup owned-dirty destinations."""
    installed = []
    for dest_rel, src in installs:
        dest = os.path.join(repo, dest_rel)
        existed = os.path.exists(dest)
        if existed and backup is not None:
            backup(dest)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        tmp = dest + ".tmp.%d" % os.getpid()
        shutil.copy2(src, tmp)
        if dest_rel.startswith("scripts/"):
            os.chmod(tmp, 0o755)
        os.replace(tmp, dest)
        installed.append(dest_rel)
    return installed


def gitignore_missing(repo):
    path = os.path.join(repo, ".gitignore")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                if line.strip() == GITIGNORE_LINE:
                    return False
    except OSError:
        pass
    return True


def ensure_gitignore(repo):
    if not gitignore_missing(repo):
        return False
    path = os.path.join(repo, ".gitignore")
    needs_newline = False
    try:
        with open(path, "rb") as fh:
            data = fh.read()
            needs_newline = bool(data) and not data.endswith(b"\n")
    except OSError:
        pass
    with open(path, "a", encoding="utf-8") as fh:
        if needs_newline:
            fh.write("\n")
        fh.write(GITIGNORE_LINE + "\n")
    return True


LOOP_DOC = """<!-- change-loop-docs:start -->
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
<!-- change-loop-docs:end -->"""

INSTRUCTIONS = """<!-- change-loop-instructions:start -->
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
<!-- change-loop-instructions:end -->"""


def _replace_block(path, start, end, block):
    with open(path, "r", encoding="utf-8") as fh:
        body = fh.read()
    if block in body:
        return "present"
    if start not in body:
        sep = "" if body.endswith("\n") or not body else "\n"
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(sep + "\n" + block + "\n")
        return "added"
    before, _, rest = body.partition(start)
    _, _, after = rest.partition(end)
    if end not in rest:
        return "present"
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(before + block + after)
    return "refreshed"


def guidance_needed(repo):
    """List (file, block-kind) guidance items missing or stale."""
    needed = []
    for filename, start, block in (
            ("CONTRIBUTING.md", LOOP_DOC_START, LOOP_DOC),
            ("AGENTS.md", LOOP_GUIDE_START, INSTRUCTIONS),
            ("CLAUDE.md", LOOP_GUIDE_START, INSTRUCTIONS)):
        path = os.path.join(repo, filename)
        try:
            with open(path, "r", encoding="utf-8") as fh:
                body = fh.read()
        except OSError:
            needed.append((filename, "added"))
            continue
        if start not in body or block not in body:
            needed.append((filename,
                           "added" if start not in body else "refreshed"))
    return needed


def ensure_guidance(repo):
    """Install/refresh managed guidance blocks; return changed files."""
    changed = []
    items = (("CONTRIBUTING.md", LOOP_DOC_START, LOOP_DOC_END, LOOP_DOC),
             ("AGENTS.md", LOOP_GUIDE_START, LOOP_GUIDE_END, INSTRUCTIONS),
             ("CLAUDE.md", LOOP_GUIDE_START, LOOP_GUIDE_END, INSTRUCTIONS))
    for filename, start, end, block in items:
        path = os.path.join(repo, filename)
        if not os.path.exists(path):
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(block + "\n")
            changed.append(filename)
            continue
        before = None
        try:
            with open(path, "r", encoding="utf-8") as fh:
                before = fh.read()
        except OSError:
            continue
        result = _replace_block(path, start, end, block)
        if result == "added" or (result == "refreshed"
                                 and block not in (before or "")):
            changed.append(filename)
    return changed
