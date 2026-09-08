#!/usr/bin/env python3
"""boot.py - the workstream plugin's own SessionStart hook (X2, X3, V1).

Fires on EVERY SessionStart source, compaction included (no matcher, same
as cos-boot.py - the injection must re-fire after compaction, since a
long-context session's own instructions decay across it). Emits, in order:

  1. This plugin's OWN session-id echo line, `<!-- workstream-session-id:
     ... -->` (V1 - a differently-named line from cos-boot's
     `cos-session-id`, so both can coexist harmlessly while cos is still
     installed; this plugin's own scripts read ONLY this line).
  2. When bound: the ~1 KB identity block read DIRECTLY from the manifest
     (X3 - deterministic, never judgment-refreshed) - name, focus, state,
     direct_report, collaborate[], maintains[], absorbed[] pointers, and
     the file paths a session can read on demand (hub's context table).
     Appends one more short NOTE line when the manifest's OWN shape fails
     validation (e.g. a bare-string direct_report instead of {name,
     session}|null) - I5 again: a corrupt field must not silently render
     as a plausible-looking wrong line (B1, 2026-09-07).
  3. A one-line degrade NOTE when vault-lock is not materialized in this
     vault (soft dependency - E1-E7 Dependencies page: "no vault-lock ->
     boot warns: writes are unguarded across sessions").

Unbound sessions (the common case for most sessions) get the echo line
plus ONE short NOTE (under 200 bytes) naming /workstream:connect (a
pre-restart ws- session) or /workstream:adopt (a new one) - never the ~1
KB identity block, but never fully silent either. This replaces the
prior if-absent-silent discipline ported from cos-boot.py: I5 ("never
silent" - docs/workstream-model.md) means a missing binding must be
announced, not left for the session to infer from an absence (2026-09-07
truth-up: a shell-run precheck reading unbound with no explanation is the
same class of defect this fixes here for boot's own SessionStart line).

Also resets this session's remind turn-counter to 0 on every fire (X4
dedup: "the boot command clears the turn counter on every fire; remind
reads that stamp first, so the next prompt after a fresh boot doesn't
re-show identity").

The WHOLE stdout of this hook command passes through
workstream_lib.guard_delivery before it is written (B5): the harness caps
each hook command's stdout independently, and over that cap the entire
payload is persisted to a file with only a ~2 KB preview inlined - defect
D1 in a new costume. The identity block's own IDENTITY_CAP keeps this
delivery two orders of magnitude under the guard in practice; the guard is
the bound, not the working limit.

Never crashes: every I/O path degrades to a NOTE; the outer seatbelt
catches anything unforeseen and exits 0 regardless (a broken boot hook
must never decapitate a session).
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import workstream_lib as wslib

IDENTITY_CAP = 1400   # bytes - target ~1 KB (hub context table); loud truncation past it

# Schema-shape problem kinds from workstream_lib.find_problems() that are
# meaningful when checked against THIS session's manifest alone (Inv-1/
# Inv-5 - no cross-manifest lookup involved). The dangling/stale-reference
# kinds (Inv-2/3/4) and "duplicate name" need the FULL vault scan to mean
# anything; checked against a single-entry dict they would false-positive
# on every ordinary reference to a sibling workstream (born_session would
# never resolve against a dict holding only itself) - the same class of
# regression I5 forbids in the opposite direction. Those are already
# surfaced inline above via the (unresolved)/[closed]/[absorbed] badges,
# so B1's schema-shape NOTE stays scoped to this manifest's own shape.
_ISOLATED_PROBLEM_KINDS = frozenset((
    "invalid state",
    "malformed direct_report",
    "malformed collaborate",
    "malformed absorbed",
    "malformed absorbed entry",
    "legacy field on active manifest",
))


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


def _rel(vault, path):
    try:
        return os.path.relpath(path, vault).replace(os.sep, "/")
    except ValueError:
        return path


def _schema_problem_note(born_session, manifest):
    """B1, I5 ('never silent' - docs/workstream-model.md): a manifest
    whose OWN field fails shape validation (e.g. direct_report is a bare
    string instead of {name,session}|null) must say so, not silently
    degrade into a plausible-looking wrong line - the same silent-failure
    class A2 fixed for an unbound session. Runs the SAME shared checker
    views.py's regenerate() calls (workstream_lib.find_problems - V4:
    "one manifest-scan function inside the plugin") against just this
    manifest, filtered to the shape-only kinds (see
    _ISOLATED_PROBLEM_KINDS above). Returns None when clean."""
    problems = wslib.find_problems({born_session: manifest})
    own = [(k, d) for k, d in problems if k in _ISOLATED_PROBLEM_KINDS]
    if not own:
        return None
    prefix = born_session + ": "
    details = [d[len(prefix):] if d.startswith(prefix) else d for _k, d in own]
    return ("NOTE (workstream boot): this workstream's manifest has a schema "
            "problem: %s - run /workstream:connect. Reporting line may be "
            "wrong." % "; ".join(details))


def render_identity_block(vault, born_session, ws_dir, root=None):
    """The ~1 KB identity block (hub's context table): born_session, name,
    focus, direct_report, collaborate[], maintains[], absorbed[] pointers,
    file paths. A deterministic manifest read - never crashes; a
    missing/broken manifest degrades to a minimal block naming the dir
    alone, loud about what's wrong (X3: never a silent drop)."""
    manifest, err = wslib.read_manifest(vault, born_session, root)
    if manifest is None:
        return ("## Workstream identity (%s) - workstream.json is %s. "
                "This session is bound to a dir with no usable manifest - "
                "re-run the adopt/fork verb to self-heal, or investigate "
                "the file directly."
                % (_rel(vault, ws_dir), err or "missing"))

    ws_root = os.path.dirname(ws_dir)
    manifests, _orphans = wslib.discover_manifests(ws_root)

    name = manifest.get("name") or born_session
    focus = manifest.get("focus") or ""
    state = manifest.get("state") or "active"

    lines = ["## Workstream identity: %s (%s)" % (name, _rel(vault, ws_dir))]
    if focus:
        lines.append("- focus: %s" % focus)
    lines.append("- state: %s" % state)
    lines.append("- born_session: %s" % born_session)

    report_entry = wslib.normalize_direct_report(manifest)
    if report_entry is None:
        lines.append("- reports to: nobody - a root; progress goes to the principal directly")
    else:
        disp, target_state = wslib.resolve_ref_display(report_entry, manifests)
        badge = " [%s]" % target_state if target_state in ("closed", "absorbed") else ""
        lines.append("- reports to: %s%s" % (disp or "(unresolved)", badge))

    collab = wslib.normalize_collaborate(manifest)
    if not collab:
        lines.append("- collaborates with: nobody")
    else:
        cells = []
        for e in collab:
            disp, target_state = wslib.resolve_ref_display(e, manifests)
            badge = " [%s]" % target_state if target_state in ("closed", "absorbed") else ""
            scope = e.get("scope")
            scope_txt = " (scope: %s)" % scope if isinstance(scope, str) and scope.strip() else ""
            cells.append("%s%s%s" % (disp or "(unresolved)", badge, scope_txt))
        lines.append("- collaborates with: " + "; ".join(cells))

    maintains = manifest.get("maintains")
    if isinstance(maintains, list) and maintains:
        lines.append("- maintains: " + "; ".join(str(m) for m in maintains))

    absorbed = wslib.normalize_absorbed(manifest)
    if absorbed:
        parts = []
        for e in absorbed:
            parts.append("%s (%s)" % (e.get("name") or e.get("born_session"), e.get("dir") or "?"))
        lines.append("- absorbed: " + "; ".join(parts))

    lines.append("- paths: manifest `%s/workstream.json`, log `%s/log.md`, "
                "hot `%s/hot.md`, policy `%s/policy.md`"
                % ((_rel(vault, ws_dir),) * 4))
    lines.append("- change these ONLY via the manifest/policy primitive from "
                "THIS session's own workstream (single-writer, I3).")

    note = _schema_problem_note(born_session, manifest)
    if note:
        lines.append(note)

    block = "\n".join(lines)
    raw = block.encode("utf-8")
    if len(raw) > IDENTITY_CAP:
        block = (raw[:IDENTITY_CAP].decode("utf-8", "ignore")
                + "\n[... truncated at %d bytes by workstream boot ...]" % IDENTITY_CAP)
    return block


def reset_remind_counter(vault, session_id, root=None):
    path = os.path.join(wslib.sessions_root(vault, root), session_id + ".remind.json")
    try:
        wslib.atomic_write_json(path, {"count": 0})
    except OSError:
        pass   # best-effort bookkeeping; a failed reset just means remind fires a turn early/late


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace", newline="\n")

    payload = read_stdin_payload()
    session_id = payload.get("session_id")
    if not (isinstance(session_id, str) and wslib.SAFE_ID_RE.match(session_id)):
        session_id = None

    vault = os.getcwd()
    out = []

    if session_id:
        out.append("<!-- workstream-session-id: %s -->" % session_id)
    else:
        out.append("NOTE (workstream boot): no valid session_id on stdin - "
                   "workstream binding is unavailable this session.")

    if session_id:
        born_session, ws_dir = wslib.resolve_bound_dir(vault, session_id)
        if ws_dir:
            out.append(render_identity_block(vault, born_session, ws_dir))
            reset_remind_counter(vault, session_id)
            if not wslib.vault_lock_available(vault):
                out.append("NOTE (workstream boot): vault-lock is not installed/materialized "
                          "in this vault - writes are unguarded across sessions this session.")
        else:
            # Unbound: I5 "never silent" - a session with no sidecar must
            # be told so in one short line, not left to infer it from the
            # absent identity block (2026-09-07 truth-up).
            out.append("NOTE (workstream boot): no sidecar for %s - not bound "
                      "to a workstream. A pre-restart ws- session: run "
                      "/workstream:connect. A new session: /workstream:adopt."
                      % session_id[:8])

    sys.stdout.write(wslib.guard_delivery("\n\n".join(out) + "\n",
                                          "SessionStart (workstream boot)"))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except BaseException:
        sys.exit(0)
