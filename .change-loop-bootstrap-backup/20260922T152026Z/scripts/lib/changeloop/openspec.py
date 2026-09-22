"""changeloop.openspec — the OpenSpec CLI is authoritative for ALL artifacts.

Managed file: change-loop-managed-file. — the OpenSpec CLI is authoritative for ALL artifacts.

Scaffolding, status/schema resolution, templates/instructions, validation,
and lifecycle all resolve through the `openspec` binary. Nothing here creates
change layouts, embeds artifact templates, or hardcodes artifact file names:
artifact paths come from `openspec status --json` at runtime, and task
progress comes from `openspec instructions apply --json` (the CLI's own
task accounting — never artifact-id or file-name matching).

Store selection: the store flag is a PER-SUBCOMMAND flag (verified against
installed help for status/instructions/validate/archive/show/list, and for
the nested `new change` subcommand). It is never passed as a global prefix
ahead of the subcommand — and for `new` it belongs to the nested `change`
subcommand (`openspec new change NAME --store ID`), never to `new` itself.
"""

import json
import os
import re
import subprocess

from .state import LoopError

# Subcommands verified (installed `openspec <cmd> --help`) to accept --store
# directly behind the subcommand word. `new` is deliberately absent: only
# its nested `change` subcommand accepts --store (see _with_store).
_STORE_COMMANDS = frozenset([
    "status", "instructions", "validate", "archive", "show", "list",
])

_STORE_ENV_VAR = "CHANGE_LOOP_STORE"


def store_id():
    return os.environ.get(_STORE_ENV_VAR) or ""


def change_name_ok(name):
    return bool(re.match(r"^[a-z0-9][a-z0-9-]*$", name or ""))


def _with_store(cmd_args):
    """Command args with the selected store flag placed per real CLI help."""
    store = store_id()
    if not store:
        return list(cmd_args)
    if list(cmd_args[:2]) == ["new", "change"]:
        # Nested subcommand: `openspec new change NAME --store ID`
        # (installed `openspec new change --help` places --store here;
        # `openspec new --store ID change NAME` is rejected).
        return list(cmd_args) + ["--store", store]
    if cmd_args and cmd_args[0] in _STORE_COMMANDS:
        return [cmd_args[0], "--store", store] + list(cmd_args[1:])
    return list(cmd_args)


def run_openspec(root, *args):
    """Run openspec in the loop root; return (rc, stdout, stderr)."""
    argv = ["openspec"] + _with_store(args)
    try:
        proc = subprocess.run(argv, cwd=root, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, timeout=120)
    except FileNotFoundError:
        raise LoopError("the 'openspec' CLI is not on PATH.")
    except subprocess.TimeoutExpired:
        raise LoopError("openspec %s timed out." % (args[0] if args else ""))
    return (proc.returncode, proc.stdout.decode("utf-8", "replace"),
            proc.stderr.decode("utf-8", "replace"))


def argv_for(root, *args):
    """The exact argv run_openspec would use (for tests)."""
    return ["openspec"] + _with_store(args)


def change_exists(root, change):
    rc, _out, _err = run_openspec(root, "status", "--change", change, "--json")
    return rc == 0


def new_change(root, change):
    if not change_name_ok(change):
        raise LoopError("malformed change name '%s' (the openspec CLI will "
                        "not accept it)." % change)
    rc, out, err = run_openspec(root, "new", "change", change)
    if rc != 0:
        raise LoopError("openspec new change failed: %s" % (err or out))
    return out


def status_raw(root, change):
    rc, out, err = run_openspec(root, "status", "--change", change, "--json")
    if rc != 0:
        raise LoopError("openspec status failed for '%s': %s"
                        % (change, (err or out).strip()))
    try:
        return json.loads(out)
    except ValueError as exc:
        raise LoopError("openspec status returned invalid JSON: %s" % exc)


def artifact_files(root, change):
    """Exactly the existing artifact paths the CLI reports for this change."""
    status = status_raw(root, change)
    paths = status.get("artifactPaths") or {}
    files = []
    for _artifact_id, info in paths.items():
        if not isinstance(info, dict):
            continue
        for rel in info.get("existingOutputPaths") or []:
            if not isinstance(rel, str) or not rel:
                continue
            if os.path.isabs(rel):
                files.append(os.path.normpath(rel))
            else:
                files.append(os.path.normpath(os.path.join(root, rel)))
    return sorted(set(files))


def artifacts_status(root, change):
    """Map artifact id -> status string from the CLI status envelope."""
    status = status_raw(root, change)
    result = {}
    artifacts = status.get("artifacts") or []
    if isinstance(artifacts, list):
        for entry in artifacts:
            if isinstance(entry, dict) and entry.get("id"):
                result[entry["id"]] = entry.get("status")
    return result


def planning_readiness(root, change):
    """(ready, detail): every CLI-reported artifact done or skipped."""
    statuses = artifacts_status(root, change)
    if not statuses:
        return False, "the CLI reports no artifacts for this change"
    pending = sorted(a for a, s in statuses.items()
                     if s not in ("done", "skipped"))
    if pending:
        return False, ("incomplete planning artifacts: %s"
                       % ", ".join(pending))
    return True, "all %d planning artifact(s) done/skipped" % len(statuses)


def validate_strict(root, change):
    """(ok, output): `openspec validate <change> --strict` acceptance gate."""
    rc, out, err = run_openspec(root, "validate", change, "--strict")
    combined = (out + "\n" + err).strip()
    return rc == 0, combined


def apply_state(root, change):
    """Parsed `openspec instructions apply` JSON for this change.

    Schema-agnostic: progress totals plus the per-task done flags the CLI
    itself derives from the configured schema's task tracking — the loop
    never guesses task files from artifact ids or file names.
    """
    rc, out, err = run_openspec(root, "instructions", "apply",
                                "--change", change, "--json")
    if rc != 0:
        raise LoopError("openspec instructions apply failed for '%s': %s"
                        % (change, (err or out).strip()))
    try:
        return json.loads(out)
    except ValueError as exc:
        raise LoopError("openspec instructions apply returned invalid "
                        "JSON: %s" % exc)


def tasks_completion(root, change):
    """(complete, detail): CLI apply state shows finished work.

    Reads the `tasks` done flags and `progress` counters from `openspec
    instructions apply --json` — the CLI's own accounting for the
    configured schema, whatever its artifact names are. When the schema
    reports no task list at all, the gate does not apply (schema
    evolution must not block approval forever): readiness plus strict
    validation decide.
    """
    try:
        apply = apply_state(root, change)
    except LoopError as exc:
        return False, str(exc)
    tasks = apply.get("tasks")
    if not tasks:
        return True, ("schema reports no task list for this change; "
                      "task gate not applicable")
    progress = apply.get("progress") or {}
    remaining = progress.get("remaining")
    if remaining is None:
        remaining = sum(1 for task in tasks
                        if isinstance(task, dict) and not task.get("done"))
    done = sum(1 for task in tasks
               if isinstance(task, dict) and task.get("done"))
    if remaining:
        return False, ("%d incomplete task(s) remain (%d completed)"
                       % (remaining, done))
    if not done:
        return False, "no completed tasks recorded by the CLI"
    return True, "all %d task(s) completed" % done


def instructions(root, artifact, change):
    rc, out, err = run_openspec(root, "instructions", artifact,
                                "--change", change)
    if rc != 0:
        raise LoopError("openspec instructions failed: %s" % (err or out))
    return out
