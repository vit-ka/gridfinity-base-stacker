"""changeloop.providers — one explicit adapter per supported provider.

Managed file: change-loop-managed-file. — one explicit adapter per supported provider.

Verified against installed binaries (observed --help + old-gate production):
  muse   — `muse exec --json --prompt-file P --workspace ROOT
             --session-id UUID [--model M] --reasoning-effort E` (model
             on fresh only; resume reuses the same UUID and keeps its
             model; effort is explicit on EVERY invocation — reviewers
             and authors medium unless `--effort` overrides). Authors
             pass `--approval-mode never` on fresh and resume invocations
             so headless runs cannot wait for interactive approval, with
             the sandbox kept on (no `--yolo`, `--disable-sandbox`, or
             `--trust-workspace`). Reviewers add `--disable-write`
             AND `--disable-shell` (`--disable-write` alone still permits
             shell writes). Workspace skills/rules load only with
             `--trust-workspace`, which the loop never passes.
  codex  — `codex exec -m M --cd ROOT --json PROMPT`; authors add
             `-s workspace-write`, reviewers add `-s read-only`.
             Every path passes `-c features.hooks=false
             -c approval_policy="never"` (hook-disabled, never approve)
             plus `-c model_reasoning_effort="E"` (explicit reasoning
             effort on EVERY invocation — all roles medium
             unless `--effort` overrides; a config override because
             `codex exec` exposes no `--effort` flag).
             Resume via `codex exec resume SID -m M --json PROMPT` plus
             `-c sandbox_mode="<role>"` (`-s`/`--cd` are fresh-only
             flags, so resume pins the role's sandbox via config:
             authors `workspace-write` (a resumed author must keep the
             write authority it needs to apply review fixes), reviewers
             `read-only` (otherwise a resumed review inherits the user's
             config policy)) and the same `model_reasoning_effort`
             override. JSONL
             events carry `thread_id` (`thread.started` / `thread_id`);
             each `turn.completed` event carries a `usage` object whose
             `input_tokens` INCLUDE cached tokens.
  claude — `claude -p --output-format json --model M --effort E` with
             the prompt on STDIN (live-probed: `--allowedTools`/
             `--disallowedTools` are variadic and swallow any positional
             prompt with "Input must be provided ... when using --print";
             stdin is the old gate's channel too, on fresh AND resume).
             The result is a single `{"type":"result",...}`
             envelope with `session_id`, the verdict JSON in `result`,
             and a `usage` object whose `input_tokens` EXCLUDE cache
             (`cache_creation_input_tokens` + `cache_read_input_tokens`
             are separate). Fresh rounds pass `--session-id UUID`;
             matching-identity follow-ups pass `--resume UUID` in the same
             workspace (every invocation runs with cwd=ROOT). A resume the
             CLI cannot find answers "No conversation found": that is
             recorded as an actionable incomplete handoff with the stored
             session preserved (fail closed — never a silent fresh
             session, never a fallback provider); pass `--fresh` to
             deliberately mint a new session. Live-probed on this build:
             `claude -p` sessions are NOT persisted anywhere under
             ~/.claude, so resuming a `-p` session currently hands off
             every follow-up round — re-probe resume after a CLI upgrade.
             Authors add `--safe-mode` (hook activity explicitly disabled:
             it turns off hooks/skills/plugins while built-in tools and
             permissions keep working, so an author inside an old-gate
             consumer never executes its hooks) + `--permission-mode
             acceptEdits` (a real --permission-mode choice, NOT a bypass
             flag) + `--allowedTools` covering read/search/edit plus
             scoped shell for coding, tests, and builds (`Read Grep Glob
             Edit Write` and `Bash(` scoped to `openspec:`, `git:`,
             `python:`, `python3:`, `pytest:`, `bash:`, `sh:`, `make:`,
             `node:`, `npm:`, `npx:` — headless test/build commands work
             without any bypass or skip-permissions flag). Reviewers add
             `--safe-mode` + `--permission-mode plan` + `--allowedTools
             "Read Grep Glob Bash(git diff:*) Bash(git status:*)
             Bash(git log:*) Bash(git show:*) Bash(git ls-files:*)"`
             (the old gate's production read-only list, live-probed working
             via stdin) + `--disallowedTools "Write Edit NotebookEdit"`.

Explicit models: every verified-provider invocation requires a non-empty
model id (no silent CLI defaults, no fallback). Explicit effort: every
verified-provider invocation carries an explicit reasoning-effort level
(muse `--reasoning-effort`, codex `-c model_reasoning_effort=`, claude
`--effort`) — all roles medium, each overridable via the
helper's `--effort` flag; reviewer effort NEVER falls through to an
ambient CLI default. Usage: per-invocation
counters are extracted from the provider's REAL output file
(`extract_usage`); muse's observed `--json` schema carries no usage
counters, so muse usage is honestly reported UNKNOWN (never zero).
Declared-only (no verified contract; selection refuses until verified):
  grok, gemini.

Every provider child runs with OPENSPEC_TELEMETRY=0 and
OPENSPEC_NO_UPDATE_CHECK=1 so OpenSpec telemetry/update requests cannot
stall under sandbox proxies; the helper's own environment is unchanged.

Fixture driver: CHANGE_LOOP_DRIVER=fixture + CHANGE_LOOP_FIXTURE_DIR=<dir>
runs fully offline:
  <dir>/<provider>.present          exists => binary considered present
  <dir>/<provider>.<mode>.out      raw provider stdout for fresh|resume
  <dir>/<provider>.<mode>.session  provider-issued session id (else .session)
  <dir>/<provider>.<mode>.rc       simulated exit code (default 0)
  <dir>/<provider>.<mode>.err      simulated stderr
  invocations.log gets one JSON line per call (argv/cwd/role/prompt bytes);
  invoked-<provider>.<mode>.prompt keeps the delivered prompt for assertions.
"""

import errno
import json
import os
import re
import shutil
import signal
import subprocess
import uuid

from . import PROVIDERS_ALL, PROVIDERS_DECLARED, PROVIDERS_VERIFIED
from .state import LoopError, validate_verdict

REVIEWER = "reviewer"
AUTHOR = "author"

# Required help-surface flags per provider (version-drift proof: the exact
# flags the adapters rely on must be present in the installed binary).
# Codex reasoning effort rides on `-c model_reasoning_effort=` (a config
# override — `codex exec` exposes no `--effort` flag — so `-c/--config`
# in the resume contract plus `codex exec --help` is the checkable
# surface for it).
_CONTRACT_FLAGS = {
    "muse": (["muse", "exec", "--help"],
             ["--session-id", "--prompt-file", "--json", "--workspace",
              "--disable-write", "--disable-shell", "--reasoning-effort",
              "--approval-mode"]),
    "codex": (["codex", "exec", "--help"],
              ["resume", "--json", "--model", "--sandbox", "--cd"]),
    "claude": (["claude", "--help"],
               ["--resume", "--session-id", "--output-format", "--model",
                "--effort", "--safe-mode", "--permission-mode",
                "--allowedTools", "--disallowedTools"]),
}

# `codex exec resume` takes no -s/--cd (fresh-only flags), so the resume
# path is contract-checked separately: -c/--config, -m/--model, --json.
_CODEX_RESUME_CONTRACT = (["codex", "exec", "resume", "--help"],
                          ["--config", "--model", "--json"])

# Read-only tool list for claude reviewers: the old gate's production
# allow-list (observed in openspec-codex-gate/scripts/review-gate.sh).
_CLAUDE_REVIEWER_ALLOW = ("Read Grep Glob Bash(git diff:*) "
                          "Bash(git status:*) Bash(git log:*) "
                          "Bash(git show:*) Bash(git ls-files:*)")

# Write authority for claude authors (no bypass flag anywhere): read,
# search, and edit tools plus shell scoped to coding, version control,
# the OpenSpec CLI, interpreters/test runners, repo script entry points,
# and the JS toolchain — enough to code, test, and build headlessly.
# Reviewers never see this list (they stay on the read-only allow-list).
_CLAUDE_AUTHOR_ALLOW = ("Read Grep Glob Edit Write "
                        "Bash(openspec:*) Bash(git:*) Bash(python:*) "
                        "Bash(python3:*) Bash(pytest:*) Bash(bash:*) "
                        "Bash(sh:*) Bash(make:*) Bash(node:*) Bash(npm:*) "
                        "Bash(npx:*)")

# Explicit reasoning effort defaults to medium for authors and reviewers.
# The helper resolves --effort, then saved role effort, then this default.
# Effort is ALWAYS rendered into argv (never left to an ambient CLI
# default) and bound into session identity alongside provider and model.
EFFORT_AUTHOR_DEFAULT = "medium"
EFFORT_REVIEWER_DEFAULT = "medium"

_AUTH_HINTS = ("authenticat", "login", "unauthorized", " 401", "403",
               "api key", "apikey", "api_key", "quota", "billing",
               "rate limit", "429", "permission denied", "forbidden")


class ProviderError(LoopError):
    """Provider unavailable / misconfigured (exit 1, actionable)."""


class InvocationError(LoopError):
    """Provider invocation failed; carries stderr for the handoff note."""


def known(provider):
    return provider in PROVIDERS_ALL


def status(provider):
    if provider in PROVIDERS_VERIFIED:
        return "verified"
    if provider in PROVIDERS_DECLARED:
        return "declared-only"
    return "unknown"


def fixture_dir():
    if os.environ.get("CHANGE_LOOP_DRIVER") == "fixture":
        return os.environ.get("CHANGE_LOOP_FIXTURE_DIR") or "/nonexistent"
    return None


def new_session_id():
    return str(uuid.uuid4())


# --- availability ------------------------------------------------------------
# check(provider): 0 when invokable now, else an actionable error naming the
# provider, the failed probe, and remediation. Declared-only providers always
# fail fast (no guessed CLI contract). Authentication itself is proven at
# invocation time: failures suggesting auth produce auth-specific remediation
# in the handoff note.

def check(provider):
    if not known(provider):
        raise ProviderError(
            "unknown provider '%s'. Valid providers: %s.\n"
            "  Remediation: re-run $change with one of: %s."
            % (provider, " | ".join(PROVIDERS_ALL),
               " | ".join(PROVIDERS_ALL)))
    if status(provider) == "declared-only":
        raise ProviderError(
            "provider '%s' is declared-only (no verified CLI contract on "
            "this machine).\n"
            "  Failed probe: binary/contract verification for '%s'.\n"
            "  Remediation: install and verify the '%s' CLI, then graduate "
            "its adapter - or re-run $change with a verified provider for "
            "that role (muse, codex, claude)."
            % (provider, provider, provider))
    fix = fixture_dir()
    if fix is not None:
        if os.path.isfile(os.path.join(fix, "%s.present" % provider)):
            return True
        raise ProviderError(
            "provider '%s' is not available (fixture: binary missing from "
            "PATH).\n"
            "  Failed probe: '%s' executable lookup.\n"
            "  Remediation: install '%s', authenticate it, or re-run $change "
            "with a different provider for that role."
            % (provider, provider, provider))
    if shutil.which(provider) is None:
        raise ProviderError(
            "provider '%s' is not available (binary missing from PATH).\n"
            "  Failed probe: '%s' executable lookup.\n"
            "  Remediation: install '%s', authenticate it, or re-run $change "
            "with a different provider for that role."
            % (provider, provider, provider))
    argv, flags = _CONTRACT_FLAGS[provider]
    try:
        proc = subprocess.run(argv, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProviderError(
            "provider '%s' is installed but not runnable (%s).\n"
            "  Failed probe: '%s' runnable/flag-surface check.\n"
            "  Remediation: repair or reinstall '%s' (CLI flags may have "
            "drifted), authenticate it, or re-run $change with a different "
            "provider for that role." % (provider, exc, provider, provider))
    if proc.returncode != 0:
        raise ProviderError(
            "provider '%s' is installed but not runnable (help probe "
            "failed).\n"
            "  Failed probe: '%s' runnable/flag-surface check.\n"
            "  Remediation: repair or reinstall '%s' (CLI flags may have "
            "drifted), authenticate it, or re-run $change with a different "
            "provider for that role." % (provider, provider, provider))
    text = proc.stdout.decode("utf-8", "replace")
    missing = [f for f in flags if f not in text]
    if missing:
        raise ProviderError(
            "provider '%s' no longer exposes the flags this loop relies on "
            "(%s).\n"
            "  Failed probe: '%s' contract-flag check.\n"
            "  Remediation: upgrade/downgrade '%s' to a compatible CLI, or "
            "re-run $change with a different provider for that role."
            % (provider, ", ".join(missing), provider, provider))
    if provider == "codex":
        _check_resume_contract(provider)
    return True


def _check_resume_contract(provider):
    """Verify the resume-subcommand flags the reviewer path relies on."""
    argv, flags = _CODEX_RESUME_CONTRACT
    try:
        proc = subprocess.run(argv, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProviderError(
            "provider '%s' resume path is not runnable (%s).\n"
            "  Failed probe: 'codex exec resume' flag-surface check.\n"
            "  Remediation: repair or reinstall '%s', or re-run $change "
            "with a different provider for that role." % (provider, exc,
                                                          provider))
    if proc.returncode != 0:
        raise ProviderError(
            "provider '%s' resume path is not runnable (help probe "
            "failed).\n"
            "  Failed probe: 'codex exec resume' flag-surface check.\n"
            "  Remediation: repair or reinstall '%s', or re-run $change "
            "with a different provider for that role." % (provider,
                                                          provider))
    text = proc.stdout.decode("utf-8", "replace")
    missing = [f for f in flags if f not in text]
    if missing:
        raise ProviderError(
            "provider '%s' resume path no longer exposes (%s).\n"
            "  Failed probe: 'codex exec resume' contract-flag check.\n"
            "  Remediation: upgrade/downgrade '%s' to a compatible CLI, or "
            "re-run $change with a different provider for that role."
            % (provider, ", ".join(missing), provider))


# --- invocation construction (verified providers only) ------------------------
# build_args(provider, role, fresh|resume, session, model, prompt, root,
#            effort=None). Effort resolves to an explicit level (the passed
# value, else medium for all roles) and is ALWAYS rendered
# into argv — reviewer effort never falls through to an ambient CLI
# default. prompt delivery per provider: muse via --prompt-file, codex
# positional, claude on stdin (its variadic tool flags swallow
# positionals, on fresh AND resume). Paths with spaces survive intact as
# single argv elements / stdin bytes.

# Pre-configured default model per verified provider. grok/gemini have no
# entry while declared-only. A CHANGE_LOOP_DEFAULT_MODEL_<PROVIDER> env
# value (uppercased provider name), when set to a non-empty model id,
# overrides the built-in entry; empty or unset means the built-in applies
# (or "no default" for providers without an entry).
DEFAULT_MODELS = {
    "muse": "muse-spark-1.3",
    "claude": "claude-fable-5-1",
    "codex": "gpt-6-astra",
}

SOURCE_EXPLICIT = "explicit"
SOURCE_DEFAULT = "provider-default"
SOURCE_NONE = "none"


def default_model(provider):
    """Configured default model id for a provider, or "" when none."""
    env_key = "CHANGE_LOOP_DEFAULT_MODEL_%s" % str(provider).upper()
    env_value = os.environ.get(env_key) or ""
    if env_value.strip():
        return env_value.strip()
    return DEFAULT_MODELS.get(provider) or ""


def resolve_model(provider, recorded):
    """Resolve (model, source) for one invocation.

    Precedence: explicit recorded value (a --model override or the
    recorded mapping, already merged by the caller), then the
    pre-configured provider default (env override first, then the
    built-in value). Returns (model, source) where source is
    "explicit", "provider-default", or "none" (nothing resolved).
    """
    if recorded and str(recorded).strip():
        return str(recorded).strip(), SOURCE_EXPLICIT
    default = default_model(provider)
    if default:
        return default, SOURCE_DEFAULT
    return "", SOURCE_NONE


def require_model(provider, model):
    """Refuse before invocation when no model id resolved."""
    if not model or not str(model).strip():
        raise ProviderError(
            "no model selected for provider '%s'.\n"
            "  Remediation: init the change with 'provider:model' roles "
            "(e.g. %s:MODEL), pass --model MODEL on this action, or "
            "configure a provider default via "
            "CHANGE_LOOP_DEFAULT_MODEL_%s; the loop never falls back to "
            "another provider's model or a CLI-built-in default."
            % (provider, provider, str(provider).upper()))
    return str(model).strip()


def require_effort(role, effort):
    """Resolve an explicit reasoning-effort level (never ambient default)."""
    if effort and str(effort).strip():
        return str(effort).strip()
    if role == REVIEWER:
        return EFFORT_REVIEWER_DEFAULT
    return EFFORT_AUTHOR_DEFAULT


def build_args(provider, role, mode, session, model, prompt, root,
               effort=None):
    if role not in (AUTHOR, REVIEWER):
        raise ProviderError("unknown role '%s'." % role)
    if provider in PROVIDERS_DECLARED or not known(provider):
        raise ProviderError(
            "provider '%s' is declared-only; no invocation contract "
            "asserted." % provider)
    model = require_model(provider, model)
    effort = require_effort(role, effort)
    reviewer = (role == REVIEWER)
    if provider == "muse":
        argv = ["muse", "exec", "--json", "--prompt-file", prompt,
                "--workspace", root]
        if mode == "resume":
            if not session:
                raise ProviderError("muse resume needs a session id.")
            # Resume reuses the session (and the model it was created
            # with); --model stays fresh-only. Effort is explicit on
            # both paths.
            argv += ["--session-id", session]
        else:
            argv += ["--session-id", session or new_session_id()]
            argv += ["--model", model]
        argv += ["--reasoning-effort", effort]
        if reviewer:
            # --disable-write alone still permits shell writes.
            argv += ["--disable-write", "--disable-shell"]
        else:
            # Headless authors cannot answer approval prompts; keep the
            # sandbox on while making approval behavior explicit.
            argv += ["--approval-mode", "never"]
        return argv
    if provider == "codex":
        # Hook-disabled + never-prompt + explicit reasoning effort on
        # EVERY path (reviewers must not run repo hooks; non-interactive
        # runs must never block on an approval prompt; reviewer effort
        # must never inherit an ambient CLI default).
        hook_off = ["-c", "features.hooks=false", "-c",
                    'approval_policy="never"', "-c",
                    'model_reasoning_effort="%s"' % effort]
        if mode == "resume":
            if not session:
                raise ProviderError("codex resume needs a session id.")
            # -s/--cd are fresh-only flags: pin the role's sandbox via
            # config instead. Reviewers stay read-only (a resumed review
            # must not inherit a workspace-write policy); authors resume
            # with workspace-write (a resumed author must keep the write
            # authority it needs to apply review fixes).
            sandbox = "workspace-write" if not reviewer else "read-only"
            argv = (["codex", "exec", "resume", session, "-m", model]
                    + hook_off
                    + ["-c", 'sandbox_mode="%s"' % sandbox])
            return argv + ["--json", prompt]
        argv = ["codex", "exec", "-m", model] + hook_off
        argv += ["--cd", root, "--json"]
        argv += ["-s", "workspace-write" if not reviewer else "read-only"]
        return argv + [prompt]
    if provider == "claude":
        # The prompt travels on stdin (see invoke): the tool flags are
        # variadic and swallow any positional prompt, on fresh AND resume.
        if mode == "resume":
            if not session:
                raise ProviderError("claude resume needs a session id.")
            # Same workspace: invoke() runs every provider child with
            # cwd=root, so --resume continues in the change workspace.
            # When the CLI cannot find the session it answers "No
            # conversation found" — the caller records an actionable
            # incomplete handoff (stored session preserved, fail closed),
            # never a silent fresh session.
            argv = ["claude", "-p", "--output-format", "json",
                    "--model", model, "--resume", session,
                    "--effort", effort]
        else:
            argv = ["claude", "-p", "--output-format", "json",
                    "--model", model,
                    "--session-id", session or new_session_id(),
                    "--effort", effort]
        if reviewer:
            argv += ["--safe-mode", "--permission-mode", "plan",
                     "--allowedTools", _CLAUDE_REVIEWER_ALLOW,
                     "--disallowedTools", "Write Edit NotebookEdit"]
        else:
            # Hook activity stays disabled on authors too (--safe-mode
            # switches off hooks/skills/plugins while built-in tools and
            # permissions keep working), so an author running inside an
            # old-gate consumer never executes its hooks. Write authority
            # comes from acceptEdits + the scoped allow-list below — no
            # bypass or skip-permissions flag anywhere.
            argv += ["--safe-mode", "--permission-mode", "acceptEdits",
                     "--allowedTools", _CLAUDE_AUTHOR_ALLOW]
        return argv
    raise ProviderError("unknown provider '%s'." % provider)


# --- invocation -----------------------------------------------------------------
# invoke(provider, role, mode, session, model, prompt_text, out_path, root,
#        effort=None, liveness_path=None)
# -> (rc, session_id, stderr_text). Interrupted invocations still persist
# whatever stdout the provider had emitted (partial telemetry survives).
# The session id is NEVER fabricated: it is
# the provider-issued id (codex thread_id / claude envelope session_id), the
# UUID handed to muse/claude via --session-id, or "" when the provider
# reports none (the caller then records an incomplete handoff, never a
# guess). The prompt travels via --prompt-file (muse), positionally
# (codex), or on stdin (claude); stdin is otherwise closed.
# Every provider subprocess runs with cwd=root so OpenSpec resolution and
# fingerprints observe the implementation workspace, never the caller's cwd.

class Interrupted(LoopError):
    """The helper was interrupted mid-invocation (child terminated)."""


# Provider children active right now (at most one per helper process).
# Every child runs as its own process-group leader (start_new_session),
# and the handlers below terminate the WHOLE group — never just the
# immediate child — before converting the signal into an Interrupted
# error, so a killed helper neither orphans provider tool descendants
# nor (via the invocation record) lets a later helper overlap them.
_ACTIVE_CHILDREN = []


def _kill_tree(proc):
    """Terminate the provider child's whole process group (best effort)."""
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except OSError:
        pass
    try:
        proc.wait(timeout=10)
    except Exception:
        pass


def _sigterm_handler(signum, frame):
    for proc in list(_ACTIVE_CHILDREN):
        _kill_tree(proc)
    raise Interrupted("interrupted by SIGTERM during provider invocation "
                      "(provider process group terminated).")


def _write_liveness(path, proc):
    """Record the live invocation tree so a later helper cannot overlap it.

    The helper holds its change lock only while alive: if the helper dies
    (SIGKILL, crash), the kernel releases the lock while the provider
    tree — its own session — may still run. The record carries that
    tree's pgid; the next helper refuses while it is alive (see
    state.live_invocation) and proceeds once it has exited.
    """
    if not path:
        return
    try:
        tmp = "%s.tmp.%d" % (path, os.getpid())
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"pid": proc.pid, "pgid": os.getpgid(proc.pid)}, fh)
            fh.write("\n")
        os.replace(tmp, path)
    except OSError:
        pass


def _clear_liveness(path):
    if not path:
        return
    try:
        os.unlink(path)
    except OSError:
        pass


def _pgid_alive(pgid):
    """True while any member of the process group still exists.

    ESRCH (no such process group) means the whole tree has exited;
    EPERM or any other outcome fails closed (a group we cannot probe
    still excludes the next invocation).
    """
    try:
        os.kill(-pgid, 0)
    except ProcessLookupError:
        return False
    except OSError as exc:
        if exc.errno == errno.ESRCH:
            return False
        return True
    return True


def _release_liveness(path):
    """Drop the invocation liveness record only when its tree is gone.

    `communicate()` returning proves only that the immediate provider
    child exited (and closed its pipes): descendants with redirected
    stdio can survive it. While any group member lives, the record is
    retained so the next helper refuses to overlap the survivor; once
    the group has exited the record is stale and removed. Unknown or
    unreadable records fail closed (retained) — the next helper's
    liveness probe reaps them once the group is provably gone.
    """
    if not path:
        return
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return
    pgid = data.get("pgid") if isinstance(data, dict) else None
    if (isinstance(pgid, int) and not isinstance(pgid, bool) and pgid > 0
            and _pgid_alive(pgid)):
        return
    _clear_liveness(path)


def _suggests_auth(text):
    lowered = (text or "").lower()
    return any(hint in lowered for hint in _AUTH_HINTS)


def _fixture_invoke(provider, mode, role, prompt_text, out_path, root, fix,
                    model, effort=None):
    raw_path = os.path.join(fix, "%s.%s.out" % (provider, mode))
    if not os.path.isfile(raw_path):
        raise InvocationError(
            "no fixture '%s.%s.out'." % (provider, mode))
    with open(raw_path, "rb") as fh:
        raw = fh.read()
    with open(out_path, "wb") as fh:
        fh.write(raw)
    rc = 0
    rc_path = os.path.join(fix, "%s.%s.rc" % (provider, mode))
    if os.path.isfile(rc_path):
        try:
            rc = int(open(rc_path).read().strip())
        except ValueError:
            rc = 1
    err = ""
    err_path = os.path.join(fix, "%s.%s.err" % (provider, mode))
    if os.path.isfile(err_path):
        err = open(err_path, "r", encoding="utf-8", errors="replace").read()
    session = ""
    for name in ("%s.%s.session" % (provider, mode), "%s.session" % provider):
        candidate = os.path.join(fix, name)
        if os.path.isfile(candidate):
            session = open(candidate).read().strip()
            break
    try:
        argv = build_args(provider, role, mode, session, model, "<prompt>",
                          root, effort)
    except ProviderError:
        argv = [provider, mode]
    record = {"provider": provider, "mode": mode, "role": role, "argv": argv,
              "cwd": root, "prompt_bytes": len(prompt_text.encode("utf-8")),
              "effort": require_effort(role, effort), "rc": rc}
    with open(os.path.join(fix, "invocations.log"), "a",
              encoding="utf-8") as log:
        log.write(json.dumps(record, sort_keys=True) + "\n")
    with open(os.path.join(fix, "invoked-%s.%s.prompt" % (provider, mode)),
              "w", encoding="utf-8") as fh:
        fh.write(prompt_text)
    if rc != 0 and _suggests_auth(err + "\n" + raw.decode("utf-8",
                                                          "replace")):
        err = (err + " Likely cause: '%s' authentication (log in / refresh "
               "credentials for '%s', or re-run $change with a different "
               "provider for that role).") % (provider, provider)
    return rc, session, err


def invoke(provider, role, mode, session, model, prompt_text, out_path, root,
           effort=None, liveness_path=None):
    require_model(provider, model)
    check(provider)
    fix = fixture_dir()
    if fix is not None:
        if provider in PROVIDERS_DECLARED:
            raise ProviderError(
                "provider '%s' is declared-only; no invocation contract "
                "asserted." % provider)
        return _fixture_invoke(provider, mode, role, prompt_text, out_path,
                               root, fix, model, effort)
    prompt_path = None
    try:
        if provider == "muse":
            prompt_path = out_path + ".prompt"
            with open(prompt_path, "w", encoding="utf-8") as fh:
                fh.write(prompt_text)
            argv = build_args(provider, role, mode, session, model,
                              prompt_path, root, effort)
            stdin_data = None
        elif provider == "claude":
            # Variadic tool flags swallow positional prompts, so claude
            # reads the prompt from stdin (old-gate channel, live-probed)
            # on fresh AND resume paths alike.
            argv = build_args(provider, role, mode, session, model,
                              prompt_text, root, effort)
            stdin_data = prompt_text.encode("utf-8")
        else:
            argv = build_args(provider, role, mode, session, model,
                              prompt_text, root, effort)
            stdin_data = None
        old_term = signal.signal(signal.SIGTERM, _sigterm_handler)
        # Own process group: cancellation kills the whole invocation tree
        # (provider child plus its tool descendants), never just the
        # immediate child. The group's pgid is recorded (liveness_path)
        # so a later helper refuses to overlap it if THIS helper dies.
        # Suppress OpenSpec background requests in provider children only:
        # sandbox proxy settings can otherwise stall each OpenSpec call.
        env = {**os.environ, "OPENSPEC_TELEMETRY": "0",
               "OPENSPEC_NO_UPDATE_CHECK": "1"}
        proc = subprocess.Popen(argv, stdin=subprocess.PIPE, cwd=root,
                                env=env,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE,
                                start_new_session=True)
        _clear_liveness(liveness_path)
        _write_liveness(liveness_path, proc)
        _ACTIVE_CHILDREN.append(proc)
        try:
            try:
                stdout, stderr = proc.communicate(input=stdin_data)
            except (KeyboardInterrupt, Interrupted):
                # Cancellation (or SIGTERM via the handler above, which
                # already killed the tree): terminate the whole group,
                # drain whatever the provider already emitted, and persist
                # that partial output so available telemetry (usage
                # events, partial verdict text) survives the interruption
                # instead of being discarded.
                _kill_tree(proc)
                try:
                    stdout, stderr = proc.communicate(timeout=10)
                except Exception:
                    stdout, stderr = b"", b""
                try:
                    with open(out_path, "wb") as fh:
                        fh.write(stdout or b"")
                except OSError:
                    pass
                raise Interrupted("interrupted during '%s' invocation "
                                  "(provider process group terminated; "
                                  "partial output persisted)."
                                  % provider)
        finally:
            if proc in _ACTIVE_CHILDREN:
                _ACTIVE_CHILDREN.remove(proc)
            signal.signal(signal.SIGTERM, old_term)
            # Release the exclusion only when the whole invocation tree
            # has exited: communicate() returning alone must not drop it
            # while redirected descendants survive.
            _release_liveness(liveness_path)
        with open(out_path, "wb") as fh:
            fh.write(stdout or b"")
        err_text = (stderr or b"").decode("utf-8", "replace")
        if proc.returncode != 0:
            hint = ""
            if _suggests_auth(err_text + "\n" + (stdout or b"").decode(
                    "utf-8", "replace")):
                hint = (" Likely cause: '%s' authentication (log in / refresh "
                        "credentials for '%s', or re-run $change with a "
                        "different provider for that role)." % (provider,
                                                                provider))
            raise InvocationError(
                "provider '%s' invocation failed (exit %d): %s%s"
                % (provider, proc.returncode, err_text.strip()[-2000:], hint))
        return proc.returncode, _capture_session_id(provider, argv, out_path,
                                                    err_text), err_text
    finally:
        if prompt_path:
            try:
                os.unlink(prompt_path)
            except OSError:
                pass


# --- session identity -----------------------------------------------------------
# Provider-issued ids only. muse: the UUID handed over via --session-id
# (resume contract). codex: thread_id from the --json event stream (the
# installed binary emits thread.started/thread_id events). claude: session_id
# from the result envelope (live-probed shape).

_ID_KEYS = ("thread_id", "threadId", "session_id", "sessionId",
            "conversation_id", "conversationId")


def _walk(obj):
    """Yield every dict and string nested in a parsed JSON value."""
    if isinstance(obj, dict):
        yield obj
        for value in obj.values():
            for item in _walk(value):
                yield item
    elif isinstance(obj, list):
        for value in obj:
            for item in _walk(value):
                yield item
    elif isinstance(obj, str):
        yield obj


def _capture_session_id(provider, argv, out_path, _err_text):
    if provider == "muse":
        try:
            index = argv.index("--session-id")
            return argv[index + 1]
        except (ValueError, IndexError):
            return ""
    if provider == "claude":
        try:
            with open(out_path, "r", encoding="utf-8") as fh:
                envelope = json.load(fh)
        except (OSError, ValueError):
            return ""
        if isinstance(envelope, dict):
            session = envelope.get("session_id")
            if isinstance(session, str) and session:
                return session
        return ""
    if provider == "codex":
        started = first = ""
        try:
            with open(out_path, "r", encoding="utf-8",
                      errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    if not isinstance(event, dict):
                        continue
                    for node in _walk(event):
                        if not isinstance(node, dict):
                            continue
                        for key in _ID_KEYS:
                            value = node.get(key)
                            if (isinstance(value, str) and value
                                    and not first):
                                first = value
                            if (event.get("type") == "thread.started"
                                    and key == "thread_id"
                                    and isinstance(value, str) and value):
                                started = value
        except OSError:
            return ""
        return started or first
    return ""


# --- verdict parsing --------------------------------------------------------------
# normalize(provider, raw_path) -> (loop_verdict, parse_note). Decodes each
# provider's ACTUAL wire format (observed in the supplied review logs),
# then strict-validates the loop schema. Unparseable output becomes an
# `error` verdict (never a silent pass, never invented findings).
# Contradictory verdicts are rejected with an error here too, so they can
# never approve content.
#
# Provider-specific terminal results ONLY (no generic dict/text harvest):
#   codex — the verdict comes from the LAST real completed
#           `agent_message` item (`item.completed` with `item.type ==
#           "agent_message"`), which must itself carry valid verdict JSON,
#           AND a successful terminal `turn.completed`. A later assistant
#           message without a verdict (e.g. "cannot complete") supersedes
#           any earlier candidate. Anything else showing the turn never
#           settled — a malformed/truncated JSON line, a dangling
#           `item.started` tool, an unfinished/failed tool completion, a
#           failure event, or a missing/non-success terminal (truncated
#           stream) — makes the verdict `error`, never clean.
#   claude — the single `{"type": "result", ...}` envelope with
#           `subtype == "success"` and `is_error == false`; the verdict
#           JSON is the envelope's `result` string. Any other subtype,
#           an error flag, or a non-string result is `error`.
#   muse — the run's own `run.terminal.completed` event (payload
#           `terminal == "completed"`) whose run identity matches the
#           run's start event; the verdict JSON is the terminal's `text`.
#           `tool.result` content is never a verdict, a failed task in
#           the run fails the run, and a missing/mismatched terminal
#           (truncated or malformed stream) is `error`.
# Intermediate assistant messages are superseded by the terminal one, and
# verdict-shaped JSON nested under tool/command-output payloads (captured
# stdout echoing a fixture, quoted intermediate JSON) is never a verdict.

# --- free-text verdict unwrapping --------------------------------------------------
# Reviewers sometimes wrap the verdict JSON in a single markdown code fence
# (```json ... ``` or ``` ... ```), optionally preceded/followed by short
# prose. extract_verdict_text() unwraps that one shape and returns the
# candidate JSON string; every free-text verdict path (claude result,
# muse terminal text, codex assistant message text, manual claude-envelope
# decode) feeds its text through it ahead of the unchanged validate_verdict
# gate. Anything that is not exactly one candidate object — multiple fenced
# blocks, an unclosed fence, brace-looking content or leftover fence
# delimiters outside the single fence, an empty fence — yields None
# (callers record error, never clean). Bare JSON passes through unchanged,
# even when JSON string values contain backticks (whole-text decoding is
# tried before fence extraction, so backticks inside strings stay data);
# prose around bare (unfenced) JSON is NOT tolerated (the downstream
# json.loads rejects it). Fence boundaries are never guessed from the
# first triple-backtick run alone: every later ``` is tried as the
# closing fence in turn, and only a split whose inner parses as JSON
# with delimiter-free surroundings counts — so backticks inside JSON
# strings cannot masquerade as the closing fence.

_FENCE_OPEN_RE = re.compile(r"```(?:[ \t]*[Jj][Ss][Oo][Nn])?[ \t]*\r?\n?")


def extract_verdict_text(text):
    """Candidate verdict JSON for free text, or None when not exactly one."""
    if not isinstance(text, str):
        return None
    stripped = text.strip()
    try:
        json.loads(stripped)
        return stripped
    except ValueError:
        pass
    opening = _FENCE_OPEN_RE.search(text)
    if opening is None:
        return None
    index = opening.end()
    while True:
        close = text.find("```", index)
        if close < 0:
            return None
        inner = text[opening.end():close].strip()
        outside = text[:opening.start()] + text[close + 3:]
        if inner:
            try:
                json.loads(inner)
            except ValueError:
                pass
            else:
                if ("```" not in outside and "{" not in outside
                        and "}" not in outside):
                    return inner
        index = close + 3


# Payload keys whose subtrees are tool/command output, never the reviewer's
# verdict. Matching is case-insensitive; assistant message text ("text",
# "message", "content", ...) is NOT in this set.
_TOOL_OUTPUT_KEYS = frozenset([
    "output", "outputs", "command_output", "aggregated_output",
    "tool_result", "tool_results", "tool_output", "function_call_output",
    "function_call_result", "exec_output", "exec_result", "command_result",
    "stdout", "stderr",
])

# Terminal markers proving the run failed or never completed: such a stream
# can never yield a clean verdict, no matter what earlier lines contain.
_TERMINAL_FAILURE_TYPES = frozenset([
    "error", "turn.failed", "turn_failed", "task.failed", "task_failed",
    "run.failed", "run_failed",
])
_TERMINAL_FAILURE_STATUSES = frozenset([
    "failed", "error", "cancelled", "canceled", "incomplete",
])


def _is_tool_key(key):
    return isinstance(key, str) and key.lower() in _TOOL_OUTPUT_KEYS


def _assistant_verdicts(event):
    """Verdict objects from an event's ASSISTANT text, in walk order.

    Recurses through dicts/lists but never descends into tool-output
    subtrees; verdicts surface either as objects or as JSON-encoded
    strings in message text (exact event names vary by CLI version, so
    the scan stays key-based outside the excluded payload keys).
    """
    found = []

    def visit(node, under_tool_output):
        if isinstance(node, dict):
            if (not under_tool_output and "status" in node
                    and "findings" in node):
                found.append(node)
                return
            for key, value in node.items():
                visit(value, under_tool_output or _is_tool_key(key))
        elif isinstance(node, list):
            for value in node:
                visit(value, under_tool_output)
        elif isinstance(node, str) and not under_tool_output:
            candidate = extract_verdict_text(node)
            if (candidate is not None and candidate.startswith("{")
                    and '"status"' in candidate):
                try:
                    obj = json.loads(candidate)
                except ValueError:
                    return
                if (isinstance(obj, dict) and "status" in obj
                        and "findings" in obj):
                    found.append(obj)

    visit(event, False)
    return found


# Assistant message-text keys: an event carrying a non-empty string under
# one of these (outside tool-output subtrees) is an assistant message, and
# only the LAST such event can carry the verdict. A final assistant message
# with no verdict (e.g. "I cannot finish this review") therefore supersedes
# any earlier verdict-shaped text instead of letting it normalize to clean.
_ASSISTANT_MESSAGE_KEYS = frozenset(["text", "message", "content"])


def _has_assistant_message(event):
    found = []

    def visit(node, under_tool_output):
        if found:
            return
        if isinstance(node, dict):
            for key, value in node.items():
                child_tool = under_tool_output or _is_tool_key(key)
                if (not child_tool
                        and isinstance(key, str)
                        and key.lower() in _ASSISTANT_MESSAGE_KEYS
                        and isinstance(value, str) and value.strip()):
                    found.append(True)
                    return
                visit(value, child_tool)
        elif isinstance(node, list):
            for value in node:
                visit(value, under_tool_output)

    visit(event, False)
    return bool(found)


def _terminal_failed(event):
    """True when the stream's final event reports failure/incompletion."""
    if not isinstance(event, dict):
        return False
    typ = event.get("type")
    if isinstance(typ, str) and typ.lower() in _TERMINAL_FAILURE_TYPES:
        return True
    status = event.get("status")
    if (isinstance(status, str)
            and status.lower() in _TERMINAL_FAILURE_STATUSES):
        return True
    err = event.get("error")
    if isinstance(err, dict) and err.get("message"):
        return True
    if isinstance(err, str) and err.strip():
        return True
    return False


def _events_from_jsonl(raw_path):
    """Parsed top-level JSON objects of a JSONL stream, in order."""
    events, _malformed = _events_from_jsonl_strict(raw_path)
    return events


def _events_from_jsonl_strict(raw_path):
    """(events, malformed) for a JSONL stream, in order.

    malformed is True when any nonempty line is not a top-level JSON
    object (truncated/corrupt output): verdict normalization must treat
    such a stream as error, never silently skip the bad lines.
    """
    try:
        with open(raw_path, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return [], False
    events = []
    malformed = False
    for line in lines:
        if not line.strip():
            continue
        try:
            top = json.loads(line)
        except ValueError:
            malformed = True
            continue
        if isinstance(top, dict):
            events.append(top)
        else:
            malformed = True
    return events, malformed


def _error_verdict(provider, summary):
    return ({"status": "error", "findings": [], "summary": summary,
             "reviewer": {"provider": provider}},
            "unparseable provider output recorded as error")


def _is_codex_agent_message(event):
    """A real completed assistant item (observed: item.completed with
    item.type == agent_message and reviewer text)."""
    if not isinstance(event, dict):
        return False
    if event.get("type") != "item.completed":
        return False
    item = event.get("item")
    return (isinstance(item, dict)
            and item.get("type") == "agent_message")


def _codex_tool_unsettled(event):
    """True when a tool event shows work never settled: a started-but-open
    execution, an in-progress completion, a nonzero exit, or an explicit
    failure marker."""
    if not isinstance(event, dict):
        return False
    typ = event.get("type")
    if typ == "item.started":
        return True
    if typ != "item.completed":
        return _terminal_failed(event)
    item = event.get("item")
    if not isinstance(item, dict):
        return False
    if item.get("type") == "agent_message":
        return False
    status = item.get("status")
    if isinstance(status, str) and status.lower() != "completed":
        return True
    exit_code = item.get("exit_code")
    if exit_code is not None and exit_code != 0:
        return True
    return False


def _normalize_codex(raw_path):
    """Verdict from the completed agent_message item plus a successful
    terminal turn.completed — never from tool content, never from a
    truncated/failed stream."""
    provider = "codex"
    events, malformed = _events_from_jsonl_strict(raw_path)
    if malformed:
        return _error_verdict(
            provider, "codex output has malformed/truncated JSON lines; "
                      "recorded as error (not clean)")
    if not events:
        return _error_verdict(
            provider, "codex output has no parseable events; recorded "
                      "as error (not clean)")
    thread_id = ""
    for event in events:
        if (event.get("type") == "thread.started"
                and isinstance(event.get("thread_id"), str)
                and event.get("thread_id")):
            thread_id = event["thread_id"]
            break
    if not thread_id:
        return _error_verdict(
            provider, "codex output has no thread.started run identity; "
                      "recorded as error (not clean)")
    # The stream must END in a successful turn.completed (a truncated
    # stream, or one ending in failure/incompletion, never yields clean).
    last = events[-1]
    if (not isinstance(last, dict) or last.get("type") != "turn.completed"
            or _terminal_failed(last)):
        return _error_verdict(
            provider, "codex turn never completed successfully; recorded "
                      "as error (not clean)")
    # Every started tool must complete: a started-but-never-completed
    # execution anywhere in the stream means the turn never settled.
    started_ids = set()
    completed_ids = set()
    for event in events:
        if not isinstance(event, dict):
            continue
        item = event.get("item")
        iid = item.get("id") if isinstance(item, dict) else None
        if event.get("type") == "item.started":
            started_ids.add(iid)
        elif event.get("type") == "item.completed" and iid is not None:
            completed_ids.add(iid)
    if started_ids - completed_ids:
        return _error_verdict(
            provider, "codex tool execution started but never completed; "
                      "recorded as error (not clean)")
    # The verdict is the LAST completed assistant message, and it must
    # itself carry valid verdict JSON. A later assistant message with no
    # verdict (e.g. "I cannot complete this review") supersedes any
    # earlier candidate — prior clean JSON is never harvested when the
    # last message is invalid. Tool/command output (completed or not) is
    # never a verdict: only agent_message text is scanned.
    verdict_index = -1
    for index, event in enumerate(events):
        if _is_codex_agent_message(event):
            verdict_index = index
    if verdict_index < 0:
        return _error_verdict(
            provider, "codex turn completed with no assistant verdict; "
                      "recorded as error (not clean)")
    candidates = _assistant_verdicts(events[verdict_index].get("item"))
    if not candidates:
        return _error_verdict(
            provider, "codex final assistant message carries no reviewer "
                      "verdict; recorded as error (not clean)")
    # Nothing after the verdict message may show unsettled work: a later
    # incomplete tool, a failed tool, or a failure event voids it.
    for event in events[verdict_index + 1:]:
        if isinstance(event, dict) and event.get("type") == "turn.completed":
            continue
        if _codex_tool_unsettled(event):
            return _error_verdict(
                provider, "codex tool work after the assistant verdict "
                          "never settled; recorded as error (not clean)")
    verdict, err = validate_verdict(candidates[-1])
    if err:
        return _error_verdict(provider, "codex verdict invalid: %s" % err)
    verdict.setdefault("reviewer", {})["provider"] = provider
    return verdict, ""


def _muse_run_id(event):
    """The run identity of a muse event, or '' when it carries none."""
    if not isinstance(event, dict):
        return ""
    payload = event.get("payload")
    if not isinstance(payload, dict):
        return ""
    run = payload.get("run_stream")
    if not isinstance(run, dict):
        return ""
    rid = run.get("id")
    return rid if isinstance(rid, str) and rid else ""


def _normalize_muse(raw_path):
    """Verdict from the run's own run.terminal.completed text with
    matching run identity — never from tool.result content, never from a
    truncated/failed stream."""
    provider = "muse"
    events, malformed = _events_from_jsonl_strict(raw_path)
    if malformed:
        return _error_verdict(
            provider, "muse output has malformed/truncated JSON lines; "
                      "recorded as error (not clean)")
    if not events:
        return _error_verdict(
            provider, "muse output has no parseable events; recorded "
                      "as error (not clean)")
    run_id = ""
    for event in events:
        if event.get("payload_type") == "run.lifecycle.started":
            run_id = _muse_run_id(event)
            if run_id:
                break
    if not run_id:
        return _error_verdict(
            provider, "muse output has no run start identity; recorded "
                      "as error (not clean)")
    for event in events:
        if event.get("payload_type") != "task.lifecycle.failed":
            continue
        if _muse_run_id(event) not in ("", run_id):
            continue
        return _error_verdict(
            provider, "muse run reports a failed task; recorded as "
                      "error (not clean)")
    terminal = None
    for event in events:
        if event.get("payload_type") != "run.terminal.completed":
            continue
        if _muse_run_id(event) != run_id:
            # A terminal from another run is not this run's decision.
            continue
        terminal = event.get("payload")
    if not isinstance(terminal, dict):
        return _error_verdict(
            provider, "muse run has no terminal for its run identity "
                      "(truncated stream); recorded as error (not clean)")
    if terminal.get("terminal") != "completed":
        return _error_verdict(
            provider, "muse run did not complete successfully; recorded "
                      "as error (not clean)")
    text = terminal.get("text")
    if not isinstance(text, str) or not text.strip():
        return _error_verdict(
            provider, "muse terminal carries no reviewer text; recorded "
                      "as error (not clean)")
    candidate = extract_verdict_text(text)
    if candidate is None:
        return _error_verdict(
            provider, "muse terminal text is not loop-verdict JSON; "
                      "recorded as error (not clean)")
    try:
        obj = json.loads(candidate)
    except ValueError:
        return _error_verdict(
            provider, "muse terminal text is not loop-verdict JSON; "
                      "recorded as error (not clean)")
    verdict, err = validate_verdict(obj)
    if err:
        return _error_verdict(provider, "muse verdict invalid: %s" % err)
    verdict.setdefault("reviewer", {})["provider"] = provider
    return verdict, ""


def normalize(provider, raw_path):
    if provider == "claude":
        try:
            with open(raw_path, "r", encoding="utf-8") as fh:
                envelope = json.load(fh)
        except (OSError, ValueError):
            return _error_verdict(provider, "unparseable provider output; "
                                            "recorded as error (not clean)")
        if not isinstance(envelope, dict) or envelope.get("type") != "result":
            return _error_verdict(
                provider, "claude output is not a result envelope; recorded "
                          "as error (not clean)")
        # The actual final result envelope only: success, not error.
        if envelope.get("subtype") != "success":
            return ({"status": "error", "findings": [],
                     "summary": ("claude turn did not succeed (subtype=%r); "
                                 "recorded as error (not clean)"
                                 % (envelope.get("subtype"),)),
                     "reviewer": {"provider": provider}},
                    "non-success claude result recorded as error")
        if envelope.get("is_error"):
            return ({"status": "error", "findings": [],
                     "summary": ("claude reported an error turn: %s"
                                 % str(envelope.get("result"))[:500]),
                     "reviewer": {"provider": provider}}, "")
        if not isinstance(envelope.get("result"), str):
            return _error_verdict(
                provider, "claude result is not reviewer text; recorded "
                          "as error (not clean)")
        candidate = extract_verdict_text(envelope.get("result") or "")
        if candidate is None:
            return _error_verdict(
                provider, "claude result text is not loop-verdict JSON; "
                          "recorded as error (not clean)")
        try:
            obj = json.loads(candidate)
        except (ValueError, AttributeError):
            return _error_verdict(
                provider, "claude result text is not loop-verdict JSON; "
                          "recorded as error (not clean)")
        verdict, err = validate_verdict(obj)
        if err:
            return _error_verdict(provider, "claude verdict invalid: %s" % err)
        verdict.setdefault("reviewer", {})["provider"] = provider
        return verdict, ""
    if provider == "codex":
        return _normalize_codex(raw_path)
    if provider == "muse":
        return _normalize_muse(raw_path)
    # Declared-only providers: loop-schema verdicts only (no wire format).
    try:
        with open(raw_path, "r", encoding="utf-8") as fh:
            obj = json.load(fh)
    except (OSError, ValueError):
        return _error_verdict(provider, "unparseable provider output; "
                                        "recorded as error (not clean)")
    verdict, err = validate_verdict(obj)
    if err:
        return _error_verdict(provider, "provider verdict invalid: %s" % err)
    verdict.setdefault("reviewer", {})["provider"] = provider
    return verdict, ""


# --- token usage ------------------------------------------------------------------
# extract_usage(provider, raw_path) -> usage dict for ONE invocation's
# stdout file. Shapes below are the REAL observed wire formats (see the
# module docstring), never invented envelopes:
#   codex  — every `turn.completed` event's `usage` object, SUMMED over the
#            file (one exec may complete several turns). `input_tokens`
#            INCLUDES cached tokens (OpenAI convention), so the uncached
#            remainder is input minus cached-read. `reasoning_output_tokens`
#            is a SUBSET of output_tokens.
#   claude — the result envelope's `usage` object: `input_tokens` EXCLUDES
#            cache, so the true input total adds `cache_creation_input_tokens`
#            + `cache_read_input_tokens`. `output_tokens_details` may carry
#            the `thinking_tokens` reasoning subset; `total_cost_usd` and
#            `num_turns` ride along when present. Raw counters are retained.
#   muse   — the observed `muse exec --json` schema carries NO usage
#            counters: usage is UNKNOWN (reported as unknown, never zero).
# Only the current invocation's output file is parsed, so resumed sessions
# never double-count earlier rounds. Unknown is not zero: every field is
# None and `known` is False when the provider reported nothing.

def _int_or_none(value):
    return value if isinstance(value, bool) is False \
        and isinstance(value, int) else None


def _codex_usage(raw_path):
    # Unknown stays unknown: each counter starts unset and only fields the
    # provider ACTUALLY reported appear in the record (a reported 0 is
    # data; an absent field is None, never a fabricated zero).
    total_in = cached = written = out = reasoning = None
    try:
        with open(raw_path, "r", encoding="utf-8",
                  errors="replace") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return None
    for line in lines:
        if '"usage"' not in line:
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        usage = event.get("usage")
        if not isinstance(usage, dict):
            continue
        inp = usage.get("input_tokens")
        if not isinstance(inp, int) or isinstance(inp, bool):
            continue
        total_in = (total_in or 0) + inp
        for key in ("cached_input_tokens", "cache_write_input_tokens",
                    "output_tokens", "reasoning_output_tokens"):
            value = usage.get(key)
            if isinstance(value, int) and not isinstance(value, bool):
                if key == "cached_input_tokens":
                    cached = (cached or 0) + value
                elif key == "cache_write_input_tokens":
                    written = (written or 0) + value
                elif key == "output_tokens":
                    out = (out or 0) + value
                else:
                    reasoning = (reasoning or 0) + value
    if total_in is None:
        return None
    raw = {"input_tokens": total_in}
    if cached is not None:
        raw["cached_input_tokens"] = cached
    if written is not None:
        raw["cache_write_input_tokens"] = written
    if out is not None:
        raw["output_tokens"] = out
    if reasoning is not None:
        raw["reasoning_output_tokens"] = reasoning
    return {
        "input_total": total_in,
        "input_cached_read": cached,
        "input_cache_write": written,
        # input_tokens include cached tokens; the remainder mixes fresh
        # input with cache-creation tokens (known only when cached reads
        # were reported).
        "input_uncached": (total_in - cached
                           if cached is not None else None),
        "output": out,
        "reasoning": reasoning,
        "total": (total_in + out if out is not None else None),
        "raw": raw,
    }


def _claude_usage(raw_path):
    try:
        with open(raw_path, "r", encoding="utf-8") as fh:
            envelope = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(envelope, dict):
        return None
    usage = envelope.get("usage")
    if not isinstance(usage, dict):
        return None
    inp = _int_or_none(usage.get("input_tokens"))
    out = _int_or_none(usage.get("output_tokens"))
    if inp is None and out is None:
        return None
    # Unreported cache/reasoning fields stay unknown (None): only actually
    # reported counters enter the record and its raw metrics.
    creation = _int_or_none(usage.get("cache_creation_input_tokens"))
    read = _int_or_none(usage.get("cache_read_input_tokens"))
    thinking = None
    details = usage.get("output_tokens_details")
    if isinstance(details, dict):
        thinking = _int_or_none(details.get("thinking_tokens"))
    cost = envelope.get("total_cost_usd")
    if not isinstance(cost, (int, float)) or isinstance(cost, bool):
        cost = None
    turns = _int_or_none(envelope.get("num_turns"))
    if (inp is not None and creation is not None and read is not None):
        # Claude input_tokens EXCLUDE cache: the true total adds both.
        input_total = inp + creation + read
    else:
        input_total = None
    if input_total is not None and out is not None:
        total = input_total + out
    else:
        total = None
    raw = {}
    if inp is not None:
        raw["input_tokens"] = inp
    if creation is not None:
        raw["cache_creation_input_tokens"] = creation
    if read is not None:
        raw["cache_read_input_tokens"] = read
    if out is not None:
        raw["output_tokens"] = out
    if thinking is not None:
        raw["thinking_tokens"] = thinking
    return {
        "input_total": input_total,
        "input_cached_read": read,
        "input_cache_write": creation,
        "input_uncached": inp,
        "output": out,
        "reasoning": thinking,
        "total": total,
        "cost_usd": cost,
        "turns": turns,
        "raw": raw,
    }


def _muse_usage(raw_path):
    # Observed `muse exec --json` events carry task/tool lifecycles, never
    # token counters: a light key scan for forward-compatibility, else
    # honestly unknown.
    try:
        with open(raw_path, "r", encoding="utf-8",
                  errors="replace") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return None
    for line in lines:
        if "input_tokens" not in line and "inputTokens" not in line:
            continue
        try:
            top = json.loads(line)
        except ValueError:
            continue
        for node in _walk(top):
            if not isinstance(node, dict):
                continue
            inp = node.get("input_tokens", node.get("inputTokens"))
            out = node.get("output_tokens", node.get("outputTokens"))
            if (isinstance(inp, int) and not isinstance(inp, bool)
                    and isinstance(out, int) and not isinstance(out, bool)):
                return {
                    "input_total": inp,
                    "input_cached_read": None,
                    "input_cache_write": None,
                    "input_uncached": None,
                    "output": out,
                    "reasoning": None,
                    "total": inp + out,
                    "raw": {"input_tokens": inp, "output_tokens": out},
                }
    return None


def extract_usage(provider, raw_path, model="", role="", session="",
                  action="", mode="", effort="", source=""):
    """Normalized per-invocation usage record (see module docstring)."""
    parsed = None
    if provider == "codex":
        parsed = _codex_usage(raw_path)
    elif provider == "claude":
        parsed = _claude_usage(raw_path)
    elif provider == "muse":
        parsed = _muse_usage(raw_path)
    record = {"provider": provider, "model": model or "", "role": role or "",
              "action": action or "", "session": session or "",
              "mode": mode or "", "effort": effort or "",
              "source": source or "",
              "known": parsed is not None,
              # Complete only when every headline counter was reported:
              # a partially known invocation (some counters None) is
              # never counted as wholly known downstream.
              "complete": False,
              "input_total": None, "input_cached_read": None,
              "input_cache_write": None, "input_uncached": None,
              "output": None, "reasoning": None, "total": None,
              "cost_usd": None, "turns": None, "raw": None,
              "note": ""}
    if parsed is None:
        record["note"] = ("no usage counters in observed '%s' output "
                          "schema; unknown, not zero" % provider)
        return record
    record.update(parsed)
    record["complete"] = (
        record.get("input_total") is not None
        and record.get("output") is not None
        and record.get("total") is not None)
    return record


def format_usage(record):
    """One host-visible usage line (unknown is not zero)."""
    who = "%s %s/%s" % (record.get("action") or "invocation",
                        record.get("provider"), record.get("model") or "?")
    if record.get("effort"):
        who += " effort=%s" % record.get("effort")
    if not record.get("known"):
        return ("change-loop: usage %s: unknown (%s)"
                % (who, record.get("note") or "not reported"))
    # Partially reported counters render only what the provider reported;
    # unreported fields stay absent (never zero-filled here either).
    parts = []
    if record.get("input_total") is not None:
        parts.append("input=%d" % record["input_total"])
    detail = []
    if record.get("input_cached_read") is not None:
        detail.append("cached_read=%d" % record["input_cached_read"])
    if record.get("input_cache_write") is not None:
        detail.append("cache_write=%d" % record["input_cache_write"])
    if record.get("input_uncached") is not None:
        detail.append("uncached=%d" % record["input_uncached"])
    if detail:
        parts.append("(%s)" % " ".join(detail))
    if record.get("output") is not None:
        out = "output=%d" % record["output"]
        if record.get("reasoning") is not None:
            out += " (reasoning=%d)" % record["reasoning"]
        parts.append(out)
    elif record.get("reasoning") is not None:
        parts.append("(reasoning=%d)" % record["reasoning"])
    if record.get("total") is not None:
        parts.append("total=%d" % record["total"])
    if not parts:
        return ("change-loop: usage %s: partial (%s)"
                % (who, record.get("note") or "provider reported no "
                   "counters"))
    if record.get("cost_usd") is not None:
        parts.append("cost_usd=%.4f" % record["cost_usd"])
    if record.get("turns") is not None:
        parts.append("turns=%d" % record["turns"])
    # A partially known invocation is flagged as such: the headline
    # counters it did NOT report stay unknown, never zero.
    if not record.get("complete"):
        missing = [key for key in ("input_total", "output", "total")
                   if record.get(key) is None]
        parts.append("(partial: %s unknown)"
                     % " ".join(missing))
    return "change-loop: usage %s: %s" % (who, " ".join(parts))
