#!/usr/bin/env python3
"""validate-scope.py PATH/to/ballast.json [PATH ...] [--consumer DIR]

The engine behind `/ballast:check` — a strict, READ-ONLY scope report.

Unlike ballast.py's own event handlers (which fail OPEN on a broken scope
so a session is never blocked by ballast's own bug), this is a standalone
operator tool: it loads each given ballast.json with
ballast_lib.load_scope and reports every ScopeError loudly. Intended for a
consumer's own test suite / CI, or a person checking a new scope by hand
before wiring the hooks.json lines to it.

For each valid scope it reports:

  * ENGINE — this engine's version against the scope's `min_engine`. Each
    event handler no-ops (fails OPEN) when the engine is older than the
    scope was pinned to, so a consumer that pins `min_engine` to the
    ballast release it vendored gets a clean silent no-op from a stale
    engine, not surprising behaviour. This states, once, whether the pin
    holds.
  * PARTS — the projected inline bytes of every SessionStart delivery
    (`hot`, `policy`, `glossary`, and the combined `all`) against the
    harness's ~9,687 B per-command inline ceiling. Projected by running
    the real handler under `--dry-run` with `source=compact`, so hot's
    number is the WORST case: it includes the refresh block and the log
    delta.
  * GATE — whether the approval gate is armed, where its tokens live,
    which switch files are present, and whether a git work tree holding
    the state root would commit those one-time tokens instead of ignoring
    them.
  * TEMPLATES — with `--consumer DIR`, whether that plugin's hooks.json
    wires the gate's PreToolUse entry at all (an armed gate nothing runs
    refuses nothing) and whether its vendored shim can still forward the
    refusal's exit 2, plus the drift lint over that plugin's vendored
    copies of Ballast's skill templates and of the shim itself (which
    must be byte-identical).

Writes nothing, anywhere: every projection runs with ballast's own
`--dry-run` flag set, and the template lint runs `sync-templates.py
--check`.

Exit codes: 0 every scope valid; 1 at least one scope invalid; 2 usage
error (no paths given).

(interpreter caveat: neither `python3` nor `python` resolves on every host
— on a Windows host use `py -3`, or the same loop the hooks use:
`for c in python3 python "py -3" py; do ...; done`.)
"""
import contextlib
import io
import json
import os
import subprocess
import sys

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPTS_DIR)
import ballast_lib as bl
import ballast

SYNC_TEMPLATES = os.path.join(SCRIPTS_DIR, "sync-templates.py")

# Worst case on purpose: a compact source is the only one that carries the
# refresh block and the log delta, so this is the largest `hot` ever gets.
PROJECTION_PAYLOAD = {"source": "compact"}


def project_part(scope, part):
    """The exact bytes the `--part` delivery would put on stdout, measured
    by running the real handler with writes suppressed. Returns
    `(text, error)`."""
    bl.DRY_RUN = True
    ballast.PART = part
    ballast.DELIVERY_LABEL = "SessionStart --part %s" % part
    buffer = io.StringIO()
    try:
        with contextlib.redirect_stdout(buffer):
            ballast.cmd_session_start(scope, PROJECTION_PAYLOAD)
    except Exception as exc:            # a projection must never crash the report
        return None, exc
    return buffer.getvalue(), None


def report_version(scope):
    """The engine version pin. Each event handler no-ops (fails open) when
    this engine is older than the scope's declared `min_engine`, so a
    consumer that pins `min_engine` to the ballast release it vendored gets
    a clean silent no-op from a stale engine rather than surprising
    behaviour. States, once, whether that pin holds."""
    engine = bl.ENGINE_VERSION
    need = scope["min_engine"]
    if bl.semver_at_least(engine, need):
        print("      engine %s vs scope min_engine %s — OK" % (engine, need))
    else:
        print("      engine %s vs scope min_engine %s — STALE: this engine "
              "is older than the scope's min_engine, so every event fails "
              "open and no-ops until this consumer re-vendors a compatible "
              "engine" % (engine, need))


def report_parts(scope):
    print("      parts (projected inline bytes vs the %d B ceiling; "
          "worst case, source=compact):" % bl.INLINE_CEILING)
    for part in ballast.SESSION_START_PARTS[1:] + ("all",):
        text, error = project_part(scope, part)
        if error is not None:
            print("        %-9s ERROR %r" % (part, error))
            continue
        size = len(text.encode("utf-8"))
        # The guard trims every delivery to fit, so a part can never
        # ARRIVE over the ceiling — what matters here is whether anything
        # had to be cut to keep it that way, and BY WHICH budget: the
        # delivery guard, a per-artifact cap and a per-slot cap all write
        # a marker with the same prefix but call for different fixes.
        causes = bl.trim_causes(text)
        print("        %-9s %6d B  %s"
              % (part, size,
                 "TRIMMED — " + "; ".join(causes) if causes else "ok"))


def git_ignore_gap(state_root):
    """The gate's own files that a git work tree containing `state_root`
    would COMMIT rather than ignore. Returns `(rel_prefix, paths)`, where
    `rel_prefix` is the state root spelled relative to that repo's root —
    or None when there is no work tree here, or no usable git. This report
    is read-only and never depends on git being installed."""
    if not os.path.isdir(state_root):
        return None
    try:
        top = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree", "--show-toplevel"],
            cwd=state_root, stdin=subprocess.DEVNULL, capture_output=True)
    except OSError:
        return None
    lines = top.stdout.decode("utf-8", "replace").split()
    if top.returncode != 0 or len(lines) < 2 or lines[0] != "true":
        return None
    try:
        rel_prefix = os.path.relpath(state_root, lines[1]).replace(os.sep, "/")
    except ValueError:
        return None
    unignored = []
    for path in (os.path.join(bl.approvals_dir(state_root), "example.token"),
                 os.path.join(state_root, bl.GATE_DISABLED_FILENAME),
                 os.path.join(state_root,
                              bl.GLOSSARY_GATE_DISABLED_FILENAME)):
        try:
            proc = subprocess.run(["git", "check-ignore", "-q", path],
                                  cwd=state_root, stdin=subprocess.DEVNULL,
                                  capture_output=True)
        except OSError:
            return None
        if proc.returncode == 1:      # 0 ignored · 1 not ignored · 128 error
            unignored.append(path)
    return rel_prefix, unignored


def report_ignore_rules(state_root):
    """Warn when the gate's one-time tokens and switch files would be
    committed. A token is a permission slip that lives for at most
    `approval_ttl_seconds`; in an auto-committing repo an unignored one
    lands as a commit and its consumption as a second commit deleting it.
    An ignore rule one directory further down does not cover them."""
    gap = git_ignore_gap(state_root)
    if not gap:
        return
    rel_prefix, unignored = gap
    if not unignored:
        return
    prefix = "" if rel_prefix in (".", "") else rel_prefix + "/"
    print("        WARN: this state root is inside a git work tree that "
          "does not ignore the gate's own files (%d of 3 unignored) — "
          "approval tokens and switch files are not history. Add to that "
          "repo's .gitignore:" % len(unignored))
    print("          %s%s/" % (prefix, bl.APPROVALS_DIRNAME))
    print("          %sballast-gate*.disabled" % prefix)


def report_gate(scope):
    state_root = scope["state_root"]
    if bl.gate_disabled(state_root):
        print("      gate: OFF — %s exists under %s; every write to a gated "
              "file is allowed"
              % (bl.GATE_DISABLED_FILENAME, state_root))
    else:
        print("      gate: ARMED for %s under %s"
              % (", ".join(bl.GATED_FILENAMES), state_root))
    if bl.glossary_gate_disabled(state_root):
        print("        glossary toggle: OFF — %s exists, glossary.md is "
              "ungated" % bl.GLOSSARY_GATE_DISABLED_FILENAME)
    directory = bl.approvals_dir(state_root)
    if os.path.isdir(directory):
        tokens = [n for n in os.listdir(directory) if n.endswith(".token")]
        print("        approvals: %d token(s) in %s (TTL %d s)"
              % (len(tokens), directory, scope["approval_ttl_seconds"]))
    else:
        print("        approvals: none minted yet (%s)" % directory)
    report_ignore_rules(state_root)


def report_consumer_shim(consumer):
    """Whether the consumer's own copy of the shim can carry a refusal at
    all. The shim is vendored byte-identical by contract; a copy from
    before 0.1.2 has no exit-2 forwarding, so every approval refusal
    reaches the harness as an allow — the gate looks wired and enforces
    nothing. Read-only: `sync-templates.py` re-copies it."""
    path = os.path.join(consumer, "shim", "ballast-shim.py")
    try:
        with open(path, "rb") as handle:
            text = handle.read().decode("utf-8", "replace")
    except OSError:
        print("        NO SHIM at %s — this consumer vendors no copy of "
              "shim/ballast-shim.py; run sync-templates.py to vendor it"
              % path)
        return
    if "REFUSAL_EXIT" not in text:
        print("        STALE SHIM: %s has no REFUSAL_EXIT — it predates the "
              "exit-2 forwarding, so EVERY approval refusal reaches the "
              "harness as an allow. Re-copy shim/ballast-shim.py "
              "(sync-templates.py does it byte-identical)." % path)


def report_gate_wiring(consumer):
    """Whether the named consumer's hooks.json runs the gate at all. The
    refusal is worth nothing unless a PreToolUse entry invokes it, unless
    the consumer's shim can forward exit 2, and — for a consumer that
    wraps the shim to resolve its scope at runtime — unless that wrapper
    exits with the shim's own code (one that copies stdout and stderr and
    then returns 0 turns every refusal into an allow). This report can see
    the first two; the third is stated so the operator checks it."""
    path = os.path.join(consumer, "hooks", "hooks.json")
    print("      gate wiring: %s" % path)
    report_consumer_shim(consumer)
    try:
        with open(path, "rb") as handle:
            data = json.loads(handle.read().decode("utf-8", "replace"))
    except OSError:
        print("        no hooks/hooks.json under %s — nothing wires the "
              "gate here" % consumer)
        return
    except ValueError as exc:
        print("        ERROR could not parse it: %s" % exc)
        return
    # A real hooks.json nests events under a top-level "hooks" key
    # ({"hooks": {"PreToolUse": [...]}}); the paste-in snippet is a bare
    # event map. Read under "hooks" when present, else fall back to the
    # top level so both shapes resolve.
    container = data.get("hooks") if isinstance(data, dict) \
        and isinstance(data.get("hooks"), dict) else data
    entries = container.get("PreToolUse") if isinstance(container, dict) else None
    if not entries:
        print("        NO PreToolUse ENTRY — the gate is armed in ballast "
              "but nothing runs it for this consumer, so every write to a "
              "gated file is allowed. Paste the PreToolUse entry from "
              "docs/consumer-hooks.json.snippet.")
        return
    print("        PreToolUse wired (%d entr%s). If that command is your "
          "own wrapper rather than the shim, it MUST exit with the shim's "
          "code — `return proc.returncode`, never `return 0`, or the "
          "refusal never reaches the harness."
          % (len(entries), "y" if len(entries) == 1 else "ies"))


def report_templates(consumer):
    print("      templates: drift lint against %s" % consumer)
    if not os.path.isfile(SYNC_TEMPLATES):
        print("        SKIPPED — the vendored-copy drift lint compares "
              "against the ballast source, so it is an author-side check; "
              "sync-templates.py is not vendored into a consumer. Run it "
              "from the ballast repo (this consumer check needs no source).")
        return
    try:
        proc = subprocess.run(
            [sys.executable, SYNC_TEMPLATES, consumer, "--check"],
            stdin=subprocess.DEVNULL, capture_output=True)
    except OSError as exc:
        print("        ERROR could not run sync-templates.py: %r" % exc)
        return
    for line in proc.stdout.decode("utf-8", "replace").splitlines():
        print("        %s" % line)
    if proc.returncode:
        print("        -> re-run sync-templates.py without --check to "
              "re-vendor (the shim is copied byte-identical; consumer "
              "extras below a template's marker are preserved)")


def scope_warnings(scope):
    warnings = []
    if not os.path.isdir(scope["root"]):
        warnings.append("root does not exist yet: %s" % scope["root"])
    for label, path, cap in (
            ("policy.md", scope["policy_path"], scope["policy_cap_bytes"]),
            ("glossary.md", scope["glossary_path"],
             scope["glossary_cap_bytes"])):
        if path and os.path.isfile(path) and os.path.getsize(path) > cap:
            warnings.append("%s is %d B, over its %d B cap already"
                            % (label, os.path.getsize(path), cap))
    if scope["hot_path"] and os.path.isfile(scope["hot_path"]):
        size = os.path.getsize(scope["hot_path"])
        if size > scope["hot_cap_bytes"]:
            warnings.append("hot.md is %d B, over its %d B cap already "
                            "(next refresh must consolidate)"
                            % (size, scope["hot_cap_bytes"]))
    return warnings


def main(argv):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace", newline="\n")

    paths = []
    consumer = None
    i = 0
    while i < len(argv):
        if argv[i] == "--consumer" and i + 1 < len(argv):
            consumer = argv[i + 1]
            i += 2
        else:
            paths.append(argv[i])
            i += 1

    if not paths:
        sys.stderr.write("usage: validate-scope.py PATH/to/ballast.json "
                         "[PATH ...] [--consumer DIR]\n")
        return 2

    ok = True
    for path in paths:
        try:
            scope = bl.load_scope(path)
        except bl.ScopeError as exc:
            print("FAIL  %s\n      %s" % (path, exc))
            ok = False
            continue
        except Exception as exc:
            print("ERROR %s\n      unexpected: %r" % (path, exc))
            ok = False
            continue

        warnings = scope_warnings(scope)
        print("%s  %s" % ("WARN " if warnings else "OK   ", path))
        for warning in warnings:
            print("      %s" % warning)
        report_version(scope)
        report_parts(scope)
        report_gate(scope)
        if consumer:
            report_gate_wiring(consumer)
            report_templates(consumer)

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
