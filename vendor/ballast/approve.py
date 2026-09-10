#!/usr/bin/env python3
"""approve.py — mint, check and consume the approval gate's one-time tokens.

The deterministic half of the gate described in `docs/approval-gate.md`.
It proves that the SANCTIONED PATH was used to write a gated file; what
actually asks a person is the skill template's own procedure, which mints
a token only after showing them the exact text.

Usage
-----
    approve.py mint    --scope PATH/to/ballast.json --file PATH [--field NAME]
    approve.py check   --file PATH [--scope PATH/to/ballast.json]
    approve.py consume --file PATH [--scope PATH/to/ballast.json]

`mint` writes `<state-root>/.ballast-approvals/<sha1 of the target's
canonical path>.token`, holding the ISO UTC mint time and the optional
field name. `<state-root>` is the parent of the scope directory — for a
scope at `<state-root>/<scope-id>/` it is `<state-root>/`.

`check` exits 0 only when a token exists and is younger than the scope's
`approval_ttl_seconds` (default 600). Without `--scope` it resolves the
nearest `.ballast-approvals` directory above the target and uses the
default TTL.

`consume` deletes the token. One approval, one write — never a standing
pass.

Off-switches, both under the state root: `ballast-gate.disabled` allows
everything (one loud stderr line says the gate is off);
`ballast-gate-glossary.disabled` disarms glossary.md alone.

Exit codes: 0 the operation succeeded (for `check`: a fresh token exists,
or the gate is off); 1 refused (no token, expired, or nothing to consume);
2 usage error.

(interpreter caveat: neither `python3` nor `python` resolves on every host
— on a Windows host use `py -3`, or the same loop the hooks use:
`for c in python3 python "py -3" py; do ...; done`.)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ballast_lib as bl

USAGE = (
    "usage: approve.py mint --scope PATH/to/ballast.json --file PATH "
    "[--field NAME]\n"
    "       approve.py check --file PATH [--scope PATH/to/ballast.json]\n"
    "       approve.py consume --file PATH [--scope PATH/to/ballast.json]\n"
)


def parse_args(argv):
    if not argv:
        return None, {}
    action = argv[0].strip().lower()
    opts = {}
    i = 1
    while i < len(argv):
        arg = argv[i]
        if arg in ("--scope", "--file", "--field") and i + 1 < len(argv):
            opts[arg[2:]] = argv[i + 1]
            i += 2
        else:
            return action, None       # unknown or dangling option
    return action, opts


def _scope_or_none(path):
    if not path:
        return None
    try:
        return bl.load_scope(path)
    except bl.ScopeError as exc:
        sys.stderr.write("approve: %s\n" % exc)
        return None


def main(argv):
    action, opts = parse_args(argv)
    if action not in ("mint", "check", "consume") or opts is None \
            or not opts.get("file"):
        sys.stderr.write(USAGE)
        return 2

    target = opts["file"]
    scope = _scope_or_none(opts.get("scope"))
    if opts.get("scope") and scope is None:
        return 2

    state_root = scope["state_root"] if scope else None
    ttl = scope["approval_ttl_seconds"] if scope else bl.DEFAULT_APPROVAL_TTL

    # The master off-switch: with the gate off there is nothing to prove,
    # so every action succeeds and says so loudly on stderr.
    if state_root and bl.gate_disabled(state_root):
        sys.stderr.write(
            "approve: the approval gate is OFF (%s exists under %s) — "
            "every write to a gated file is allowed without a token.\n"
            % (bl.GATE_DISABLED_FILENAME, state_root))
        if action == "check":
            return 0

    if action == "mint":
        if not state_root:
            sys.stderr.write("approve: mint needs --scope (the state root "
                             "is the parent of the scope directory)\n")
            return 2
        path = bl.mint_approval(state_root, target, opts.get("field"))
        print("minted %s" % path)
        print("  target: %s" % bl.canonical_target(target))
        if opts.get("field"):
            print("  field:  %s" % opts["field"])
        print("  expires in %d seconds, and is consumed by the first write"
              % ttl)
        return 0

    record = bl.read_approval(target, state_root)
    fresh = bl.approval_is_fresh(record, ttl)

    if action == "check":
        if fresh:
            print("fresh approval for %s (minted %s)"
                  % (bl.canonical_target(target), record.get("minted")))
            return 0
        print("NO fresh approval for %s%s"
              % (bl.canonical_target(target),
                 " (found one, but it is older than %d seconds)" % ttl
                 if record else ""))
        return 1

    if not record:
        print("nothing to consume for %s" % bl.canonical_target(target))
        return 1
    consumed = bl.consume_approval(record)
    print("%s %s" % ("consumed" if consumed else "could not consume",
                     record.get("_path")))
    return 0 if consumed else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
