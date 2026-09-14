#!/usr/bin/env python3
"""migrate-from-gate.sh — inventory, dry-run, and authorized migration off the
old hook-based review gate onto the $change loop.

Managed file: change-loop-managed-file.

  migrate-from-gate.sh                    scan ~/Projects, read-only inventory/dry-run report
  migrate-from-gate.sh <repo...>          inventory/dry-run for named repos (changes nothing)
  migrate-from-gate.sh --apply <repo...>  execute the authorized migration

--apply requires explicit repo paths (never implicit whole-tree apply) and
runs only after replacement tests pass AND a clean separate Codex code
review — authorization for that execution was already given; no artificial
confirmation step is added here.

Owned-only removal, and ownership is CONTENT-verified, never filename-only:
a path whose name matches the old bootstrap's list counts as owned only when
its bytes carry an old-gate marker (review-gate / codex-gate / REVIEW_GATE).
A name match WITHOUT the marker is an unrelated collision: preserved
byte-for-byte and reported distinctly, never removed.

Owned scope (content-verified):
  scripts:  scripts/{review-gate.sh,review-gate-hook.sh,review-gate,
              review-gate-arm.sh,review-gate-codex-hook.sh,
              codex-gate.sh,codex-gate-hook.sh,codex-gate}
  skills:   .claude/skills/review-gate, .agents/skills/review-gate
  hooks:    settings entries whose command matches the old gate's owned-hook
            pattern, in .claude/settings.json and .codex/hooks.json
  guidance: <!-- review-gate-docs:start -->…<!-- review-gate-docs:end -->
            in CONTRIBUTING.md, and
            <!-- review-gate-instructions:start -->…<!-- …:end --> in
            AGENTS.md / CLAUDE.md

Always preserved (never removed, never rewritten):
  .review-gate-history/ and .claude/.review-gate-* state (history/budget
  provenance stays readable and attributable), unrelated hooks/settings/
  skills/docs, the .gitignore transient glob, old source code/history.
Every removal/modification is backed up first under
.change-loop-migration-backup/<timestamp>/ (owned files included when they
carry local modifications — backup is unconditional).

Old-source repo (detected via bootstrap-repo.sh + scripts/review-gate.sh):
apply ceases hook activity (hook wiring + armed marker) while retaining
implementation, docs, and history.

Old-gate budgets are preserved AND imported automatically: apply parses
.claude/.review-gate-budget-*.json ({change, used}) into the transient
.change-loop/migrated-budget.json snapshot that loop init recovers from.

Inventory and apply share ONE action plan per repo: dry-run renders the plan
with zero writes; --apply executes exactly that plan. An empty plan is a
true no-op (no backup directory, no bootstrap side effects).

The tool never runs git reset/checkout and never touches dirty/untracked
content outside the owned list above. Dry-run performs ZERO writes.
"""

import json
import os
import re
import shutil
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "lib"))

from changeloop import install as INS
from changeloop import state as ST

OWNED_SCRIPTS = ("review-gate.sh", "review-gate-hook.sh", "review-gate",
                 "review-gate-arm.sh", "review-gate-codex-hook.sh",
                 "codex-gate.sh", "codex-gate-hook.sh", "codex-gate")
OWNED_SKILLS = (".claude/skills/review-gate", ".agents/skills/review-gate")

# Inverse of the old bootstrap's keep_other merge filter.
OWNED_HOOK_RE = (r"^(REVIEW_GATE_HOST=(claude|codex) )?bash [^\n]*"
                 r"scripts/(review-gate-(hook|arm|codex-hook)|"
                 r"codex-gate-(hook|arm))\.sh\"?$")
OWNED_HOOK_PATTERN = re.compile(OWNED_HOOK_RE)

# Content markers proving a file came from the old gate bootstrap.
# These are DISTINCTIVE old-bootstrap strings (header lines, code
# constants, the canonical repo reference, the host-selection env var) —
# never bare filename substrings: any independently maintained
# scripts/review-gate.sh contains its own name in usage text, so matching
# "review-gate" alone is vacuous and would delete unrelated work.
# Anything without these markers stays a reported collision (preserved).
OLD_GATE_MARKERS = ("host-profile code-review gate",
                    "Stop-hook guard for the review gate",
                    "arms the review gate",
                    "SKIP_REVIEW_GATE",
                    "openspec-codex-gate",
                    "REVIEW_GATE_HOST=")

DOCS_BLOCKS = (
    ("CONTRIBUTING.md", "<!-- review-gate-docs:start -->",
     "<!-- review-gate-docs:end -->"),
    ("AGENTS.md", "<!-- review-gate-instructions:start -->",
     "<!-- review-gate-instructions:end -->"),
    ("CLAUDE.md", "<!-- review-gate-instructions:start -->",
     "<!-- review-gate-instructions:end -->"),
)


def _canon():
    override = os.environ.get("CHANGE_LOOP_CANON")
    if override:
        return os.path.realpath(override)
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.realpath(os.path.join(here, ".."))


def _timestamp():
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _has_marker(path):
    try:
        with open(path, "rb") as fh:
            content = fh.read().decode("utf-8", "replace")
    except OSError:
        return False
    return any(marker in content for marker in OLD_GATE_MARKERS)


def _dir_has_marker(path):
    found_any = False
    for dirpath, _dirnames, filenames in os.walk(path):
        for name in filenames:
            found_any = True
            if _has_marker(os.path.join(dirpath, name)):
                return True
    return False if found_any else None


def is_source_repo(repo):
    return (os.path.isfile(os.path.join(repo, "bootstrap-repo.sh"))
            and os.path.isfile(os.path.join(repo, "scripts",
                                            "review-gate.sh")))


def _owned_hook_commands(settings_path):
    """Owned hook commands (with matchers) in a settings file, or []."""
    try:
        with open(settings_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return []
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return []
    owned = []

    def visit(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "command" and isinstance(value, str):
                    if OWNED_HOOK_PATTERN.search(value):
                        owned.append(value)
                else:
                    visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    visit(hooks)
    return owned


def _old_budget_snapshot(repo):
    """{change: used} parsed from old-gate budget files (content-verified)."""
    result = {}
    sources = []
    claude_dir = os.path.join(repo, ".claude")
    try:
        names = sorted(os.listdir(claude_dir))
    except OSError:
        return result, sources
    for name in names:
        if not (name.startswith(".review-gate-") and name.endswith(".json")):
            continue
        path = os.path.join(claude_dir, name)
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        change = data.get("change")
        used = data.get("used")
        if (isinstance(change, str) and isinstance(used, int)
                and not isinstance(used, bool) and 0 <= used <= 5):
            if used > result.get(change, 0):
                result[change] = used
            sources.append("%s (change %s: used=%d)"
                           % (path, change, used))
    return result, sources


# --- action plan ------------------------------------------------------------------
# build_plan computes the complete, concrete action list. The dry-run report
# renders it (zero writes); --apply executes exactly it.
#
# Eligibility: only identified old-gate consumers get actions. A directory
# with no gate trace at all (no owned scripts/skills, no owned hook
# wiring, no managed doc blocks, no armed marker, no budget files, no
# gate history) yields an empty plan — zero modifications, even on a
# whole-root scan. A same-named file WITHOUT an old-gate marker is an
# unrelated collision, never an eligibility signal.

def _gate_signals(repo):
    """True when the repo shows any old-gate consumer trace."""
    for name in OWNED_SCRIPTS:
        path = os.path.join(repo, "scripts", name)
        if os.path.isdir(path) and not os.path.islink(path):
            if _dir_has_marker(path):
                return True
        elif os.path.isfile(path):
            if _has_marker(path):
                return True
    for rel in OWNED_SKILLS:
        path = os.path.join(repo, rel)
        if os.path.isdir(path) and not os.path.islink(path):
            if _dir_has_marker(path):
                return True
        elif os.path.isfile(path):
            if _has_marker(path):
                return True
    for rel in (".claude/settings.json", ".codex/hooks.json"):
        path = os.path.join(repo, rel)
        if os.path.isfile(path) and _owned_hook_commands(path):
            return True
    for filename, start, end in DOCS_BLOCKS:
        path = os.path.join(repo, filename)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as fh:
                body = fh.read()
        except OSError:
            continue
        if start in body and end in body:
            return True
    if os.path.isfile(os.path.join(repo, ".claude",
                                   ".review-gate-active")):
        return True
    try:
        names = os.listdir(os.path.join(repo, ".claude"))
    except OSError:
        names = []
    for name in names:
        if name.startswith(".review-gate-") and name.endswith(".json"):
            return True
    if os.path.isdir(os.path.join(repo, ".review-gate-history")):
        return True
    return False


def build_plan(repo, canon):
    plan = {"repo": repo, "kind": None, "actions": [], "preserved": [],
            "collisions": [], "sources": []}
    plan["kind"] = ("OLD-SOURCE" if is_source_repo(repo) else "consumer")

    if plan["kind"] == "OLD-SOURCE":
        # Old-source repos keep implementation, docs, skills, and history;
        # only hook activity ceases. Ownership content checks still apply.
        for rel in (".claude/settings.json", ".codex/hooks.json"):
            path = os.path.join(repo, rel)
            if os.path.isfile(path) and _owned_hook_commands(path):
                plan["actions"].append({"kind": "strip-hooks", "path": path})
        armed = os.path.join(repo, ".claude", ".review-gate-active")
        if os.path.isfile(armed):
            plan["actions"].append({"kind": "clear-armed-marker",
                                   "path": armed})
        _record_preserved(plan, repo)
        return plan

    owned_scripts = []
    for name in OWNED_SCRIPTS:
        path = os.path.join(repo, "scripts", name)
        if os.path.lexists(path):
            if os.path.isdir(path) and not os.path.islink(path):
                owned = _dir_has_marker(path)
            elif os.path.isfile(path):
                owned = _has_marker(path)
            else:
                owned = None
            if owned:
                owned_scripts.append(path)
            else:
                plan["collisions"].append(
                    "%s (name matches old gate but content carries no "
                    "old-gate marker — preserved)" % path)
    owned_skills = []
    for rel in OWNED_SKILLS:
        path = os.path.join(repo, rel)
        if os.path.lexists(path):
            if os.path.isdir(path) and not os.path.islink(path):
                owned = _dir_has_marker(path)
            elif os.path.isfile(path):
                owned = _has_marker(path)
            else:
                owned = None
            if owned:
                owned_skills.append(path)
            else:
                plan["collisions"].append(
                    "%s (name matches old gate but content carries no "
                    "old-gate marker — preserved)" % path)
    for path in owned_scripts + owned_skills:
        plan["actions"].append({"kind": "remove-owned", "path": path})

    for rel in (".claude/settings.json", ".codex/hooks.json"):
        path = os.path.join(repo, rel)
        if os.path.isfile(path) and _owned_hook_commands(path):
            plan["actions"].append({"kind": "strip-hooks", "path": path})

    for filename, start, end in DOCS_BLOCKS:
        path = os.path.join(repo, filename)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as fh:
                body = fh.read()
        except OSError:
            continue
        if start in body and end in body:
            plan["actions"].append({"kind": "strip-docs", "path": path,
                                   "start": start, "end": end})

    armed = os.path.join(repo, ".claude", ".review-gate-active")
    if os.path.isfile(armed):
        plan["actions"].append({"kind": "clear-armed-marker", "path": armed})

    if plan["kind"] == "consumer" and not _gate_signals(repo):
        # Not an identified old-gate consumer: the plan stays empty (no
        # installs, no removals, no guidance, no budget work) — unrelated
        # directories get zero actions even on a whole-root scan.
        _record_preserved(plan, repo)
        return plan

    if plan["kind"] == "consumer":
        installs, collisions = INS.plan_install(repo, canon)
        for dest_rel, src in installs:
            plan["actions"].append({"kind": "install", "dest": dest_rel,
                                   "src": src})
        plan["collisions"].extend(
            "replacement %s (unrelated content — preserved, not installed)"
            % rel for rel in collisions)
        removals, legacy_collisions = INS.plan_legacy(repo)
        for rel in removals:
            plan["actions"].append({"kind": "remove-owned",
                                   "path": os.path.join(repo, rel),
                                   "note": "stale managed file from the "
                                           "previous loop layout"})
        plan["collisions"].extend(
            "stale %s (unrelated content — preserved, not removed)"
            % rel for rel in legacy_collisions)
        if INS.gitignore_missing(repo):
            plan["actions"].append({"kind": "ensure-gitignore"})
        for filename, _mode in INS.guidance_needed(repo):
            plan["actions"].append({"kind": "ensure-guidance",
                                   "file": filename})

    budget, sources = _old_budget_snapshot(repo)
    plan["sources"] = sources
    if budget:
        snapshot = os.path.join(repo, ".change-loop",
                                "migrated-budget.json")
        current = {}
        try:
            with open(snapshot, "r", encoding="utf-8") as fh:
                current = (json.load(fh).get("changes") or {})
        except (OSError, ValueError):
            current = {}
        merged = dict(current)
        for change, used in budget.items():
            merged[change] = max(merged.get(change, 0), used)
        if merged != current:
            plan["actions"].append({"kind": "import-budget",
                                   "changes": merged, "sources": sources})

    _record_preserved(plan, repo)
    return plan


def _record_preserved(plan, repo):
    history = os.path.join(repo, ".review-gate-history")
    if os.path.isdir(history):
        plan["preserved"].append("%s/.review-gate-history/ (verdict "
                                 "history, retained)" % repo)
    claude_dir = os.path.join(repo, ".claude")
    try:
        names = sorted(os.listdir(claude_dir))
    except OSError:
        names = []
    shown_budget = False
    for name in names:
        if name.startswith(".review-gate-") and not name.endswith(".tmp."):
            full = os.path.join(claude_dir, name)
            if os.path.exists(full) and not shown_budget:
                plan["preserved"].append(
                    "%s/.claude/.review-gate-* (budget/session/cooldown "
                    "provenance, retained)" % repo)
                shown_budget = True


def plan_is_empty(plan):
    return not plan["actions"]


# --- dry-run report (zero writes) -----------------------------------------------------

def _describe_action(action):
    kind = action["kind"]
    if kind == "remove-owned":
        note = " (%s)" % action["note"] if action.get("note") else ""
        return "remove owned: %s%s" % (action["path"], note)
    if kind == "strip-hooks":
        return "strip owned hook wiring (unrelated hooks kept): %s" % action[
            "path"]
    if kind == "strip-docs":
        return "strip managed block: %s" % action["path"]
    if kind == "clear-armed-marker":
        return "clear armed-workstream marker (hook activity): %s" % action[
            "path"]
    if kind == "install":
        return "install replacement: %s" % action["dest"]
    if kind == "ensure-gitignore":
        return "ensure .gitignore transient entry"
    if kind == "ensure-guidance":
        return "add/refresh managed guidance: %s" % action["file"]
    if kind == "import-budget":
        return "import old-gate budget snapshot: %s" % ", ".join(
            "%s=%d" % item for item in sorted(action["changes"].items()))
    return kind


def report_plan(plan):
    repo = plan["repo"]
    print("=== %s ===" % repo)
    if plan["kind"] == "OLD-SOURCE":
        print("  kind: OLD-SOURCE repo (implementation/history retained; "
              "hook activity to cease)")
    else:
        print("  kind: consumer repo")
    if plan_is_empty(plan):
        print("  owned gate items: none")
    else:
        print("  owned gate items (%d):" % len(plan["actions"]))
        for action in plan["actions"]:
            print("    - %s" % _describe_action(action))
    for item in plan["preserved"]:
        print("    keep: %s" % item)
    for item in plan["collisions"]:
        print("    collision (preserved, never removed): %s" % item)
    if plan["sources"]:
        for source in plan["sources"]:
            print("    budget source: %s" % source)
    if plan_is_empty(plan):
        print("  dry-run: no modifications.")
    elif plan["kind"] == "OLD-SOURCE":
        print("  dry-run would: back up owned items; remove owned hook "
              "wiring + armed marker; retain scripts/skills/docs/history.")
    else:
        print("  dry-run would: back up owned items; remove owned "
              "scripts/skills/hooks/guidance; install replacement "
              "(managed scripts + $change skill); import old-gate budget; "
              "preserve everything listed under keep/collision.")
    print("")
    return True


# --- apply (executes exactly the planned actions) ---------------------------------------

def _copy_for_backup(src, backup_root, repo):
    rel = os.path.relpath(src, repo)
    dest = os.path.join(backup_root, rel)
    if os.path.lexists(dest):
        # First backup per path wins: the same file is backed up before
        # stripping old guidance AND before installing new guidance, and
        # the second copy must never overwrite the original with the
        # stripped intermediate version.
        return
    parent = os.path.dirname(dest)
    if parent:
        os.makedirs(parent, exist_ok=True)
    if os.path.isdir(src) and not os.path.islink(src):
        shutil.copytree(src, dest, symlinks=True)
    else:
        shutil.copy2(src, dest)


def _is_owned_hook(hook):
    return (isinstance(hook, dict) and hook.get("type") == "command"
            and bool(OWNED_HOOK_PATTERN.search(hook.get("command") or "")))


def _strip_owned_hooks(path):
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return False

    def clean(node):
        changed = False
        if isinstance(node, dict):
            for key, value in list(node.items()):
                if key == "hooks" and isinstance(value, list):
                    kept = []
                    for entry in value:
                        if (isinstance(entry, dict)
                                and isinstance(entry.get("hooks"), list)):
                            # Matcher wrapper: filter its nested hook list.
                            sub = entry["hooks"]
                            remaining = [hook for hook in sub
                                         if not _is_owned_hook(hook)]
                            if len(remaining) != len(sub):
                                changed = True
                            if remaining:
                                entry["hooks"] = remaining
                                kept.append(entry)
                            else:
                                changed = True
                        elif _is_owned_hook(entry):
                            changed = True
                        else:
                            kept.append(entry)
                    node[key] = kept
                else:
                    if clean(value):
                        changed = True
        elif isinstance(node, list):
            for value in node:
                if clean(value):
                    changed = True
        return changed

    if not clean(data):
        return False
    tmp = path + ".tmp.%d" % os.getpid()
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, path)
    return True


def _strip_docs_block(path, start, end):
    with open(path, "r", encoding="utf-8") as fh:
        body = fh.read()
    if start not in body or end not in body:
        return False
    before, _, rest = body.partition(start)
    _, _, after = rest.partition(end)
    tmp = path + ".tmp.%d" % os.getpid()
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(before.rstrip("\n") + "\n" + after.lstrip("\n"))
    os.replace(tmp, path)
    return True


def apply_plan(plan):
    repo = plan["repo"]
    if plan_is_empty(plan):
        print("=== %s ===" % repo)
        print("  no modifications.")
        return True
    print("=== %s ===" % repo)
    backup_root = os.path.join(repo, ".change-loop-migration-backup",
                               _timestamp())
    os.makedirs(backup_root, exist_ok=True)

    def backup(path):
        _copy_for_backup(path, backup_root, repo)

    installs = [a for a in plan["actions"] if a["kind"] == "install"]
    if plan["kind"] == "OLD-SOURCE":
        print("  old-source repo: ceasing hook activity, retaining "
              "implementation/history.")
    for action in plan["actions"]:
        kind = action["kind"]
        if kind == "remove-owned":
            backup(action["path"])
            if os.path.isdir(action["path"]) and not os.path.islink(
                    action["path"]):
                shutil.rmtree(action["path"])
            else:
                os.unlink(action["path"])
            print("  removed owned: %s" % os.path.relpath(action["path"],
                                                          repo))
        elif kind == "strip-hooks":
            backup(action["path"])
            if _strip_owned_hooks(action["path"]):
                print("  removed owned hook wiring from %s (unrelated hooks "
                      "preserved; backup kept)."
                      % os.path.relpath(action["path"], repo))
        elif kind == "strip-docs":
            backup(action["path"])
            if _strip_docs_block(action["path"], action["start"],
                                 action["end"]):
                print("  removed managed block from %s (backup kept)."
                      % os.path.relpath(action["path"], repo))
        elif kind == "clear-armed-marker":
            backup(action["path"])
            os.unlink(action["path"])
            print("  cleared armed-workstream marker (hook activity "
                  "ceased).")
        elif kind == "import-budget":
            snapshot = os.path.join(repo, ".change-loop",
                                    "migrated-budget.json")
            os.makedirs(os.path.dirname(snapshot), exist_ok=True)
            with open(snapshot, "w", encoding="utf-8") as fh:
                json.dump({"changes": action["changes"],
                           "sources": action["sources"],
                           "importedAt": _timestamp()}, fh, indent=2,
                          sort_keys=True)
            print("  imported old-gate budget snapshot: %s" % ", ".join(
                "%s=%d" % item for item in sorted(
                    action["changes"].items())))
            # The snapshot above lives under git-ignored .change-loop/,
            # so a fresh clone would restore a full allowance. ALSO append
            # one budget record per change to the committable history
            # store, which budget replay already parses.
            provenance = "; ".join(action["sources"])
            for change_name, used in sorted(action["changes"].items()):
                ST.history_record(
                    repo, change_name, "budget", None,
                    "imported budget provenance: %d consumed (%s)"
                    % (used, provenance))
    if installs:
        installed = INS.apply_install(
            repo, [(a["dest"], a["src"]) for a in installs], backup=backup)
        print("  installed replacement: %s" % " ".join(installed))
    for action in plan["actions"]:
        if action["kind"] == "ensure-gitignore":
            if INS.ensure_gitignore(repo):
                print("  ensured .gitignore transient entry")
        elif action["kind"] == "ensure-guidance":
            pass
    guidance_targets = sorted(set(a["file"] for a in plan["actions"]
                                  if a["kind"] == "ensure-guidance"))
    if guidance_targets:
        for filename in guidance_targets:
            path = os.path.join(repo, filename)
            if os.path.isfile(path):
                backup(path)
        changed = INS.ensure_guidance(repo)
        if changed:
            print("  added/refreshed managed guidance: %s"
                  % " ".join(changed))
    if plan["kind"] == "OLD-SOURCE":
        print("  retained: scripts/, skills, docs, .review-gate-history/, "
              "budget/session state.")
    else:
        print("  preserved: .review-gate-history/, budget/session state, "
              "unrelated content.")
    for item in plan["collisions"]:
        print("  collision (preserved): %s" % item)
    print("  backup: %s" % backup_root)
    return True


# --- entry ------------------------------------------------------------------------------------

def _enumerate_repos(args):
    repos = []
    if not args:
        home = os.path.expanduser("~")
        projects = os.path.join(home, "Projects")
        try:
            names = sorted(os.listdir(projects))
        except OSError:
            return repos
        for name in names:
            full = os.path.realpath(os.path.join(projects, name))
            if os.path.isdir(full):
                repos.append(full)
    else:
        for arg in args:
            if not os.path.isdir(arg):
                sys.stderr.write("migrate-from-gate: not a directory: %s\n"
                                 % arg)
                sys.exit(1)
            repos.append(os.path.realpath(arg))
    return repos


def main(argv):
    apply = False
    args = list(argv)
    if args[:1] == ["--apply"]:
        apply = True
        args = args[1:]
    if apply and not args:
        sys.stderr.write("migrate-from-gate: --apply needs explicit repo "
                         "paths (no implicit whole-tree apply).\n")
        return 1
    canon = _canon()
    repos = _enumerate_repos(args)
    if not apply:
        print("migrate-from-gate: inventory/dry-run (read-only — nothing "
              "will be modified)")
        print("")
        shown = 0
        for repo in repos:
            plan = build_plan(repo, canon)
            if plan_is_empty(plan) and not plan["preserved"] \
                    and not plan["collisions"] and not plan["sources"]:
                continue
            report_plan(plan)
            shown += 1
        print("Scanned %d repos; %d have gate-related items."
              % (len(repos), shown))
        print("Re-run with --apply <repo...> (after tests + clean Codex "
              "review) to execute.")
        return 0
    print("migrate-from-gate: executing authorized migration on %d "
          "repo(s)." % len(repos))
    for repo in repos:
        apply_plan(build_plan(repo, canon))
    print("migrate-from-gate: done. Verify with 'git status' in each repo "
          "and commit when ready.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
