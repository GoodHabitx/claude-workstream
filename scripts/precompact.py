#!/usr/bin/env python3
"""precompact.py - the workstream plugin's own PreCompact hook.

Prints a short, deterministic ~0.3 KB note (04f step 7) so a bound
session's workstream identity survives the compaction SUMMARY itself -
distinct from boot.py's re-injection, which already re-shows the full
identity block on the SessionStart that follows compaction (source=compact
also fires boot.py, no matcher). This hook's only job is steering what the
summary preserves, never duplicating that injection.

Unbound sessions (no sidecar, or born_session doesn't resolve to an
existing dir): print nothing, exit 0 - same near-zero-cost discipline as
every other hook here. Read-only end to end - never writes anything.

The whole stdout of this hook command passes through
workstream_lib.guard_delivery before it is written (B5) - the same bound
boot.py and remind.py carry, for the same reason.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import workstream_lib as wslib

NOTE_CAP = 400   # bytes - target ~0.3 KB (hub context table)


def read_stdin_payload():
    try:
        if sys.stdin is None or sys.stdin.isatty():
            return {}
        raw = sys.stdin.read()
    except Exception:
        return {}
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def build_note(vault, born_session, ws_dir, root=None):
    manifest, _err = wslib.read_manifest(vault, born_session, root)
    manifest = manifest or {}
    name = manifest.get("name")
    label = name.strip() if isinstance(name, str) and name.strip() else born_session
    lines = [
        "Workstream compaction note: preserve this workstream's identity "
        "and in-flight work through the summary.",
        "- workstream: %s (%s)" % (label, os.path.relpath(ws_dir, vault).replace(os.sep, "/")),
    ]
    focus = manifest.get("focus")
    if isinstance(focus, str) and focus.strip():
        lines.append("- focus: %s" % focus.strip())
    state = manifest.get("state")
    if isinstance(state, str) and state.strip():
        lines.append("- state: %s" % state.strip())
    lines.append("- preserve any in-flight work, decisions, or open threads "
                "from this session's recent turns relevant to this workstream.")
    note = "\n".join(lines)
    raw = note.encode("utf-8")
    if len(raw) > NOTE_CAP:
        note = raw[:NOTE_CAP].decode("utf-8", "ignore") + "\n[... truncated ...]"
    return note


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace", newline="\n")

    payload = read_stdin_payload()
    vault = os.getcwd()
    session_id = payload.get("session_id")

    born_session, ws_dir = wslib.resolve_bound_dir(vault, session_id)
    if not ws_dir:
        return 0

    note = build_note(vault, born_session, ws_dir)
    sys.stdout.write(wslib.guard_delivery(note + "\n",
                                          "PreCompact (workstream note)"))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except BaseException:
        sys.exit(0)
