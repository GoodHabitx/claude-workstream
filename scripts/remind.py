#!/usr/bin/env python3
"""remind.py - the workstream plugin's own UserPromptSubmit hook (X4).

Re-shows the identity block every N=25 turns (measured p90 turn-gap
census - a LOCAL default, not model-validated; see docs/workstream-model.md
Open note). Dedup via the boot-stamped counter (boot.py resets it to 0 on
every SessionStart fire, including post-compaction, so identity does not
double-show right after a fresh boot already showed it - X4/04f step 5).

Ballast's own re-ground fires on this SAME UserPromptSubmit slot and MUST
run first (hooks.json lists Ballast's ballast-dispatch.py entry ABOVE this
one) - this file has no ordering logic of its own to enforce that; it is
purely a hooks.json wiring fact.

Unbound sessions: near-zero-cost no-op (one sidecar lookup, no counter
file touched, no output) - the overwhelming common case.

The whole stdout of this hook command passes through
workstream_lib.guard_delivery before it is written (B5) - the same bound
boot.py and precompact.py carry, for the same reason.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import workstream_lib as wslib
import boot as workstream_boot

REMIND_EVERY_N = 25


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


def counter_path(vault, session_id, root=None):
    return os.path.join(wslib.sessions_root(vault, root), session_id + ".remind.json")


def bump_and_check(vault, session_id, root=None):
    """Increment the turn counter; return True (and reset to 0) once it
    reaches REMIND_EVERY_N, else False. A missing/broken counter file
    starts fresh at 0 -> 1 rather than crashing (fail-soft bookkeeping,
    same discipline as every other primitive here)."""
    path = counter_path(vault, session_id, root)
    data, _err = wslib.read_json(path)
    count = data.get("count") if isinstance(data, dict) else 0
    if not isinstance(count, int):
        count = 0
    count += 1
    if count >= REMIND_EVERY_N:
        try:
            wslib.atomic_write_json(path, {"count": 0})
        except OSError:
            pass
        return True
    try:
        wslib.atomic_write_json(path, {"count": count})
    except OSError:
        pass
    return False


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace", newline="\n")

    payload = read_stdin_payload()
    session_id = payload.get("session_id")
    if not (isinstance(session_id, str) and wslib.SAFE_ID_RE.match(session_id)):
        return 0

    vault = os.getcwd()
    born_session, ws_dir = wslib.resolve_bound_dir(vault, session_id)
    if not ws_dir:
        return 0   # unbound: near-zero-cost no-op

    if bump_and_check(vault, session_id):
        block = workstream_boot.render_identity_block(vault, born_session, ws_dir)
        sys.stdout.write(wslib.guard_delivery(block + "\n",
                                              "UserPromptSubmit (workstream remind)"))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except BaseException:
        sys.exit(0)
