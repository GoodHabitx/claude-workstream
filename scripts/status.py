#!/usr/bin/env python3
"""status.py [ARTIFACT] [--born-session ID | --session TRANSCRIPT_ID] -
print a workstream's five artifacts, their sizes against their caps, and
their contents VERBATIM.

The five, in this order: `manifest` (workstream.json), `hot` (hot.md),
`policy` (policy.md), `global-policy` (the shared
`<state-root>/_global/policy.md`), `glossary` (glossary.md). No
argument prints all five; one argument prints that one. A file that does
not exist prints `absent`; a file field the scope sets to `null` prints
`disabled`.

This script PRINTS. It does not summarize, rank, diagnose or advise, and
it never writes. Everything it emits is either read from a file or
arithmetic over two numbers - the point is that a person (or a session
that has lost the thread) can see exactly what its own continuity files
say without a model standing between them and the bytes.

Caps come from the scope's own `ballast.json`, falling back to the
defaults ballast documents in `docs/ballast.json.md` when a key is absent
- which is what an older, un-migrated scope looks like, and is worth
seeing. hot.md additionally reports each slot's length against schema
v2's per-slot cap; those caps are read from the vendored ballast
(`ballast_lib.HOT_SLOT_SPECS`, resolved vendored-first), so there is one
source of truth for them. If no ballast engine resolves at all, the slot
lengths still print and the caps read `unknown`.

(interpreter caveat: neither `python3` nor `python` resolves on every
host - on Windows use `py -3`.)
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import workstream_lib as wslib

ARTIFACTS = ("manifest", "hot", "policy", "global-policy", "glossary")

GLOBAL_DIRNAME = "_global"

# ballast.json's own documented defaults (docs/ballast.json.md), used only
# when the scope does not declare the key - an un-migrated 0.1.1 scope
# declares no glossary, and seeing that is the point.
DEFAULT_FILENAMES = {
    "hot": "hot.md",
    "policy": "policy.md",
    "glossary": "glossary.md",
}
DEFAULT_CAPS = {
    "hot_cap_bytes": 4096,
    "policy_cap_bytes": 7168,
    "glossary_cap_bytes": 4096,
}
CAP_KEY = {
    "hot": "hot_cap_bytes",
    "policy": "policy_cap_bytes",
    "global-policy": "policy_cap_bytes",
    "glossary": "glossary_cap_bytes",
}

# A runaway file must never be slurped whole into memory, cap or no cap.
# This is the read bound, not a budget: over it, the contents print
# truncated and say so.
READ_LIMIT = 1024 * 1024


def _fmt(n):
    return "{:,}".format(n)


def size_line(raw, cap, cap_key):
    """`<n> B / <cap> B cap (<key>)`, plus the arithmetic when over. Two
    numbers and their difference - no verdict."""
    line = "%s B / %s B cap (%s)" % (_fmt(len(raw)), _fmt(cap), cap_key)
    if len(raw) > cap:
        line += " - OVER by %s B" % _fmt(len(raw) - cap)
    return line


def load_scope(scope_path):
    """`(scope_dict, note)`. A missing or unreadable ballast.json is not an
    error here - it means the defaults apply, and the note says so."""
    if not os.path.isfile(scope_path):
        return {}, "no ballast.json at this scope - ballast's own defaults apply"
    data, err = wslib.read_json(scope_path, cap=READ_LIMIT)
    if data is None:
        return {}, "ballast.json is %s - ballast's own defaults apply" % (err or "missing")
    return data, None


def scope_file(scope, key, ws_dir):
    """`(path, disabled_note)` for one file class the scope declares.
    An explicit `null` means the class is disabled for this scope, which
    is a real answer and not an absence."""
    if key in scope and scope[key] is None:
        return None, 'disabled - this scope sets "%s": null' % key
    name = scope.get(key)
    if not isinstance(name, str) or not name.strip():
        name = DEFAULT_FILENAMES[key]
    return (name if os.path.isabs(name) else os.path.join(ws_dir, name)), None


def hot_slot_lines(text):
    """One `slot: <len>/<cap>` line per schema-v2 slot, in ballast's own
    table order. Falls back to printing lengths with `unknown` caps when
    no ballast engine resolves (vendored or installed) - the lengths are
    still the useful half."""
    try:
        ballast_lib_path = wslib.ballast_script("ballast_lib.py")
        if not ballast_lib_path:
            raise ImportError("ballast_lib.py not found")
        import importlib.util
        spec = importlib.util.spec_from_file_location("ballast_lib", ballast_lib_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        specs = [(name, cap) for name, cap, _empty in module.HOT_SLOT_SPECS]
        slots = module.parse_hot_slots(text)
    except Exception as exc:
        return (["per-slot caps unknown (ballast not importable: %s) - lengths only:"
                 % type(exc).__name__]
                + ["  %s: %d chars" % (k, len(v))
                   for k, v in sorted(_naive_slots(text).items())])
    out = []
    for name, cap in specs:
        value = slots.get(name)
        if value is None:
            out.append("  %s: absent" % name)
            continue
        line = "  %s: %d/%d chars" % (name, len(value), cap)
        if len(value) > cap:
            line += " - OVER by %d" % (len(value) - cap)
        out.append(line)
    return out


def _naive_slots(text):
    """The `key: value` lines, parsed without ballast. Only used to keep
    the slot lengths printable when ballast is not installed."""
    slots = {}
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("<!--") or ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip().lower().replace("_", "-").replace(" ", "-")
        if key and " " not in key:
            slots.setdefault(key, value.strip())
    return slots


def read_verbatim(path):
    """`(text, raw_bytes, note)` - the file as it is on disk, bounded."""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as handle:
            raw = handle.read(READ_LIMIT)
    except OSError as exc:
        return None, b"", "unreadable: %s" % exc
    note = None
    if size > READ_LIMIT:
        note = ("contents truncated at the %s B read bound (file is %s B)"
                % (_fmt(READ_LIMIT), _fmt(size)))
    return raw.decode("utf-8", "replace"), raw, note


def render_artifact(name, path, vault, cap, cap_key, disabled_note):
    rel = path and os.path.relpath(path, vault).replace(os.sep, "/")
    out = ["### %s%s" % (name, " - %s" % rel if rel else "")]
    if disabled_note:
        out.append(disabled_note)
        return out
    if not os.path.isfile(path):
        out.append("absent")
        return out
    text, raw, note = read_verbatim(path)
    if text is None:
        out.append(note)
        return out
    out.append(size_line(raw, cap, cap_key))
    if name == "hot":
        out.extend(hot_slot_lines(text))
    if note:
        out.append(note)
    out.append("")
    out.append(text.rstrip("\n"))
    return out


def build(vault, born_session, only=None, root=None):
    ws_dir = os.path.join(wslib.state_root(vault, root), born_session)
    scope, scope_note = load_scope(os.path.join(ws_dir, "ballast.json"))
    state_root = wslib.state_root(vault, root)
    global_dir = os.path.join(state_root, GLOBAL_DIRNAME)
    global_scope, _ = load_scope(os.path.join(global_dir, "ballast.json"))

    lines = ["## workstream:status - %s (%s)"
             % (born_session,
                os.path.relpath(ws_dir, vault).replace(os.sep, "/"))]
    if scope_note:
        lines.append(scope_note)
    lines.append("")

    for name in ARTIFACTS:
        if only and name != only:
            continue
        if name == "manifest":
            block = render_artifact(
                name, wslib.manifest_path_for(vault, born_session, root), vault,
                wslib.MANIFEST_CAP, "workstream_lib.MANIFEST_CAP", None)
        elif name == "global-policy":
            path, disabled = scope_file(global_scope, "policy", global_dir)
            if not os.path.isfile(os.path.join(global_dir, "ballast.json")) \
                    and not os.path.isdir(global_dir):
                block = ["### global-policy - %s/policy.md"
                         % os.path.relpath(global_dir, vault).replace(os.sep, "/"),
                         "absent - no _global/ scope has been seeded "
                         "(/workstream:global-policy creates it)"]
            else:
                cap = global_scope.get("policy_cap_bytes",
                                       DEFAULT_CAPS["policy_cap_bytes"])
                block = render_artifact(name, path, vault, cap,
                                        "policy_cap_bytes", disabled)
        else:
            path, disabled = scope_file(scope, name, ws_dir)
            cap_key = CAP_KEY[name]
            cap = scope.get(cap_key, DEFAULT_CAPS[cap_key])
            block = render_artifact(name, path, vault, cap, cap_key, disabled)
        lines.extend(block)
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def main(argv):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace", newline="\n")

    parser = argparse.ArgumentParser(prog="status.py")
    parser.add_argument("artifact", nargs="?", choices=ARTIFACTS)
    parser.add_argument("--born-session", dest="born_session")
    parser.add_argument("--session", dest="session",
                        help="a transcript session_id, resolved via its sidecar")
    args = parser.parse_args(argv)

    vault = os.getcwd()
    born_session = args.born_session
    if not born_session and args.session:
        born_session, ws_dir = wslib.resolve_bound_dir(vault, args.session)
        if not ws_dir:
            sys.stderr.write("status: session %r is not bound to a workstream "
                             "(no sidecar, or it points at a dir that is gone) "
                             "- run /workstream:adopt or /workstream:sidecar.\n"
                             % args.session)
            return 1
    if not born_session:
        sys.stderr.write("status: needs --born-session ID or --session "
                         "TRANSCRIPT_ID (this session's own id is the one "
                         "boot.py echoed as workstream-session-id).\n")
        return 2
    if not wslib.SAFE_ID_RE.match(born_session):
        sys.stderr.write("status: unsafe born_session %r\n" % born_session)
        return 2

    ws_dir = os.path.join(wslib.state_root(vault), born_session)
    if not os.path.isdir(ws_dir):
        sys.stderr.write("status: no workstream directory at %s\n" % ws_dir)
        return 1

    sys.stdout.write(build(vault, born_session, args.artifact))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
