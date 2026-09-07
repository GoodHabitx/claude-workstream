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
  3. A one-line degrade NOTE when vault-lock is not materialized in this
     vault (soft dependency - E1-E7 Dependencies page: "no vault-lock ->
     boot warns: writes are unguarded across sessions").

Unbound sessions (the common case for most sessions) get ONLY the echo
line - no NOTE, no identity block; same if-absent-silent discipline
cos-boot.py used for "no sidecar for this session at all".

Also resets this session's remind turn-counter to 0 on every fire (X4
dedup: "the boot command clears the turn counter on every fire; remind
reads that stamp first, so the next prompt after a fresh boot doesn't
re-show identity").

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
        # else: unbound - the common case, stay silent (matches cos-boot's
        # "no sidecar for this session -> silent" discipline).

    sys.stdout.write("\n\n".join(out) + "\n")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except BaseException:
        sys.exit(0)
