#!/usr/bin/env bash
#
# sync-change-loop.sh — refresh managed loop copies from the canonical checkout.
# Managed file: change-loop-managed-file.
#
# Explicit host invocation ONLY (never from hooks):
#   scripts/sync-change-loop.sh [/path/to/repo]
#
# Canonical-wins per file: compares the repo's managed scripts/skill against
# the canonical source and overwrites only differing files, preserving the
# executable bit and printing a one-line notice per updated file.
# Stale managed files from the previous layout are removed.
#
#   - Canonical source absent → skipped silently, committed copies used (exit 0).
#   - Target IS the canonical repo → never self-syncs (exit 0 with notice).
#
# Canonical source: ${CHANGE_LOOP_CANON:-~/Projects/openspec-change-loop}.

set -euo pipefail

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
CANON_REPO="${CHANGE_LOOP_CANON:-$HOME/Projects/openspec-change-loop}"

TARGET="${1:-$PWD}"
cd "$TARGET" || { echo "sync-change-loop: cannot cd into '$TARGET'" >&2; exit 1; }
TARGET="$(pwd -P)"

# Absent canonical source: standalone repo, skip silently, use committed copies.
if [ ! -d "$CANON_REPO/scripts" ] || [ ! -f "$CANON_REPO/.agents/skills/change/SKILL.md" ]; then
  exit 0
fi
CANON_REPO="$(cd "$CANON_REPO" && pwd -P)"

# The canonical repo never syncs onto itself.
if [ "$TARGET" = "$CANON_REPO" ]; then
  echo "sync-change-loop: target is the canonical checkout — self-sync skipped."
  exit 0
fi

MANAGED=(scripts/change-loop scripts/bootstrap-change-loop.sh
  scripts/sync-change-loop.sh scripts/migrate-from-gate.sh
  scripts/lib/changeloop/__init__.py scripts/lib/changeloop/state.py
  scripts/lib/changeloop/openspec.py scripts/lib/changeloop/providers.py
  scripts/lib/changeloop/install.py)
LEGACY_MANAGED=(scripts/lib/change-loop-state.sh
  scripts/lib/change-loop-providers.sh scripts/lib/change-loop-cli.sh)
SKILL_SRC=".agents/skills/change/SKILL.md"
SENTINEL="change-loop-managed-file"
updated=0
for rel in "${MANAGED[@]}" "$SKILL_SRC" ".claude/skills/change/SKILL.md"; do
  # Both host skill copies converge onto the one canonical skill source.
  case "$rel" in
    .claude/skills/change/SKILL.md) src="$CANON_REPO/$SKILL_SRC" ;;
    *) src="$CANON_REPO/$rel" ;;
  esac
  [ -f "$src" ] || continue
  if [ ! -f "$TARGET/$rel" ] || ! cmp -s "$src" "$TARGET/$rel"; then
    # Same sentinel rule as bootstrap/migration: a differing destination
    # WITHOUT the managed marker is an unrelated collision — preserved,
    # never overwritten (bootstrap and migration preserve it, so sync
    # must not clobber it on the next host invocation).
    if [ -f "$TARGET/$rel" ] \
      && ! grep -qi "$SENTINEL" "$TARGET/$rel" 2>/dev/null; then
      echo "sync-change-loop: preserved unrelated collision (no managed marker): $rel."
      continue
    fi
    mkdir -p "$TARGET/$(dirname "$rel")"
    tmp="$TARGET/$rel.tmp.$$"
    cp "$src" "$tmp"
    # Preserve the executable bit convention: scripts stay executable.
    case "$rel" in scripts/*) chmod +x "$tmp" ;; esac
    perl -e 'rename($ARGV[0], $ARGV[1]) or die "$!"' "$tmp" "$TARGET/$rel" \
      || { rm -f "$tmp"; echo "sync-change-loop: failed to update $rel" >&2; exit 1; }
    echo "sync-change-loop: updated $rel from canonical."
    updated=$(( updated + 1 ))
  fi
done
for rel in "${LEGACY_MANAGED[@]}"; do
  if [ -e "$TARGET/$rel" ] || [ -L "$TARGET/$rel" ]; then
    # Ownership evidence required: a same-named file WITHOUT the managed
    # marker is an unrelated collision — preserved, never removed.
    if ! grep -qi "$SENTINEL" "$TARGET/$rel" 2>/dev/null; then
      echo "sync-change-loop: preserved unrelated collision (no managed marker): $rel."
      continue
    fi
    # Owned stale file: back up the dirty original first, then remove.
    SYNC_BK="${SYNC_BACKUP_ROOT:-}"
    if [ -z "$SYNC_BK" ]; then
      SYNC_BK="$TARGET/.change-loop-sync-backup/$(date -u +%Y%m%dT%H%M%SZ)"
      mkdir -p "$SYNC_BK/$(dirname "$rel")"
      SYNC_BACKUP_ROOT="$SYNC_BK"
    fi
    mkdir -p "$SYNC_BACKUP_ROOT/$(dirname "$rel")"
    cp -p "$TARGET/$rel" "$SYNC_BACKUP_ROOT/$rel" 2>/dev/null || true
    rm -f "$TARGET/$rel"
    echo "sync-change-loop: removed stale managed file $rel (backup kept)."
    updated=$(( updated + 1 ))
  fi
done
if [ "$updated" = "0" ]; then
  echo "sync-change-loop: already converged (nothing to update)."
fi
exit 0
