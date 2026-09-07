#!/usr/bin/env python3
"""ballast-dispatch.py EVENT [PLUGIN_ROOT] - the wrapper hook that resolves
THIS session's bound workstream scope and forwards to shim/ballast-shim.py
with a resolved --scope path.

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

Unbound sessions (no sidecar, or born_session doesn't resolve to an
existing dir - the common case for most sessions/most turns): near-zero-
cost no-op, exit 0, no ballast invocation at all, no output.

Bound sessions: ensures a ballast.json exists in the workstream's own dir
(writing the documented default scope - fixtures/scope-example/
ballast.json verbatim - on first use only; a scope a workstream skill has
already customized is left untouched), then execs the shim with
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

# Verbatim copy of ballast's fixtures/scope-example/ballast.json (docs/
# ballast.json.md) - the workstream scope IS the documented default scope,
# since 02/04g's hot required_slots are exactly the nine names ballast.json
# already defaults to. A workstream that needs a different scope can edit
# its own <ws_dir>/ballast.json after this first write; this wrapper never
# overwrites an existing one.
DEFAULT_SCOPE = {
    "root": ".",
    "log": "log.md",
    "hot": "hot.md",
    "index": "index.md",
    "policy": "policy.md",
    "regen": None,
    "hot_cap_bytes": 1024,
    "policy_cap_bytes": 7168,
    "required_slots": [
        "focus", "next", "blocked", "updated",
        "done", "looping", "progressing", "who-acts-next", "stall",
    ],
    "significant_write_rule": "path-under-root",
    "reground_interval_turns": 25,
    "min_engine": "0.1.0",
}


def read_stdin_bytes():
    try:
        if sys.stdin is None or sys.stdin.isatty():
            return b""
        return sys.stdin.buffer.read()
    except Exception:
        return b""


def resolve_plugin_root(argv):
    if len(argv) > 1 and argv[1]:
        return argv[1]
    env_root = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if env_root:
        return env_root
    return wslib.plugin_root()


def ensure_scope(ws_dir):
    scope_path = os.path.join(ws_dir, "ballast.json")
    if os.path.isfile(scope_path):
        return scope_path
    try:
        wslib.atomic_write_lf(scope_path, json.dumps(DEFAULT_SCOPE, indent=2) + "\n")
    except OSError:
        pass   # ballast.py falls back to its own built-in minimal defaults if unreadable
    return scope_path


def main(argv):
    event = argv[0] if argv else "SessionStart"
    plugin_root = resolve_plugin_root(argv)
    raw_stdin = read_stdin_bytes()

    try:
        payload = json.loads(raw_stdin.decode("utf-8", "replace")) if raw_stdin else {}
    except Exception:
        payload = {}
    session_id = payload.get("session_id") if isinstance(payload, dict) else None

    vault = os.getcwd()
    born_session, ws_dir = wslib.resolve_bound_dir(vault, session_id)
    if not ws_dir:
        return 0   # unbound: near-zero-cost no-op, no ballast invocation

    scope_path = ensure_scope(ws_dir)

    shim = os.path.join(plugin_root, "shim", "ballast-shim.py")
    if not os.path.isfile(shim):
        sys.stderr.write("ballast-dispatch: shim not found at %s - ballast event "
                         "skipped this session (fail open).\n" % shim)
        return 0

    try:
        proc = subprocess.run([sys.executable, shim, event, "--scope", scope_path],
                              input=raw_stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if proc.stdout:
            sys.stdout.buffer.write(proc.stdout)
        if proc.stderr:
            sys.stderr.buffer.write(proc.stderr)
    except Exception as exc:
        sys.stderr.write("ballast-dispatch: forwarding to the shim failed (fail open): %r\n" % exc)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except SystemExit:
        raise
    except BaseException:
        sys.exit(0)
