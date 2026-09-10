#!/usr/bin/env python3
"""policy.py — the sanctioned writer for a scope's policy.md.

One of the two write primitives behind the approval gate
(`docs/approval-gate.md`). It refuses without a fresh approval token,
consumes the token on success, writes atomically, and appends one line to
log.md. The PreToolUse gate refuses a direct Write/Edit to these files, so
this is the only TOOL path that reaches them — a shell command that writes
the same file is not refused (docs/approval-gate.md, "The honest limit").

Usage
-----
    policy.py set --scope PATH/to/ballast.json --from PATH/to/text
                  [--file policy.md] [--reason TEXT]

`set` REPLACES the whole file with the contents of `--from`. Whole-file
replacement is deliberate: the design's update cadence for standing text
is a periodic full re-statement, not a stream of small incremental edits.

`--file` names which policy file to write; it resolves against the scope
root, and defaults to the scope's own declared `policy` path. `--reason`
is recorded in log.md alongside the write.

Exit codes: 0 written; 1 refused (no fresh approval) or unreadable input;
2 usage error.

(interpreter caveat: neither `python3` nor `python` resolves on every host
— on a Windows host use `py -3`, or the same loop the hooks use:
`for c in python3 python "py -3" py; do ...; done`.)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ballast_lib as bl

USAGE = ("usage: policy.py set --scope PATH/to/ballast.json "
         "--from PATH/to/text [--file NAME] [--reason TEXT]\n")

OPTIONS = ("--scope", "--from", "--file", "--reason")


def parse_args(argv):
    if not argv:
        return None, {}
    action = argv[0].strip().lower()
    opts = {}
    i = 1
    while i < len(argv):
        if argv[i] in OPTIONS and i + 1 < len(argv):
            opts[argv[i][2:]] = argv[i + 1]
            i += 2
        else:
            return action, None
    return action, opts


def main(argv):
    action, opts = parse_args(argv)
    if action != "set" or opts is None or not opts.get("scope") \
            or not opts.get("from"):
        sys.stderr.write(USAGE)
        return 2

    try:
        scope = bl.load_scope(opts["scope"])
    except bl.ScopeError as exc:
        sys.stderr.write("policy: %s\n" % exc)
        return 2

    target = scope["policy_path"]
    if opts.get("file"):
        target = opts["file"] if os.path.isabs(opts["file"]) \
            else os.path.join(scope["root"], opts["file"])
    if not target:
        sys.stderr.write("policy: this scope declares no policy file "
                         "(\"policy\": null) — nothing to write\n")
        return 2

    text, _truncated = bl.read_text(opts["from"])
    if text is None:
        sys.stderr.write("policy: cannot read --from %s\n" % opts["from"])
        return 1

    allowed, message = bl.authorize_write(scope, target)
    if not allowed:
        sys.stderr.write("policy: %s\n" % message)
        return 1

    bl.write_text_atomic(target, text if text.endswith("\n") else text + "\n")
    detail = " — %s" % opts["reason"] if opts.get("reason") else ""
    bl.log_primitive(scope, "[policy.py] set %s (%d B)%s"
                     % (os.path.basename(target),
                        len(text.encode("utf-8")), detail))
    print("wrote %s (%d B); %s"
          % (target, len(text.encode("utf-8")), message))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
