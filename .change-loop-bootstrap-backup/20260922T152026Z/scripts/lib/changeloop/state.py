"""changeloop.state — hook-free per-change loop state.

Managed file: change-loop-managed-file. — hook-free per-change loop state.

Layout (repo-local):
  <root>/.change-loop/<change>/state.json        transient roles/step/budget
  <root>/.change-loop/<change>/input-context.md  persisted plan-writer input
  <root>/.change-loop/<change>/lock               fcntl guard (kernel-managed:
                                                  a dead holder releases it)
  <root>/.change-loop-history/                    committable append-only verdicts

Concurrency: every state mutation runs under an exclusive flock held for the
whole action — eligibility checks, provider invocation, and result acceptance
together — so concurrent helpers for one change serialize instead of racing
the budget or overwriting pending invocations. Read-only reports take no
lock; state files are written atomically (tmp + rename), so readers always
see a consistent snapshot.
"""

import errno
import fcntl
import hashlib
import json
import os
import re
import tempfile
from contextlib import contextmanager

from . import (
    EXIT_LOCKED,
    MAX_ROUNDS,
    REVIEW_STEPS,
    VERDICT_STATUSES,
    is_blocking_severity,
)

STATE_DIRNAME = ".change-loop"
HISTORY_DIRNAME = ".change-loop-history"

# Transient content excluded from the code fingerprint (loop bookkeeping,
# migration backups, VCS metadata).
_FINGERPRINT_EXCLUDE_DIRS = frozenset([
    ".git",
    STATE_DIRNAME,
    HISTORY_DIRNAME,
    ".change-loop-migration-backup",
    ".change-loop-bootstrap-backup",
])


class LoopError(Exception):
    """Misuse / misconfiguration (exit 1)."""

    def __init__(self, message, exit_code=1):
        super().__init__(message)
        self.exit_code = exit_code


class LockedError(LoopError):
    """Another run holds the change lock (exit 3)."""

    def __init__(self, message):
        super().__init__(message, exit_code=EXIT_LOCKED)


def default_root():
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.normpath(os.path.join(here, "..", "..", ".."))


def resolve_root(explicit=None):
    return os.path.normpath(explicit or os.environ.get("CHANGE_LOOP_ROOT")
                            or default_root())


def state_dir(root, change):
    return os.path.join(root, STATE_DIRNAME, change)


def state_file(root, change):
    return os.path.join(state_dir(root, change), "state.json")


def input_file(root, change):
    return os.path.join(state_dir(root, change), "input-context.md")


def lock_file(root, change):
    return os.path.join(state_dir(root, change), "lock")


def history_dir(root):
    return os.path.join(root, HISTORY_DIRNAME)


def invocation_file(root, change):
    return os.path.join(state_dir(root, change), "invocation.json")


def live_invocation(root, change):
    """Return the live invocation pgid, or None when safe to invoke.

    The change lock is kernel-managed: a dead helper releases it even
    when its provider tree (its own process group) survives. The
    invocation record bridges that gap — while the recorded process
    group is still alive, a new helper must refuse rather than overlap
    it. A record whose group has exited is stale: removed best-effort,
    then None (proceed).

    Caveat: pgids can be recycled by the kernel, so a refusal names the
    record path for host inspection/removal when it is known stale.
    """
    path = invocation_file(root, change)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    pgid = data.get("pgid") if isinstance(data, dict) else None
    if not isinstance(pgid, int) or isinstance(pgid, bool) or pgid <= 0:
        return None
    # The stored identifier is a PROCESS-GROUP id: probe the group with a
    # negative pgid. os.kill(pgid, 0) would test only the leader PID and
    # miss orphaned descendants (a dead leader with a live grandchild
    # must still refuse overlap). ESRCH means the whole group is gone
    # (stale: remove best-effort, then proceed); EPERM or any other
    # error fails closed (unknown-owner groups still refuse).
    try:
        os.kill(-pgid, 0)
    except ProcessLookupError:
        try:
            os.unlink(path)
        except OSError:
            pass
        return None
    except OSError as exc:
        if exc.errno == errno.ESRCH:
            try:
                os.unlink(path)
            except OSError:
                pass
            return None
        return pgid
    return pgid


def now_iso():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def require_change(root, change):
    if not change:
        raise LoopError("change name required.")
    if not os.path.isfile(state_file(root, change)):
        raise LoopError(
            "no loop state for change '%s' (run 'init' first)." % change)


def load_state(root, change):
    require_change(root, change)
    with open(state_file(root, change), "r", encoding="utf-8") as fh:
        return json.load(fh)


def _atomic_write_json(path, obj):
    tmp_fd, tmp_path = tempfile.mkstemp(dir=os.path.dirname(path),
                                        prefix=".state.")
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, indent=2, sort_keys=True)
            fh.write("\n")
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def save_state(root, change, state):
    state["updatedAt"] = now_iso()
    _atomic_write_json(state_file(root, change), state)


@contextmanager
def locked(root, change):
    """Hold the change lock for the whole action (checks + work + writes)."""
    path = lock_file(root, change)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fh = open(path, "a+b")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        raise LockedError(
            "change '%s' is locked by another run; retry after it "
            "finishes (locks are kernel-managed: a crashed holder "
            "releases automatically)." % change)
    try:
        fh.truncate(0)
        fh.write(str(os.getpid()).encode("ascii"))
        fh.flush()
        yield
    finally:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        finally:
            fh.close()


# --- verdict schema -------------------------------------------------------
# Strict validation for every verdict BEFORE it can affect the loop:
# complete shape, no contradictions. Blocking behavior derives from the
# validated findings, never from the status string alone.

def _finding_error(index, finding):
    if not isinstance(finding, dict):
        return "findings[%d] must be an object" % index
    sev = finding.get("severity")
    if sev not in ("P0", "P1", "P2", "P3"):
        return ("findings[%d].severity must be one of P0|P1|P2|P3 "
                "(got %r)" % (index, sev))
    title = finding.get("title")
    if not isinstance(title, str) or not title.strip():
        return "findings[%d].title must be a non-empty string" % index
    detail = finding.get("detail")
    if not isinstance(detail, str):
        return "findings[%d].detail must be a string" % index
    return None


def validate_verdict(obj):
    """Return (verdict, error). Verdict is the normalized dict or None."""
    if not isinstance(obj, dict):
        return None, "verdict must be a JSON object"
    status = obj.get("status")
    if status not in VERDICT_STATUSES:
        return None, ("verdict status must be one of %s (got %r)"
                      % ("|".join(VERDICT_STATUSES), status))
    findings = obj.get("findings")
    if not isinstance(findings, list):
        return None, "verdict findings must be an array"
    for i, finding in enumerate(findings):
        err = _finding_error(i, finding)
        if err:
            return None, err
    summary = obj.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        return None, "verdict summary must be a non-empty string"
    blocking = [f for f in findings
                if is_blocking_severity(f["severity"])]
    if status == "clean" and blocking:
        return None, ("contradictory verdict: status is clean but carries "
                      "%d P0-P2 finding(s)" % len(blocking))
    if status == "blocking" and not blocking:
        return None, ("contradictory verdict: status is blocking but carries "
                      "no P0-P2 findings")
    if status in ("error", "limit") and findings:
        return None, ("contradictory verdict: status %r must carry no "
                      "findings" % status)
    reviewer = obj.get("reviewer")
    if reviewer is not None and not isinstance(reviewer, dict):
        return None, "verdict reviewer must be an object when present"
    normalized = {
        "status": status,
        "findings": [{"severity": f["severity"], "title": f["title"],
                      "detail": f["detail"]} for f in findings],
        "summary": summary,
    }
    if isinstance(reviewer, dict):
        normalized["reviewer"] = {k: reviewer[k] for k in ("provider", "session")
                                  if isinstance(reviewer.get(k), str)}
    return normalized, None


def verdict_consumes(verdict):
    """A round is consumed only by a validated blocking verdict."""
    return (verdict["status"] == "blocking"
            and any(is_blocking_severity(f["severity"])
                    for f in verdict["findings"]))


# --- fingerprints ----------------------------------------------------------
# Content hashes, never constants. The code fingerprint hashes exactly the
# git-scoped working state — tracked plus untracked-but-not-ignored files
# (`git ls-files -z --cached --others --exclude-standard`, NUL-delimited)
# minus loop bookkeeping — so reviewer test runs cannot poison it with
# ignored churn (`__pycache__`, `.pytest_cache`, `.coverage`, dependency
# trees) the way a raw tree walk does. Design decision 2's throwaway-index
# tree hash, minus the throwaway index: the file set is git's, the bytes
# are the worktree's ("as they stand": staged, unstaged, and untracked
# alike). Both git commands work without HEAD, so unborn repositories hash
# correctly. ANY git failure (missing binary, non-repo, unreadable file)
# raises LoopError: callers treat it as fail-closed, never a reusable
# magic value.

def file_hash(path):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_scoped_files(root):
    """Sorted worktree-relative paths git reports (tracked + others)."""
    import subprocess
    if not os.path.isdir(root):
        raise LoopError("cannot fingerprint: '%s' is not a directory."
                        % root)
    try:
        proc = subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--others",
             "--exclude-standard"],
            cwd=root, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=60)
    except FileNotFoundError:
        raise LoopError("cannot fingerprint workspace: 'git' is not on "
                        "PATH (fail closed).")
    except subprocess.TimeoutExpired:
        raise LoopError("cannot fingerprint workspace: 'git ls-files' "
                        "timed out (fail closed).")
    if proc.returncode != 0:
        raise LoopError(
            "cannot fingerprint workspace: 'git ls-files' failed (%s) "
            "(fail closed)."
            % (proc.stderr.decode("utf-8", "replace").strip()[-300:]))
    rels = [p for p in proc.stdout.decode("utf-8", "surrogateescape")
            .split("\x00") if p]
    scoped = []
    for rel in rels:
        first = rel.split("/", 1)[0]
        if first in _FINGERPRINT_EXCLUDE_DIRS:
            continue
        scoped.append(rel)
    return sorted(scoped)


def code_fingerprint(root):
    """Content fingerprint of the implementation workspace.

    Raises LoopError when the tree cannot be read (fail closed). A
    tracked path missing from the worktree (unstaged deletion) is a
    legitimate content state — represented as a deletion marker — not
    an unreadable-file failure: ordinary deletions must stay
    reviewable. Genuinely unreadable paths (permissions, broken links,
    non-file worktree entries) still fail closed with a distinct error.
    """
    entries = []
    try:
        for rel in _git_scoped_files(root):
            full = os.path.join(root, rel)
            if os.path.islink(full):
                try:
                    target = os.readlink(full)
                except OSError as exc:
                    raise LoopError("cannot fingerprint workspace: '%s': "
                                    "%s (fail closed)." % (rel, exc))
                entries.append(rel + "\x00link:" + target)
            elif os.path.isfile(full):
                entries.append(rel + "\x00" + file_hash(full))
            elif not os.path.lexists(full):
                # Tracked (staged) but deleted from the worktree without
                # `git rm`: represent the deletion instead of rejecting
                # the fingerprint.
                entries.append(rel + "\x00deleted")
            else:
                raise LoopError("cannot fingerprint workspace: '%s' is "
                                "present but not a readable file or link "
                                "(fail closed)." % rel)
    except OSError as exc:
        raise LoopError("cannot fingerprint workspace: %s" % exc)
    digest = hashlib.sha256()
    for entry in entries:
        digest.update(entry.encode("utf-8", "surrogateescape"))
        digest.update(b"\x00")
    return "tree:" + digest.hexdigest()


def plan_fingerprint(root, artifact_files):
    """sha256 over the exact CLI-resolved artifact files and their bytes."""
    digest = hashlib.sha256()
    count = 0
    for path in sorted(set(artifact_files)):
        if not os.path.isfile(path):
            continue
        try:
            digest.update(path.encode("utf-8"))
            digest.update(b"\x00")
            with open(path, "rb") as fh:
                for chunk in iter(lambda: fh.read(65536), b""):
                    digest.update(chunk)
            digest.update(b"\x00")
            count += 1
        except OSError as exc:
            raise LoopError("cannot fingerprint plan artifact '%s': %s"
                            % (path, exc))
    return ("plan:%d:" % count) + digest.hexdigest()


# --- history ----------------------------------------------------------------
# history_record appends one JSON record; never rewrites. Returns the path.
# Every record carries `consumed`: True only when the entry spent a blocking
# round live. Replay counts consumed entries only, so stale, impostor,
# no-pending, and post-exhaustion blocking verdicts (all written with
# consumed=False) never resurrect allowance after state loss.

def history_record(root, change, step, verdict, note, consumed=False):
    os.makedirs(history_dir(root), exist_ok=True)
    prefix = "%s-" % change
    number = 0
    try:
        names = sorted(n for n in os.listdir(history_dir(root))
                       if n.startswith(prefix) and n.endswith(".json"))
    except OSError:
        names = []
    for name in names:
        # Number from this change's own records only: a sibling change
        # whose name shares the prefix (foo vs foo-bar) must neither
        # collide with nor shift this change's numbering.
        try:
            with open(os.path.join(history_dir(root), name), "r",
                      encoding="utf-8") as fh:
                record = json.load(fh)
        except (OSError, ValueError):
            continue
        if isinstance(record, dict) and record.get("change") == change:
            number += 1
    number += 1
    path = os.path.join(history_dir(root), "%s%04d-%s.json"
                        % (prefix, number, step))
    while os.path.exists(path):
        number += 1
        path = os.path.join(history_dir(root), "%s%04d-%s.json"
                            % (prefix, number, step))
    record = {"change": change, "step": step, "verdict": verdict,
              "note": note, "recordedAt": now_iso(),
              "consumed": bool(consumed)}
    _atomic_write_json(path, record)
    return path


def iter_history(root, change):
    """This change's records only, by exact logical identity.

    The filename prefix alone is not identity: change `foo` must never
    replay `foo-bar`'s verdicts, resets, or imports (and vice versa), so
    every record's embedded `change` field must match exactly.
    """
    prefix = "%s-" % change
    try:
        names = sorted(n for n in os.listdir(history_dir(root))
                       if n.startswith(prefix) and n.endswith(".json"))
    except OSError:
        return
    for name in names:
        path = os.path.join(history_dir(root), name)
        try:
            with open(path, "r", encoding="utf-8") as fh:
                record = json.load(fh)
        except (OSError, ValueError):
            continue
        if not isinstance(record, dict) or record.get("change") != change:
            continue
        yield path, record


# --- approvals / freshness ---------------------------------------------------
# A plan-scope edit invalidates plan AND code approvals (code was reviewed
# against superseded requirements). A code-scope edit invalidates code only.
# Committing identical content never invalidates (content hash, not history).

def refresh_approvals(state, plan_fp, code_fp, artifacts_ok=True):
    state["fingerprints"]["plan"] = plan_fp
    state["fingerprints"]["code"] = code_fp
    if not artifacts_ok:
        # Referenced input artifacts changed out from under the approval.
        state["approvals"]["plan"] = None
        state["approvals"]["code"] = None
        state["approvals"]["codePlan"] = None
        return "input-artifacts"
    if (state["approvals"].get("plan") is not None
            and state["approvals"].get("plan") != plan_fp):
        state["approvals"]["plan"] = None
        state["approvals"]["code"] = None
        state["approvals"]["codePlan"] = None
        return "plan"
    if (state["approvals"].get("code") is not None
            and state["approvals"].get("code") != code_fp):
        state["approvals"]["code"] = None
        state["approvals"]["codePlan"] = None
        return "code"
    return None


# --- budget recovery ---------------------------------------------------------
# The durable budget is the maximum over every source: live state, committed
# verdict history replay, migration-imported budgets, and old-gate files.
# Deleting transient state (or checking out fresh) never restores allowance.

# Notes marking legacy (pre-`consumed`-flag) history entries that spent
# nothing live. New records do not need these: they carry consumed=False.
_LEGACY_NON_CONSUMING_NOTES = (
    "budget unchanged",
    "history only",
    "no state change",
    "stale verdict",
    "without a matching pending",
    "does not match",
    "post-exhaustion",
    "rejected verdict",
    "incomplete handoff",
)


def _replay_history_consumed(root, change):
    """Reconstruct (consumed, saw_reset) from append-only history records.

    Replay is ordered: a reset zeroes the running count, so only imports
    and consumed verdicts recorded AFTER the last reset count. The reset
    therefore bounds every historical budget source, not just history
    replay (see recover_budget).
    """
    consumed = 0
    saw_reset = False
    for _path, record in iter_history(root, change):
        step = record.get("step")
        note = record.get("note") or ""
        verdict = record.get("verdict") or {}
        if step == "budget" and "budget reset" in note:
            consumed = 0
            saw_reset = True
        elif step == "budget" and "imported budget provenance" in note:
            match = re.search(r"(\d+) consumed", note)
            if match:
                consumed = max(consumed, int(match.group(1)))
        elif step in REVIEW_STEPS and verdict.get("status") == "blocking":
            if "consumed" in record:
                if record.get("consumed") is True:
                    consumed += 1
            elif not any(marker in note
                         for marker in _LEGACY_NON_CONSUMING_NOTES):
                consumed += 1
    return consumed, saw_reset


def _old_gate_consumed(root, change):
    """Best-effort read of pre-migration gate budgets for this change.

    Observed wire format (real consumer):
      {"version": 1, "change": "<name>", "used": N, "allowance": 5, ...}
    Only files naming this change count; anything else is ignored.
    """
    best = 0
    sources = []
    candidates = []
    claude_dir = os.path.join(root, ".claude")
    try:
        names = os.listdir(claude_dir)
    except OSError:
        names = []
    for name in names:
        if name.startswith(".review-gate-") and name.endswith(".json"):
            candidates.append(os.path.join(claude_dir, name))
    for path in sorted(candidates):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        if data.get("change") != change:
            continue
        used = data.get("used")
        if isinstance(used, bool):
            continue
        if isinstance(used, int) and 0 <= used <= MAX_ROUNDS:
            if used > best:
                best = used
            sources.append("%s (used=%d)" % (path, used))
    return best, sources


def _migrated_budget(root, change):
    """Budget snapshot written by migration apply (transient, post-migration)."""
    path = os.path.join(root, STATE_DIRNAME, "migrated-budget.json")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return 0, []
    if not isinstance(data, dict):
        return 0, []
    changes = data.get("changes")
    if isinstance(changes, dict):
        value = changes.get(change, 0)
    else:
        value = data.get("consumed", 0)
    if isinstance(value, int) and 0 < value <= MAX_ROUNDS:
        return value, ["%s (change %s: %d)" % (path, change, value)]
    return 0, []


def recover_budget(root, change):
    """Return (consumed, provenance_notes) from all durable sources.

    An explicit reset in the replayed history bounds EVERY historical
    source: old-gate files and migration snapshots predate the loop's
    own accounting, so once a reset record exists only post-reset
    history counts. Losing transient state (or cloning fresh) can
    therefore never resurrect pre-reset rounds. Post-reset imports
    always leave ordered history records of their own, so they still
    count.
    """
    history_consumed, saw_reset = _replay_history_consumed(root, change)
    notes = []
    if history_consumed:
        notes.append("committed verdict history replays %d consumed round(s)"
                     % history_consumed)
    if saw_reset:
        notes.append("explicit reset bounds all pre-reset budget sources")
        return min(history_consumed, MAX_ROUNDS), notes
    old_consumed, old_sources = _old_gate_consumed(root, change)
    mig_consumed, mig_sources = _migrated_budget(root, change)
    consumed = max(history_consumed, old_consumed, mig_consumed)
    for source in old_sources:
        notes.append("old-gate budget %s" % source)
    for source in mig_sources:
        notes.append("migration-imported budget %s" % source)
    return min(consumed, MAX_ROUNDS), notes


def blank_state(change, roles, models=None, consumed=0, provenance=None,
                efforts=None):
    return {
        "change": change,
        "roles": dict(roles),
        # Explicit model per role; "" means unselected (invocation refuses
        # before running until a model is mapped or --model is passed).
        "models": dict(models or {}),
        # Explicit reasoning effort per role; "" resolves per invocation
        # to reviewers medium / authors high (recorded, never an ambient
        # CLI default) and binds session identity with model.
        "efforts": dict(efforts or {}),
        "step": "plan",
        # After an error/limit handoff, only this action may resume.
        "resumeStep": None,
        "consumed": consumed,
        "maxRounds": MAX_ROUNDS,
        "exhausted": consumed >= MAX_ROUNDS,
        "fingerprints": {"plan": None, "code": None, "codePlan": None},
        "approvals": {"plan": None, "code": None, "codePlan": None},
        "sessions": {},
        "pending": None,
        "inputArtifacts": {},
        # Revision of the plan-writer input context: bumped by every
        # substantive persist-input, stamped into review pendings, and
        # checked at acceptance — a verdict recovered against superseded
        # input can never approve.
        "inputRevision": 0,
        "lastFindings": [],
        # Cumulative per-invocation token totals (kept SEPARATE from the
        # blocking-verdict budget above). Per-invocation records append to
        # usage.jsonl next to state.json; usageByProvider aggregates the
        # same records per provider/model for status reporting.
        # Partial availability survives aggregation: `known` counts
        # records with ANY counter, `complete` those with every headline
        # counter, `partial` the known-but-incomplete remainder, and each
        # summed counter carries its own reporter count (`<key>_known`)
        # so status can print known subtotals and completeness instead
        # of fabricated zeros.
        "usageTotals": {"invocations": 0, "known": 0, "complete": 0,
                        "partial": 0, "unknown": 0,
                        "input_total": 0, "input_total_known": 0,
                        "output": 0, "output_known": 0,
                        "total": 0, "total_known": 0,
                        "cost_usd": 0.0, "cost_known": 0},
        "usageByProvider": {},
        "provenance": list(provenance or []),
        "createdAt": now_iso(),
        "updatedAt": now_iso(),
    }


def normalize_state(state):
    """Forward-fill new keys; pin the fixed budget; normalize exhaustion."""
    state.setdefault("sessions", {})
    state.setdefault("pending", None)
    state.setdefault("inputArtifacts", {})
    state.setdefault("lastFindings", [])
    state.setdefault("provenance", [])
    state.setdefault("models", {})
    state.setdefault("efforts", {})
    state.setdefault("resumeStep", None)
    if not isinstance(state.get("inputRevision"), int):
        state["inputRevision"] = 0
    state.setdefault("usageTotals", {"invocations": 0, "known": 0,
                                     "complete": 0, "partial": 0,
                                     "unknown": 0, "input_total": 0,
                                     "input_total_known": 0,
                                     "output": 0, "output_known": 0,
                                     "total": 0, "total_known": 0,
                                     "cost_usd": 0.0, "cost_known": 0})
    if not isinstance(state.get("usageByProvider"), dict):
        state["usageByProvider"] = {}
    for key in ("planWriter", "planReviewer", "coder", "codeReviewer"):
        state["models"].setdefault(key, "")
        if not isinstance(state["efforts"], dict):
            state["efforts"] = {}
        state["efforts"].setdefault(key, "")
    totals = state["usageTotals"]
    for key, default in (("invocations", 0), ("known", 0), ("complete", 0),
                         ("partial", 0), ("unknown", 0),
                         ("input_total", 0), ("input_total_known", 0),
                         ("output", 0), ("output_known", 0),
                         ("total", 0), ("total_known", 0),
                         ("cost_usd", 0.0), ("cost_known", 0)):
        totals.setdefault(key, default)
    for entry in state["usageByProvider"].values():
        if not isinstance(entry, dict):
            continue
        for key, default in (("invocations", 0), ("known", 0),
                             ("complete", 0), ("partial", 0),
                             ("unknown", 0), ("input_total", 0),
                             ("input_total_known", 0),
                             ("output", 0), ("output_known", 0),
                             ("total", 0), ("total_known", 0),
                             ("cost_usd", 0.0), ("cost_known", 0)):
            entry.setdefault(key, default)
    state.setdefault("fingerprints", {"plan": None, "code": None,
                                      "codePlan": None})
    state.setdefault("approvals", {"plan": None, "code": None,
                                   "codePlan": None})
    state["maxRounds"] = MAX_ROUNDS
    if not isinstance(state.get("consumed"), int):
        state["consumed"] = 0
    state["consumed"] = max(0, min(state["consumed"], MAX_ROUNDS))
    if state["consumed"] >= MAX_ROUNDS:
        state["exhausted"] = True
    state.setdefault("exhausted", False)
    if state.get("step") not in ("plan", "plan-review", "code",
                                 "code-review", "done", "handoff"):
        state["step"] = "plan"
    if state.get("step") != "handoff":
        state["resumeStep"] = None
    return state


# --- token usage ------------------------------------------------------------
# Per-invocation usage records append to usage.jsonl (one JSON object per
# line); cumulative totals live in state["usageTotals"]. Both are durable
# and entirely separate from the blocking-verdict budget (`consumed`).

def usage_file(root, change):
    return os.path.join(state_dir(root, change), "usage.jsonl")


def append_usage(root, change, state, record):
    """Persist one invocation's usage; update cumulative totals.

    Only the CURRENT invocation's output file is ever parsed (callers pass
    one out_path per invocation), so resumed sessions never double-count
    earlier rounds: each record is one delta, added once here to both the
    global totals and the per-provider/model aggregates.
    """
    with open(usage_file(root, change), "a", encoding="utf-8") as fh:
        fh.write(json.dumps(dict(record, recordedAt=now_iso()),
                            sort_keys=True) + "\n")
    totals = state.setdefault("usageTotals", {})
    totals["invocations"] = totals.get("invocations", 0) + 1
    provider = record.get("provider") or "?"
    model = record.get("model") or "?"
    by = state.setdefault("usageByProvider", {})
    entry = by.setdefault("%s|%s" % (provider, model),
                         {"invocations": 0, "known": 0, "complete": 0,
                          "partial": 0, "unknown": 0,
                          "input_total": 0, "input_total_known": 0,
                          "output": 0, "output_known": 0,
                          "total": 0, "total_known": 0,
                          "cost_usd": 0.0, "cost_known": 0})
    entry["invocations"] = entry.get("invocations", 0) + 1
    if record.get("known"):
        # A partially known invocation counts as known AND partial —
        # never as wholly known. Every summed counter carries its own
        # reporter count, so unreported fields stay unknown downstream
        # instead of collapsing into a fabricated zero.
        totals["known"] = totals.get("known", 0) + 1
        entry["known"] = entry.get("known", 0) + 1
        if record.get("complete"):
            totals["complete"] = totals.get("complete", 0) + 1
            entry["complete"] = entry.get("complete", 0) + 1
        else:
            totals["partial"] = totals.get("partial", 0) + 1
            entry["partial"] = entry.get("partial", 0) + 1
        for key in ("input_total", "output", "total"):
            if isinstance(record.get(key), int):
                totals[key] = totals.get(key, 0) + record[key]
                entry[key] = entry.get(key, 0) + record[key]
                totals[key + "_known"] = totals.get(key + "_known", 0) + 1
                entry[key + "_known"] = entry.get(key + "_known", 0) + 1
        if isinstance(record.get("cost_usd"), (int, float)):
            totals["cost_usd"] = totals.get("cost_usd", 0.0) \
                + record["cost_usd"]
            entry["cost_usd"] = entry.get("cost_usd", 0.0) \
                + record["cost_usd"]
            totals["cost_known"] = totals.get("cost_known", 0) + 1
            entry["cost_known"] = entry.get("cost_known", 0) + 1
    else:
        totals["unknown"] = totals.get("unknown", 0) + 1
        entry["unknown"] = entry.get("unknown", 0) + 1
