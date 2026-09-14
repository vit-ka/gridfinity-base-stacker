#!/usr/bin/env python3
"""bootstrap-change-loop.sh — provision the $change loop into a repository.

Managed file: change-loop-managed-file.

Run it inside the target repo, or pass the repo path:
  bootstrap-change-loop.sh [/path/to/repo]

Idempotent: re-running converges the repo onto the current loop without
duplicating entries, blocks, gitignore lines, or docs. A second run changes
nothing.

Steps:
  1. openspec init (only if the repo has no openspec/ directory)
  2. install managed scripts + skill (missing or owned-dirty destinations;
     unrelated collisions at replacement paths are preserved, never
     overwritten; owned-dirty files are backed up first under
     .change-loop-bootstrap-backup/<timestamp>/)
  3. remove stale managed files from the previous layout
  4. add .gitignore entries for the loop's transient state
  5. document the loop in CONTRIBUTING.md, AGENTS.md, and CLAUDE.md
     (versioned sentinel blocks, refreshed in place)

NEVER wires hooks: .claude/settings.json and .codex/hooks.json are not
touched. The loop is driven entirely by host invocation of scripts/change-loop.

The managed files are copied from THIS canonical checkout. All OpenSpec
artifact structure is resolved via the `openspec` CLI at runtime — no
templates are vendored here or in any installed file.
"""

import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "lib"))

from changeloop import install as INS


def _canon():
    return os.path.dirname(os.path.abspath(__file__))


def _timestamp():
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def main(argv):
    target = argv[0] if argv else os.getcwd()
    if not os.path.isdir(target):
        sys.stderr.write("bootstrap-change-loop: not a directory: %s\n"
                         % target)
        return 1
    target = os.path.realpath(target)
    for tool in ("openspec",):
        if shutil.which(tool) is None:
            sys.stderr.write("bootstrap-change-loop: '%s' not found on "
                             "PATH.\n" % tool)
            return 1
    proc = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"],
                          cwd=target, stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL)
    if proc.returncode != 0:
        sys.stderr.write("bootstrap-change-loop: '%s' is not a git "
                         "repository. Run 'git init' first.\n" % target)
        return 1
    canon = os.environ.get("CHANGE_LOOP_CANON") or os.path.join(
        _canon(), "..")
    canon = os.path.realpath(canon)

    if target == canon:
        print("bootstrap-change-loop: target is the canonical checkout — "
              "verifying convergence only.")
        return 0

    print("bootstrap-change-loop: provisioning change loop into %s" % target)

    if not os.path.isdir(os.path.join(target, "openspec")):
        print("  - openspec init --tools none")
        proc = subprocess.run(["openspec", "init", "--tools", "none"],
                              cwd=target)
        if proc.returncode != 0:
            sys.stderr.write("bootstrap-change-loop: openspec init "
                             "failed.\n")
            return 1
    else:
        print("  - openspec already initialized (skipped)")

    installs, collisions = INS.plan_install(target, canon)
    backup_root = None

    def backup(path):
        nonlocal backup_root
        if backup_root is None:
            backup_root = os.path.join(
                target, ".change-loop-bootstrap-backup", _timestamp())
            os.makedirs(backup_root, exist_ok=True)
        rel = os.path.relpath(path, target)
        dest = os.path.join(backup_root, rel)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copy2(path, dest)

    if installs:
        installed = INS.apply_install(target, installs, backup=backup)
        print("  - installed managed files: %s" % " ".join(installed))
        if backup_root:
            print("  - backup of owned-dirty replacements: %s" % backup_root)
    else:
        print("  - managed files already converged (skipped)")
    for rel in collisions:
        print("  - collision preserved (unrelated content, not overwritten):"
              " %s" % rel)

    removals, legacy_collisions = INS.plan_legacy(target)
    for rel in removals:
        # Owned stale file: back up the dirty original first, then remove.
        backup(os.path.join(target, rel))
        os.unlink(os.path.join(target, rel))
        print("  - removed stale managed file: %s" % rel)
    for rel in legacy_collisions:
        print("  - collision preserved (unrelated content at a stale path, "
              "not removed): %s" % rel)

    if INS.ensure_gitignore(target):
        print("  - ensured .gitignore entries")
    else:
        print("  - .gitignore already covers transient state (skipped)")

    changed = INS.ensure_guidance(target)
    if changed:
        print("  - documented/refreshed the loop in: %s"
              % " ".join(changed))
    else:
        print("  - loop docs already current (skipped)")

    if collisions:
        print("bootstrap-change-loop: done with %d preserved collision(s) "
              "listed above. Review with 'git status' and commit when "
              "ready." % len(collisions))
    else:
        print("bootstrap-change-loop: done. Review the changes with "
              "'git status' and commit when ready.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
