#!/usr/bin/env python3
"""ballast-dispatch.py EVENT [PLUGIN_ROOT] [--part NAME] [--scope-kind KIND]
- the wrapper hook that resolves which ballast.json applies to THIS session
and forwards to shim/ballast-shim.py with a resolved --scope path.

Why this file exists rather than the byte-identical static template
ballast ships (docs/consumer-hooks.json.snippet): this plugin owns MANY
scopes, one per bound workstream directory, resolved at runtime from the
session's own sidecar - not one static ballast.json per plugin. Ballast's
own docs call this case out explicitly: "a plugin that owns many scopes,
one per some runtime-resolved identity, such as one workstream directory
per session... needs its own small wrapper hook script that first
resolves which ballast.json applies this invocation, then execs the shim
with that resolved --scope path." This is that wrapper. shim/ballast-
shim.py itself is COPIED BYTE-IDENTICAL from the claude-ballast repo,
never modified here.

Arguments
---------
`--part NAME` is forwarded to the shim untouched (ballast 0.1.2's
SessionStart split: `hot` / `policy` / `glossary` / `playbook`, default
`all`). Omitted here means omitted there, which is how an un-updated hook
line keeps its pre-split behavior.

`--scope-kind global` points the invocation at the SHARED scope,
`<state-root>/_global/ballast.json` (global-policy.md - the rules every
workstream reads), instead of this session's own workstream directory.
It resolves nothing and prints nothing when the session is unbound or
that file does not exist: an operator seeds `_global/` deliberately, and
a consumer that has not is not in an error state. `workstream` (the
default) is this session's own bound workstream directory.

Exit codes
----------
The shim's own exit code is returned VERBATIM. That is load-bearing: the
one non-zero code ballast uses is 2, the PreToolUse approval refusal
(hook protocol - exit 2, reason on stderr), and a wrapper that copies the
output and then returns 0 turns every refusal into an allow, so the gate
looks wired and enforces nothing (ballast's docs/approval-gate.md, "The
same requirement binds any wrapper").

This wrapper adds ONE refusal of its own, before any forwarding: a direct
Write/Edit/NotebookEdit of a `workstream.json` under the state root
(WORKSTREAM_MANIFEST_REFUSAL below). Manifests are schema-authoritative
and single-writer; every field write goes through scripts/manifest.py.
That check runs whether or not this session is bound - it protects the
state root, not this session's own identity.

Unbound sessions (no sidecar, or born_session doesn't resolve to an
existing dir - the common case for most sessions/most turns): near-zero-
cost no-op, exit 0, no output, no ballast invocation at all - with ONE
exception, PreToolUse. Ballast's approval gate protects FILES under the
state root (policy.md, global-policy.md, playbook.md, glossary.md), not
sessions, and this plugin is the only consumer that wires it: an unbound
session skipping the gate would leave those four files guarded in name
only against the very population the docstring above calls the common
case. So on PreToolUse alone, when this session is unbound and the write
TARGETS a path under the state root, the scope is resolved from that
target instead of from the session (gate_scope_for below) and the forward
happens anyway. Every other event, and every PreToolUse whose target is
outside the state root, stays the silent no-op.

Bound sessions: ensures a ballast.json exists in the workstream's own dir
(writing the documented default scope - ballast 0.1.2's
fixtures/scope-example/ballast.json verbatim - on first use, and
migrating a scope that still holds the old 0.1.1 default byte-for-byte;
a customized scope is left untouched), then runs the shim with
--scope <ws_dir>/ballast.json, forwarding stdin/stdout/stderr byte for
byte (the shim already fails open if ballast itself is not installed -
this wrapper adds no new failure mode on top of that).
"""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import workstream_lib as wslib

# Verbatim copy of ballast 0.1.2's fixtures/scope-example/ballast.json
# (docs/ballast.json.md prints the same text as its Example) - the
# workstream scope IS the documented default scope, since 02/04g's hot
# required_slots are exactly the nine names ballast.json already defaults
# to. Held as TEXT, not a dict, so what lands on disk is byte-identical to
# the fixture rather than whatever json.dumps happens to render.
DEFAULT_SCOPE_TEXT = """{
  "root": ".",
  "log": "log.md",
  "hot": "hot.md",
  "index": "index.md",
  "policy": "policy.md",
  "glossary": "glossary.md",
  "playbook": "playbook.md",
  "regen": null,
  "hot_cap_bytes": 4096,
  "policy_cap_bytes": 7168,
  "glossary_cap_bytes": 4096,
  "playbook_inject_cap_bytes": 2048,
  "playbook_file_warn_bytes": 16384,
  "required_slots": [
    "focus", "next", "blocked", "updated",
    "done", "looping", "progressing", "who-acts-next", "stall"
  ],
  "significant_write_rule": "path-under-root",
  "reground_interval_turns": 25,
  "min_engine": "0.1.0"
}
"""

# The 0.1.1 default this wrapper used to write - `json.dumps(DEFAULT_SCOPE,
# indent=2) + "\\n"` of the dict it then held - kept verbatim so the
# migration below can recognize it. A scope holding EXACTLY these bytes was
# written by the wrapper and never touched by anyone, so replacing it loses
# no choice a person made. Anything else - a raised cap, a dropped class, a
# hand-written scope - is a decision, and is left alone; /workstream:status
# prints its caps so the owner can see what it is still running and raise
# them deliberately.
OLD_DEFAULT_SCOPE_TEXT = """{
  "root": ".",
  "log": "log.md",
  "hot": "hot.md",
  "index": "index.md",
  "policy": "policy.md",
  "regen": null,
  "hot_cap_bytes": 1024,
  "policy_cap_bytes": 7168,
  "required_slots": [
    "focus",
    "next",
    "blocked",
    "updated",
    "done",
    "looping",
    "progressing",
    "who-acts-next",
    "stall"
  ],
  "significant_write_rule": "path-under-root",
  "reground_interval_turns": 25,
  "min_engine": "0.1.0"
}
"""

GLOBAL_DIRNAME = "_global"
MANIFEST_FILENAME = "workstream.json"
WRITE_TOOL_NAMES = ("write", "edit", "notebookedit")
PATH_KEYS = ("file_path", "path", "notebook_path")
REFUSAL_EXIT = 2   # the hook protocol's refusal code (ballast_lib.REFUSAL_EXIT)

WORKSTREAM_MANIFEST_REFUSAL = (
    "workstream: %s may not be written with Write/Edit/NotebookEdit.\n"
    "A workstream manifest is schema-authoritative and single-writer: every "
    "field write goes through scripts/manifest.py, which validates the field's "
    "shape, refuses the retired fields, and holds maintains/direct_report/"
    "collaborate/absorbed behind Ballast's approval gate.\n"
    "The sanctioned path: run /workstream:manifest, which routes to the "
    "primitive - manifest.py set BORN_SESSION FIELD JSON-VALUE (or the "
    "collaborate-add / collaborate-remove / append-absorbed subcommand). Read "
    "it back with manifest.py read BORN_SESSION."
)


def read_stdin_bytes():
    try:
        if sys.stdin is None or sys.stdin.isatty():
            return b""
        return sys.stdin.buffer.read()
    except Exception:
        return b""


def parse_args(argv):
    """`(event, plugin_root_arg, part, scope_kind)`. Positional order is
    unchanged from 0.1.3 (EVENT then an optional PLUGIN_ROOT), so an
    un-updated hook line keeps working; the two flags may appear anywhere
    after them."""
    positional, part, scope_kind = [], None, "workstream"
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--part" and i + 1 < len(argv):
            part = argv[i + 1]
            i += 2
        elif arg == "--scope-kind" and i + 1 < len(argv):
            scope_kind = argv[i + 1]
            i += 2
        else:
            positional.append(arg)
            i += 1
    event = positional[0] if positional else "SessionStart"
    plugin_root_arg = positional[1] if len(positional) > 1 else None
    return event, plugin_root_arg, part, scope_kind


def resolve_plugin_root(plugin_root_arg):
    if plugin_root_arg:
        return plugin_root_arg
    env_root = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if env_root:
        return env_root
    return wslib.plugin_root()


def canonical(path):
    """The one spelling of a path this wrapper compares on - symlinks
    resolved, case and separators normalized. Same rule ballast's
    canonical_target() applies, and for the same reason: a consuming vault
    is commonly reachable under two roots (a native path and a symlink to
    it from the other subsystem), so raw string comparison would let a
    write arriving through one spelling walk past a guard written against
    the other."""
    return os.path.normcase(os.path.realpath(os.path.abspath(path)))


def is_under(path, root):
    if not path or not root:
        return False
    try:
        return os.path.commonpath([canonical(path), canonical(root)]) \
            == canonical(root)
    except ValueError:
        return False   # different drives on Windows -> definitely not under


def extract_paths(tool_input):
    if not isinstance(tool_input, dict):
        return []
    out = []
    for key in PATH_KEYS:
        val = tool_input.get(key)
        if isinstance(val, str) and val.strip():
            out.append(val)
    return out


def manifest_write_refused(payload, vault):
    """The one refusal this wrapper owns: a direct Write/Edit/NotebookEdit
    of a workstream.json under the state root. Returns the target path
    when the write must be refused, else None. Deliberately independent of
    whether THIS session is bound - the manifest tree is what is being
    protected, not this session's identity."""
    tool = payload.get("tool_name")
    if not isinstance(tool, str) or tool.strip().lower() not in WRITE_TOOL_NAMES:
        return None
    root = wslib.state_root(vault)
    cwd = payload.get("cwd") or vault
    for raw_path in extract_paths(payload.get("tool_input")):
        target = raw_path if os.path.isabs(raw_path) else os.path.join(cwd, raw_path)
        if os.path.basename(target).strip().lower() != MANIFEST_FILENAME:
            continue
        if is_under(target, root):
            return target
    return None


def any_scope_under(root):
    """The first existing `<state-root>/<dir>/ballast.json`, in sorted
    directory order, or None.

    Ballast's PreToolUse gate reads exactly two things from the scope it is
    handed - the state root (the parent of the scope's own directory) and
    `approval_ttl_seconds` - so for a gate check against a target under THIS
    state root, every scope under it answers the same. Creates nothing: a
    scope file is written for a BOUND session by ensure_scope, never by a
    gate check."""
    try:
        with os.scandir(root) as entries:
            names = sorted(e.name for e in entries if e.is_dir())
    except OSError:
        return None
    for name in names:
        candidate = os.path.join(root, name, "ballast.json")
        if os.path.isfile(candidate):
            return candidate
    return None


def gate_scope_for(payload, vault):
    """The ballast.json Ballast's PreToolUse gate runs against when THIS
    session is unbound - resolved from the WRITE TARGET rather than from the
    session - or None when there is nothing here to gate.

    Only a Write/Edit/NotebookEdit whose target lies under the state root
    can reach a gated file, so everything else returns None and keeps the
    unbound no-op intact. WHICH filenames are gated stays Ballast's call and
    is deliberately not restated here; this resolves only which scope
    answers the question.

    Honest limit: with no ballast.json anywhere under the state root there
    is no scope to answer with, so the write passes ungated. That state
    ends at the first bound session's own SessionStart, which is what
    writes one (ensure_scope)."""
    tool = payload.get("tool_name")
    if not isinstance(tool, str) or tool.strip().lower() not in WRITE_TOOL_NAMES:
        return None
    root = wslib.state_root(vault)
    cwd = payload.get("cwd") or vault
    targets = []
    for raw_path in extract_paths(payload.get("tool_input")):
        target = raw_path if os.path.isabs(raw_path) else os.path.join(cwd, raw_path)
        if is_under(target, root):
            targets.append(target)
    if not targets:
        return None
    for target in targets:
        beside = os.path.join(os.path.dirname(target), "ballast.json")
        if os.path.isfile(beside):
            return beside
    return any_scope_under(root)


def ensure_scope(ws_dir):
    """The workstream's own ballast.json: written from the documented
    default on first use, MIGRATED when it still holds the 0.1.1 default
    byte-for-byte, and otherwise never touched."""
    scope_path = os.path.join(ws_dir, "ballast.json")
    if os.path.isfile(scope_path):
        try:
            with open(scope_path, "r", encoding="utf-8", newline="") as handle:
                existing = handle.read()
        except OSError:
            return scope_path   # unreadable: ballast.py falls back to its own defaults
        if existing.replace("\r\n", "\n") != OLD_DEFAULT_SCOPE_TEXT:
            return scope_path   # customized, or already current - leave it alone
    try:
        wslib.atomic_write_lf(scope_path, DEFAULT_SCOPE_TEXT)
    except OSError:
        pass   # ballast.py falls back to its own built-in minimal defaults if unreadable
    return scope_path


def resolve_scope(vault, session_id, scope_kind):
    """The ballast.json this invocation acts on, or None to no-op.

    `global` needs BOTH a bound session (an unbound session has no
    workstream to read the shared rules on behalf of) and an existing
    `<state-root>/_global/ballast.json`; it never creates either."""
    _born_session, ws_dir = wslib.resolve_bound_dir(vault, session_id)
    if not ws_dir:
        return None
    if scope_kind == "global":
        global_scope = os.path.join(wslib.state_root(vault), GLOBAL_DIRNAME,
                                    "ballast.json")
        return global_scope if os.path.isfile(global_scope) else None
    return ensure_scope(ws_dir)


def main(argv):
    event, plugin_root_arg, part, scope_kind = parse_args(argv)
    plugin_root = resolve_plugin_root(plugin_root_arg)
    raw_stdin = read_stdin_bytes()

    try:
        payload = json.loads(raw_stdin.decode("utf-8", "replace")) if raw_stdin else {}
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    session_id = payload.get("session_id")

    vault = os.getcwd()

    if event == "PreToolUse":
        refused = manifest_write_refused(payload, vault)
        if refused:
            sys.stderr.write(WORKSTREAM_MANIFEST_REFUSAL % refused + "\n")
            return REFUSAL_EXIT

    scope_path = resolve_scope(vault, session_id, scope_kind)
    if not scope_path and event == "PreToolUse" and scope_kind != "global":
        # The gate binds FILES, not sessions (module docstring): an unbound
        # session writing under the state root still passes through it,
        # with the scope resolved from the target it is writing.
        scope_path = gate_scope_for(payload, vault)
    if not scope_path:
        return 0   # unbound, or a global delivery with no _global/ seeded

    shim = os.path.join(plugin_root, "shim", "ballast-shim.py")
    if not os.path.isfile(shim):
        sys.stderr.write("ballast-dispatch: shim not found at %s - ballast event "
                         "skipped this session (fail open).\n" % shim)
        return 0

    shim_argv = [sys.executable, shim, event, "--scope", scope_path]
    if part:
        shim_argv += ["--part", part]
    try:
        proc = subprocess.run(shim_argv, input=raw_stdin,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if proc.stdout:
            sys.stdout.buffer.write(proc.stdout)
        if proc.stderr:
            sys.stderr.buffer.write(proc.stderr)
    except Exception as exc:
        sys.stderr.write("ballast-dispatch: forwarding to the shim failed (fail open): %r\n" % exc)
        return 0
    # VERBATIM, never `return 0`: ballast's PreToolUse approval refusal is
    # an exit 2 the shim already forwards, and swallowing it here would
    # turn every refusal into an allow.
    return proc.returncode


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except SystemExit:
        raise
    except BaseException:
        sys.exit(0)
