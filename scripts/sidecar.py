#!/usr/bin/env python3
"""sidecar.py - the sidecar primitive (I5, L3): a small json file that maps
THIS conversation's rotating transcript id -> the workstream's permanent
born_session. Disposable by default; this primitive is the NEW guardian
that also WRITES and SELF-HEALS it (decided, I5/L3 - the target this cos
0.20.0 never built: rebind is retired, and the one adjacent real need - a
missing/stale sidecar - is this primitive's job, mechanically, not a human
ritual).

Importable functions for other scripts (boot.py, remind.py, precompact.py
read via workstream_lib.resolve_bound_dir directly - a read-only fast
path that does not import this file at all, keeping the near-zero-cost
unbound case free of this module's overhead); this file's OWN job is the
WRITE + SELF-HEAL side, used by the lifecycle verbs (adopt/fork - built by
the second builder) and by connect's Inv-6 fix.

CLI (used by skills via subprocess, and by tests):
    sidecar.py resolve <session_id>
        -> prints {"born_session": ..., "ws_dir": ...} or {} ; exit 0 if
           resolved, exit 1 if not (mirrors workstream_lib.resolve_bound_dir)
    sidecar.py write <session_id> <born_session> [--name NAME] [--focus FOCUS]
        -> creates/overwrites the sidecar for session_id (single sidecar per
           transcript id - this IS the writer, not a peer to guard against)
    sidecar.py self-heal <session_id> <born_session> [--name NAME] [--focus FOCUS]
        -> same write, but ALWAYS prints a loud one-line note naming what
           changed (target: a missing/stale sidecar gets a loud line, never
           a silent drop - I5 Build notes)
    sidecar.py check <session_id> <born_session>
        -> connect's Inv-6 (self-heal check): prints "match" / "mismatch:
           <detail>" / "missing"; exit 0 always (a report, not a failure)

Every write goes through atomic_write_lf (workstream_lib) - tmp+replace,
never a torn sidecar.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import workstream_lib as wslib


def resolve(vault, session_id, root=None):
    born_session, ws_dir = wslib.resolve_bound_dir(vault, session_id, root)
    if born_session is None:
        return None
    return {"born_session": born_session, "ws_dir": ws_dir}


def write(vault, session_id, born_session, name=None, focus=None, root=None, dry_run=False):
    """Create/overwrite the sidecar for session_id. Refuses (raises
    ValueError) on an unsafe session_id or born_session - callers pass
    already-validated ids (the harness's own session_id, a manifest's own
    born_session), so this is a defensive belt, not the primary gate.
    `dry_run=True` (spec 4.3) runs every check above but never touches
    disk - see workstream_lib.atomic_write_json."""
    if not (isinstance(session_id, str) and wslib.SAFE_ID_RE.match(session_id)):
        raise ValueError("unsafe session_id: %r" % (session_id,))
    if not (isinstance(born_session, str) and wslib.SAFE_ID_RE.match(born_session)):
        raise ValueError("unsafe born_session: %r" % (born_session,))
    path = wslib.sidecar_path(vault, session_id, root)
    data = {
        "session_id": session_id,
        "born_session": born_session,
        "written_at": wslib.iso_now(),
    }
    if isinstance(name, str) and name.strip():
        data["name"] = name.strip()
    if isinstance(focus, str) and focus.strip():
        data["focus"] = focus.strip()
    wslib.atomic_write_json(path, data, dry_run=dry_run)
    return path


def self_heal(vault, session_id, born_session, name=None, focus=None, root=None):
    """Same write as `write`, but returns a loud human-readable note
    describing what happened (target behavior, I5 Build notes: a missing
    or stale sidecar gets a LOUD line pointing at self-heal, never
    silence)."""
    existing, _err = wslib.read_json(wslib.sidecar_path(vault, session_id, root),
                                     cap=wslib.WS_BINDING_CAP)
    prior = existing.get("born_session") if isinstance(existing, dict) else None
    write(vault, session_id, born_session, name=name, focus=focus, root=root)
    if prior is None:
        return ("sidecar self-heal: session %s had NO sidecar - written, now bound "
                "to workstream %s." % (session_id, born_session))
    if prior == born_session:
        return ("sidecar self-heal: session %s sidecar already pointed at %s - "
                "rewritten (refresh), no change in binding." % (session_id, born_session))
    return ("sidecar self-heal: session %s sidecar pointed at STALE workstream %s - "
            "corrected to %s." % (session_id, prior, born_session))


def check(vault, session_id, expected_born_session, root=None):
    """connect's Inv-6 (renamed self-heal, L3/V-Build-notes): "match" /
    "missing" / "mismatch: was <x>, manifest says <expected>". A pure
    report - never writes; the caller decides whether to call self_heal."""
    data, err = wslib.read_json(wslib.sidecar_path(vault, session_id, root),
                                cap=wslib.WS_BINDING_CAP)
    if data is None:
        return "missing" if err is None else ("missing (%s)" % err)
    actual = data.get("born_session")
    if actual == expected_born_session:
        return "match"
    return "mismatch: sidecar says %r, expected %r" % (actual, expected_born_session)


def main(argv):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace", newline="\n")

    p = argparse.ArgumentParser(prog="sidecar.py")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_resolve = sub.add_parser("resolve")
    p_resolve.add_argument("session_id")

    p_write = sub.add_parser("write")
    p_write.add_argument("session_id")
    p_write.add_argument("born_session")
    p_write.add_argument("--name")
    p_write.add_argument("--focus")
    p_write.add_argument("--dry-run", action="store_true", dest="dry_run")

    p_heal = sub.add_parser("self-heal")
    p_heal.add_argument("session_id")
    p_heal.add_argument("born_session")
    p_heal.add_argument("--name")
    p_heal.add_argument("--focus")

    p_check = sub.add_parser("check")
    p_check.add_argument("session_id")
    p_check.add_argument("born_session")

    args = p.parse_args(argv)
    vault = os.getcwd()

    if args.cmd == "resolve":
        result = resolve(vault, args.session_id)
        print(json.dumps(result or {}))
        return 0 if result else 1

    if args.cmd == "write":
        try:
            path = write(vault, args.session_id, args.born_session, args.name, args.focus,
                        dry_run=args.dry_run)
        except ValueError as e:
            sys.stderr.write("sidecar: %s\n" % e)
            return 2
        print("sidecar: %s%s" % ("DRY-RUN: would write " if args.dry_run else "wrote ", path))
        return 0

    if args.cmd == "self-heal":
        try:
            print(self_heal(vault, args.session_id, args.born_session, args.name, args.focus))
        except ValueError as e:
            sys.stderr.write("sidecar: %s\n" % e)
            return 2
        return 0

    if args.cmd == "check":
        print(check(vault, args.session_id, args.born_session))
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
