#!/usr/bin/env python3
"""glossary.py — the sanctioned writer for glossary.md.

One of the two write primitives behind the approval gate
(`docs/approval-gate.md`). It refuses without a fresh approval token,
consumes the token on success, writes atomically, and appends one line to
log.md. The PreToolUse gate refuses a direct Write/Edit to glossary.md —
unless the glossary toggle disarms it — so this is the only TOOL path that
reaches the file; a shell command that writes it is not refused
(docs/approval-gate.md, "The honest limit").

The split that matters: the ENTRY goes to glossary.md as one line,
`- **term** — gloss`; the REASON goes to log.md. A definition with an
argument attached is two things on one line, and the entries are what get
injected on every boot.

Usage
-----
    glossary.py add    --scope PATH --term TEXT --gloss TEXT --reason TEXT
    glossary.py edit   --scope PATH --term TEXT --gloss TEXT --reason TEXT
    glossary.py delete --scope PATH --term TEXT --reason TEXT

`--reason` is required on every action: an entry is admitted (or evicted)
because of an observed naming failure, and that observation is the thing
worth keeping.

Exit codes: 0 written; 1 refused, or the term was missing/duplicate;
2 usage error.

(interpreter caveat: neither `python3` nor `python` resolves on every host
— on a Windows host use `py -3`, or the same loop the hooks use:
`for c in python3 python "py -3" py; do ...; done`.)
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ballast_lib as bl

USAGE = (
    "usage: glossary.py add    --scope PATH --term TEXT --gloss TEXT "
    "--reason TEXT\n"
    "       glossary.py edit   --scope PATH --term TEXT --gloss TEXT "
    "--reason TEXT\n"
    "       glossary.py delete --scope PATH --term TEXT --reason TEXT\n"
)

OPTIONS = ("--scope", "--term", "--gloss", "--reason")

ENTRY_RE = re.compile(r"^-\s+\*\*(?P<term>[^*]+)\*\*\s*(?:—|--|-)\s*"
                      r"(?P<gloss>.*)$")


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


def entry_line(term, gloss):
    return "- **%s** — %s" % (term.strip(), gloss.strip())


def term_of(line):
    match = ENTRY_RE.match(line.strip())
    return match.group("term").strip() if match else None


def main(argv):
    action, opts = parse_args(argv)
    if action not in ("add", "edit", "delete") or opts is None \
            or not opts.get("scope") or not opts.get("term") \
            or not opts.get("reason"):
        sys.stderr.write(USAGE)
        return 2
    if action in ("add", "edit") and not opts.get("gloss"):
        sys.stderr.write(USAGE)
        return 2

    try:
        scope = bl.load_scope(opts["scope"])
    except bl.ScopeError as exc:
        sys.stderr.write("glossary: %s\n" % exc)
        return 2

    target = scope["glossary_path"]
    if not target:
        sys.stderr.write("glossary: this scope declares no glossary file "
                         "(\"glossary\": null) — nothing to write\n")
        return 2

    term = opts["term"].strip()
    text, _ = bl.read_text(target, cap_bytes=bl.FILE_READ_LIMIT)
    lines = text.splitlines() if text is not None else []
    hits = [i for i, line in enumerate(lines) if term_of(line) == term]

    if action == "add":
        if hits:
            sys.stderr.write("glossary: %r is already defined — use `edit`\n"
                             % term)
            return 1
        lines.append(entry_line(term, opts["gloss"]))
    elif action == "edit":
        if len(hits) != 1:
            sys.stderr.write("glossary: expected exactly one entry for %r, "
                             "found %d\n" % (term, len(hits)))
            return 1
        lines[hits[0]] = entry_line(term, opts["gloss"])
    else:
        if not hits:
            sys.stderr.write("glossary: no entry for %r\n" % term)
            return 1
        for index in reversed(hits):
            del lines[index]

    allowed, message = bl.authorize_write(scope, target)
    if not allowed:
        sys.stderr.write("glossary: %s\n" % message)
        return 1

    bl.write_text_atomic(target, "\n".join(lines).rstrip("\n") + "\n")
    # The entry goes to the file; the REASON goes to the log, never inline.
    bl.log_primitive(scope, "[glossary.py] %s %r — %s"
                     % (action, term, opts["reason"]))
    print("%s %r in %s; %s" % (action, term, target, message))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
