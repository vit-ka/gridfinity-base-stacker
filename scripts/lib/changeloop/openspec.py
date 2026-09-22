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

import glob as _glob
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


def usable_artifact_files(root, change):
    """Existing artifact files carrying non-whitespace content.

    Schema-agnostic: candidates come only from `openspec status --json`
    (see artifact_files above) — no artifact names are matched here. A
    file counts when it holds non-whitespace bytes, so zero-byte files
    never unlock invocation. Documented assumption: the CLI scaffolds
    no artifact files for a fresh change, so presence-plus-non-empty
    implies real content. CLI resolution failures propagate to the
    caller (reported as errors, never as missing input); unreadable
    files simply do not count.
    """
    usable = []
    for path in artifact_files(root, change):
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                content = fh.read()
        except OSError:
            continue
        if content.strip():
            usable.append(path)
    return usable


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


# --- full role context -------------------------------------------------------
# Shared read-only context assembly for every role invocation. Each helper
# run rebuilds the bundle live from the CLI in the selected planning scope:
# status for identity/scope/artifact inventory, per-artifact instructions
# for planning roles, apply instructions for implementation roles, plus the
# complete contents of every reported existing file and relevant
# instruction-returned file. Nothing is guessed: artifact identifiers and
# file paths come from CLI responses, relative paths resolve against the
# CLI-declared change directory, and the selected store flag rides on every
# subcommand through run_openspec. Any required resolution failure,
# malformed response, or unreadable reported file raises LoopError naming
# the command or path — callers record an incomplete handoff and never
# invoke a provider on partial context. Expected absent prerequisites the
# CLI reports explicitly (unfinished dependencies, empty file sets, a
# blocked apply state) are planning state, not errors, and stay in the
# bundle exactly as reported.

_GLOB_CHARS = ("*", "?", "[")

_STATUS_META_KEYS = ("changeName", "schemaName", "changeDir", "changeRoot",
                     "planningHome", "actionContext", "artifacts", "root")

_INSTRUCTION_META_KEYS = ("dependencies", "unlocks", "outputPath",
                          "resolvedOutputPath", "existingOutputPaths",
                          "changeDir", "planningHome", "root")


def _read_context_file(path):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError as exc:
        raise LoopError("cannot read context file '%s': %s" % (path, exc))


def _resolve_reported_path(rel, change_dir, root):
    """CLI path semantics: absolute paths verbatim, relative ones against
    the CLI-declared change directory (loop root when undeclared)."""
    if os.path.isabs(rel):
        return os.path.normpath(rel)
    base = change_dir or root
    return os.path.normpath(os.path.join(base, rel))


def _expand_reported_path(resolved):
    """Concrete paths pass through; pattern paths expand to existing files
    only (a pattern with no hits is uncreated content, not an error)."""
    if any(mark in resolved for mark in _GLOB_CHARS):
        try:
            hits = _glob.glob(resolved, recursive=True)
        except Exception:
            return []
        return sorted(path for path in
                      (os.path.normpath(hit) for hit in hits)
                      if os.path.isfile(path))
    return [resolved]


def _require_envelope(data, command, change):
    """Reject non-dict responses and the CLI's error envelope (zero exit
    but no change identity) as malformed context."""
    if not isinstance(data, dict) or not data.get("changeName"):
        raise LoopError("%s returned an unexpected response for '%s' "
                        "(missing change identity)." % (command, change))
    return data


def artifact_instruction_state(root, artifact_id, change):
    """Parsed `openspec instructions <artifact> --json` for one artifact.

    Raises LoopError naming the command on failure, non-JSON output, or a
    response without change identity.
    """
    command = "openspec instructions %s" % artifact_id
    rc, out, err = run_openspec(root, "instructions", artifact_id,
                                "--change", change, "--json")
    if rc != 0:
        raise LoopError("%s failed for '%s': %s"
                        % (command, change, (err or out).strip()))
    try:
        data = json.loads(out)
    except ValueError as exc:
        raise LoopError("%s returned invalid JSON: %s" % (command, exc))
    return _require_envelope(data, command, change)


def _active_artifact_ids(status):
    """Non-skipped artifact ids from status, falling back to the reported
    path inventory when the CLI lists no artifact entries."""
    ids = []
    seen = set()
    entries = status.get("artifacts")
    if isinstance(entries, list):
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            ident = entry.get("id")
            if not ident or ident in seen:
                continue
            if entry.get("status") == "skipped":
                continue
            seen.add(ident)
            ids.append(ident)
    if not ids:
        paths = status.get("artifactPaths")
        if isinstance(paths, dict):
            for ident in paths:
                if ident and ident not in seen:
                    seen.add(ident)
                    ids.append(ident)
    return ids


def _change_dir(status):
    """CLI-declared change directory for relative-path semantics."""
    return status.get("changeRoot") or ""


def _strict_path_list(value, command, change, entry, what):
    """Validate a CLI path list; malformed shapes raise, empties drop out.

    Absent (None) is uncreated content, not an error; a present value of
    the wrong shape, or a non-string entry, is malformed context that the
    caller must surface instead of silently omitting."""
    if value is None:
        return []
    if not isinstance(value, list):
        raise LoopError("%s returned a malformed %s in entry '%s' for "
                        "'%s' (expected a path list)."
                        % (command, what, entry, change))
    for rel in value:
        if not isinstance(rel, str):
            raise LoopError("%s returned a malformed path in %s entry "
                            "'%s' for '%s' (expected a path string)."
                            % (command, what, entry, change))
    return [rel for rel in value if rel]


def _strict_dependencies(value, command, change, entry):
    """Validate a CLI dependency list; malformed shapes raise."""
    if value is None:
        return []
    if not isinstance(value, list):
        raise LoopError("%s returned a malformed dependency list in entry "
                        "'%s' for '%s' (expected a list)."
                        % (command, entry, change))
    for dep in value:
        if not isinstance(dep, dict):
            raise LoopError("%s returned a malformed dependency entry in "
                            "'%s' for '%s' (expected an object)."
                            % (command, entry, change))
    return value


def _collect_context_mapping(mapping, ident, change_dir, root, command,
                             change, collected):
    """Collect concrete context files from a CLI context-file mapping.

    Accepts the mapping shape (entry id -> path list) and the bare path
    list shape; a wrong-shaped mapping, entry, or path raises instead of
    silently dropping required context."""
    if mapping is None:
        return
    if isinstance(mapping, list):
        items = [(ident, mapping)]
    elif isinstance(mapping, dict):
        items = list(mapping.items())
    else:
        raise LoopError("%s returned a malformed context-file mapping for "
                        "'%s' (expected a path list mapping)."
                        % (command, change))
    for key, rels in items:
        label = key or ident or "apply"
        for rel in _strict_path_list(rels, command, change, label,
                                     "context-file entry"):
            collected.append(("context:%s" % label,
                              _resolve_reported_path(rel, change_dir,
                                                     root)))


def _status_existing_files(status, root, change):
    """(artifact id, resolved path) for every CLI-reported existing file.

    Mirrors artifact_files resolution (absolute verbatim, relative against
    the loop root) so prompt contents and fingerprints never diverge. A
    malformed file inventory raises naming the status command."""
    command = "openspec status"
    found = []
    paths = status.get("artifactPaths")
    if paths is None:
        return found
    if not isinstance(paths, dict):
        raise LoopError("%s returned a malformed file inventory for '%s' "
                        "(artifact paths are not a mapping)."
                        % (command, change))
    for ident, info in paths.items():
        if not isinstance(info, dict):
            raise LoopError("%s returned a malformed file inventory entry "
                            "'%s' for '%s' (expected an object)."
                            % (command, ident, change))
        for rel in _strict_path_list(info.get("existingOutputPaths"),
                                     command, change, ident,
                                     "existing-file list"):
            if os.path.isabs(rel):
                found.append((ident, os.path.normpath(rel)))
            else:
                found.append((ident, os.path.normpath(
                    os.path.join(root, rel))))
    return found


def _bundle_files(collected):
    """Read each collected (role, path) once; deduplicate by resolved path
    while keeping every role association for the label."""
    ordered = {}
    for role, path in collected:
        entry = ordered.setdefault(path, {"path": path, "roles": []})
        if role not in entry["roles"]:
            entry["roles"].append(role)
    files = []
    for path in sorted(ordered):
        entry = ordered[path]
        entry["content"] = _read_context_file(path)
        entry["roles"] = sorted(entry["roles"])
        files.append(entry)
    return files


def resolve_planning_context(root, change):
    """Full planning bundle: status plus instructions for every non-skipped
    artifact, completed dependency files, instruction context files, and
    all reported existing files with complete contents. Blocked artifacts
    keep their instructions and missing-dependency information; failed
    resolution, malformed responses, or unreadable reported files raise."""
    status = status_raw(root, change)
    _require_envelope(status, "openspec status", change)
    collected = [("existing:%s" % ident, path)
                 for ident, path in
                 _status_existing_files(status, root, change)]
    details = []
    for ident in _active_artifact_ids(status):
        command = "openspec instructions %s" % ident
        data = artifact_instruction_state(root, ident, change)
        details.append(data)
        change_dir = (data.get("changeDir") or _change_dir(status)
                      or root)
        for rel in _strict_path_list(data.get("existingOutputPaths"),
                                     command, change, ident,
                                     "existing-file list"):
            collected.append(
                ("existing:%s" % ident,
                 _resolve_reported_path(rel, change_dir, root)))
        for dep in _strict_dependencies(data.get("dependencies"), command,
                                        change, ident):
            if not dep.get("done"):
                continue
            rel = dep.get("path")
            if not isinstance(rel, str) or not rel:
                raise LoopError("%s returned a completed dependency without "
                                "a path in entry '%s' for '%s'."
                                % (command, dep.get("id"), change))
            base = _resolve_reported_path(rel, change_dir, root)
            for hit in _expand_reported_path(base):
                collected.append(("dependency:%s" % dep.get("id"), hit))
        _collect_context_mapping(data.get("contextFiles"), ident,
                                 change_dir, root, command, change,
                                 collected)
    return {"change": change, "store": store_id(), "status": status,
            "artifact_instructions": details,
            "files": _bundle_files(collected)}


def resolve_apply_context(root, change):
    """Full implementation bundle: status plus apply instructions, every
    file from the status inventory and the apply context-file mapping with
    complete contents, and the CLI's tasks/progress/state/project inputs.
    A blocked or all-done apply state is authoritative content, not an
    error; failed resolution, malformed responses, or unreadable reported
    files raise."""
    status = status_raw(root, change)
    _require_envelope(status, "openspec status", change)
    apply = apply_state(root, change)
    _require_envelope(apply, "openspec instructions apply", change)
    change_dir = (apply.get("changeDir") or _change_dir(status) or root)
    collected = [("existing:%s" % ident, path)
                 for ident, path in
                 _status_existing_files(status, root, change)]
    _collect_context_mapping(apply.get("contextFiles"), "", change_dir,
                             root, "openspec instructions apply", change,
                             collected)
    return {"change": change, "store": store_id(), "status": status,
            "apply": apply, "files": _bundle_files(collected)}


def followup_commands(kind, change, artifact_ids=()):
    """Exact follow-up CLI commands for this scope (store placed per real
    CLI help, behind the subcommand)."""
    cmds = [" ".join(argv_for(None, "status", "--change", change, "--json"))]
    if kind == "apply":
        cmds.append(" ".join(argv_for(None, "instructions", "apply",
                                     "--change", change, "--json")))
    else:
        for ident in artifact_ids:
            cmds.append(" ".join(argv_for(None, "instructions", ident,
                                         "--change", change, "--json")))
    cmds.append(" ".join(argv_for(None, "validate", change, "--strict")))
    return cmds


def _format_metadata_block(status):
    subset = {key: status[key] for key in _STATUS_META_KEYS if key in status}
    return json.dumps(subset, indent=2, sort_keys=True)


def _format_extra_fields(data, known):
    extra = {key: value for key, value in data.items() if key not in known}
    if not extra:
        return ""
    return ("Additional CLI fields (project context, rules, guidance, or "
            "scope metadata — consider for applicability):\n%s"
            % json.dumps(extra, indent=2, sort_keys=True))


def _format_instruction_detail(data):
    lines = []
    ident = data.get("artifactId")
    lines.append("Artifact '%s'%s"
                 % (ident, (": %s" % data.get("description"))
                    if data.get("description") else ""))
    if data.get("instruction"):
        lines.append("Instruction:\n%s" % data["instruction"])
    if data.get("template"):
        lines.append("Template:\n%s" % data["template"])
    for key in _INSTRUCTION_META_KEYS:
        if key in data:
            value = data[key]
            lines.append("%s: %s" % (key, value if isinstance(value, str)
                                     else json.dumps(value, sort_keys=True)))
    known = set(_INSTRUCTION_META_KEYS) | {"artifactId", "description",
                                           "instruction", "template"}
    extra = _format_extra_fields(data, known)
    if extra:
        lines.append(extra)
    return "\n".join(lines)


def _format_apply_detail(apply):
    lines = []
    for key in ("changeName", "schemaName", "changeDir", "state"):
        if key in apply:
            lines.append("%s: %s" % (key, apply[key]))
    if apply.get("instruction"):
        lines.append("Instruction:\n%s" % apply["instruction"])
    if apply.get("progress") is not None:
        lines.append("Progress: %s"
                     % json.dumps(apply["progress"], sort_keys=True))
    tasks = apply.get("tasks")
    if isinstance(tasks, list) and tasks:
        lines.append("Tasks (CLI accounting — the only completion record):")
        for task in tasks:
            if isinstance(task, dict):
                lines.append("- [%s] %s %s"
                             % ("x" if task.get("done") else " ",
                                task.get("id"), task.get("description")))
    for key in ("missingArtifacts", "contextFiles", "root"):
        if key in apply:
            value = apply[key]
            lines.append("%s: %s" % (key, value if isinstance(value, str)
                                     else json.dumps(value, sort_keys=True)))
    for key in ("context", "operationGuidance"):
        if key in apply and apply[key] not in (None, "", [], {}):
            value = apply[key]
            lines.append("%s (required project input"
                         " — read and apply where relevant):\n%s"
                         % (key, value if isinstance(value, str)
                            else json.dumps(value, indent=2, sort_keys=True))
                         if key == "context" else
                         "%s (advisory — follow applicable entries, report "
                         "conflicts with controlling inputs):\n%s"
                         % (key, value if isinstance(value, str)
                            else json.dumps(value, indent=2, sort_keys=True)))
    known = {"changeName", "schemaName", "changeDir", "state", "instruction",
             "progress", "tasks", "missingArtifacts", "contextFiles", "root",
             "context", "operationGuidance"}
    extra = _format_extra_fields(apply, known)
    if extra:
        lines.append(extra)
    return "\n".join(lines)


def _format_files_block(files):
    lines = ["Complete file contents (%d file(s), deduplicated by resolved "
             "path — full text, never truncated or summarized):"
             % len(files)]
    if not files:
        lines.append("  (no CLI-reported files to materialize)")
    for entry in files:
        lines.append("--- FILE %s (via %s) ---"
                     % (entry["path"], ", ".join(entry["roles"])))
        content = entry["content"]
        lines.append(content if content.strip() else "(empty file)")
    return "\n".join(lines)


def format_planning_block(ctx):
    """Render the planning bundle: metadata, per-artifact instructions with
    follow-ups, then every complete file."""
    status = ctx["status"]
    ids = [data.get("artifactId") for data in
           ctx["artifact_instructions"] if isinstance(data, dict)]
    parts = [
        "Full change context for '%s' (planning scope %s, store %s) — "
        "rebuilt live for this invocation."
        % (ctx["change"], status.get("schemaName"),
           ctx["store"] or "(default OpenSpec root)"),
        "Status metadata:\n%s" % _format_metadata_block(status),
        "Follow-up commands in this scope:\n%s"
        % "\n".join("  %s" % cmd for cmd in
                    followup_commands("planning", ctx["change"], ids)),
    ]
    for data in ctx["artifact_instructions"]:
        parts.append(_format_instruction_detail(data))
    parts.append(_format_files_block(ctx["files"]))
    return "\n\n".join(parts)


def format_apply_block(ctx):
    """Render the implementation bundle: metadata, apply state, then every
    complete file."""
    status = ctx["status"]
    parts = [
        "Full change context for '%s' (planning scope %s, store %s) — "
        "rebuilt live for this invocation."
        % (ctx["change"], status.get("schemaName"),
           ctx["store"] or "(default OpenSpec root)"),
        "Status metadata:\n%s" % _format_metadata_block(status),
        "Follow-up commands in this scope:\n%s"
        % "\n".join("  %s" % cmd for cmd in
                    followup_commands("apply", ctx["change"])),
        "Apply state:\n%s" % _format_apply_detail(ctx["apply"]),
        _format_files_block(ctx["files"]),
    ]
    return "\n\n".join(parts)
