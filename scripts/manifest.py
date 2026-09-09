#!/usr/bin/env python3
"""manifest.py - the manifest primitive (I3, I4, I5): schema authority for
workstream.json, single-writer-per-manifest. A session writes only its OWN
workstream.json; every field write in this file assumes the CALLER already
verified that (via its own sidecar) - this primitive enforces SCHEMA, not
WHO is calling, matching cos 0.20.0's own trust boundary (a skill run
inside a session is trusted to act for that session).

Schema (I4, target): name, state (active|closed|absorbed), focus, created,
born_session, spawned_from, spawned_from_session, direct_report (single
{name,session}|null - never a list), collaborate[] ({name,session,scope}),
refocused[], previous_names[], projects[] (informational, I7), absorbed[]
(NEW - {name, born_session, dir, transcript}, AB3), maintains[] (NEW -
vault wikilinks or absolute paths, I6). DROPPED going forward: parents[]/
parent/parent_session (multi-parent retired), rebound[] (rebind retired,
L3) - create_manifest never writes them; set_field refuses to
(re)introduce them. (There is no top-level `scope` field - a per-peer
`scope` freeform note lives only inside each collaborate[] entry, B2
2026-09-07: an earlier draft of this docstring claimed one, but neither
create_manifest nor SCHEMA_FIELDS ever implemented it.)

The approval gate (B2): a write that CHANGES `maintains`,
`direct_report`, `collaborate` or `absorbed` needs a fresh one-time
approval token (Ballast's `approve.py` convention, resolved through
`workstream_lib.ballast_script`), which it consumes. The token is scoped
to the manifest FILE, not to the individual field: Ballast keys a token by
its target path alone, and its check/consume never read the field, so one
approval authorizes the next gated write to this workstream.json until it
is consumed - it is deliberately NOT a per-field pass, because the
mechanism cannot deliver one. Those four reshape
the fleet - who this workstream answers to, who it works with, what it
owns, what it swallowed - and each shows up in another workstream's own
graph, so an unreviewed edit silently rewires the org chart. EVERY other
field is ungated: `state`, `focus`, `name`, lineage, the immutables, the
append-only audit arrays, `last_touched`. Gating a hook-written field
would fail every hook-driven write in the vault, which is a strictly
worse failure than the one the gate exists to prevent. A no-op assignment
is never gated (nothing changed for anyone to have reviewed), the shape
check runs FIRST (an impossible value is refused for being wrong, and
never spends an approval), and `<state-root>/ballast-gate.disabled`
disarms the gate exactly as it does for Ballast itself.

The ONE sanctioned cross-manifest write (AB1-AB7): absorb_close sets
state=absorbed + absorbed_by/absorbed_by_session on the STALE workstream's
OWN manifest, from the OVERTAKER's session - wrapped in vault-lock
acquire/release when vault-lock is available (soft dependency; degrades to
an unlocked write with a loud warning when absent, never a silent skip and
never a hard refusal - AB1's failure was blindness, not the absence of a
lock).

CLI (subprocess surface for skills + tests):
    manifest.py read <born_session>
    manifest.py create <born_session> --name N --focus F
                 [--spawned-from NAME --spawned-from-session ID]
    manifest.py repair <born_session> --name N [--focus F]
                 [--spawned-from NAME --spawned-from-session ID]
    manifest.py set <born_session> <field> <json-value>
    manifest.py collaborate-add <born_session> --peer-session ID --peer-name N [--scope S]
    manifest.py collaborate-remove <born_session> --peer-session ID
    manifest.py append-absorbed <born_session> --name N --absorbed-born-session ID --dir PATH [--transcript PATH]
    manifest.py absorb-close <stale_born_session> --by-name N --by-session ID [--vault-lock PATH --lock-session ID]
"""
import argparse
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import workstream_lib as wslib

LEGACY_FIELDS = ("parents", "parent", "parent_session", "rebound")

# --- the approval gate (B2), FILE-scoped over four fields ------------------
# The four fields whose change reshapes the FLEET rather than this one
# workstream's own description of itself: who it answers to, who it works
# with, what it owns, and what it swallowed. Each is read at every boot by
# this session and shows up in another workstream's own graph, so an
# unreviewed edit silently rewires the org chart. Everything else - state,
# focus, name, lineage, the immutables, the append-only audit arrays,
# last_touched - is never gated: those either describe this workstream to
# itself or are written by machinery on a cadence no person could approve.
GATED_FIELDS = ("maintains", "direct_report", "collaborate", "absorbed")

# Ballast's master off-switch, honored here for the same reason ballast
# honors it: a vault deliberately running without the gate (or without
# ballast at all) must still be able to write its own manifests.
GATE_DISABLED_FILENAME = "ballast-gate.disabled"

NO_APPROVAL = (
    "manifest: %r is approval-gated and has no fresh approval.\n"
    "Show Adam the exact field change - the value now, the value after - "
    "wait for his explicit yes, then mint the one-time approval:\n"
    "  %s mint --scope %s --file %s\n"
    "and run this command again. The approval is scoped to this FILE, not "
    "to one field (Ballast keys the token by target path): it authorizes "
    "the next gated write to %s, is one-time, expires in minutes, and is "
    "consumed by that write."
)

NO_SCOPE = (
    "manifest: %r is approval-gated, but there is no ballast scope "
    "(a ballast.json) anywhere under the state root %s, so no approval can "
    "be minted - approve.py mint needs a readable --scope to resolve the "
    "state root. Let a bound session's SessionStart write its scope first, "
    "or, if this vault runs without the gate, create %s under the state "
    "root to disarm it. A gated field is never written unapproved."
)

BALLAST_ABSENT = (
    "manifest: %r is approval-gated, and the `ballast` plugin - which owns "
    "the approval gate - is not installed anywhere this process can see, so "
    "no approval can be minted or checked. Install `ballast` from the "
    "staff-plugins marketplace, or, if this vault deliberately runs without "
    "it, create %s under the state root to disarm the gate. A gated field is "
    "never written unapproved."
)

GATE_OFF_NOTE = ("manifest: the approval gate is OFF (%s exists under %s) - "
                 "writing %r without an approval.")

# Fields set_field will validate the SHAPE of when present - everything
# else passes through as opaque JSON (name/focus/projects entries etc. are
# free-form strings/lists the schema does not otherwise constrain).
SCHEMA_FIELDS = {
    "state": lambda v: isinstance(v, str) and v in wslib.VALID_STATES,
    "direct_report": lambda v: v is None or isinstance(v, dict),
    "collaborate": lambda v: isinstance(v, list) and all(isinstance(e, dict) for e in v),
    "projects": lambda v: isinstance(v, list) and all(isinstance(e, str) for e in v),
    "maintains": lambda v: isinstance(v, list) and all(isinstance(e, str) for e in v),
    "absorbed": lambda v: isinstance(v, list) and all(isinstance(e, dict) for e in v),
    "refocused": lambda v: isinstance(v, list),
    "previous_names": lambda v: isinstance(v, list),
}


def _run_approve(approve_py, action, target, scope_path):
    """One `approve.py <action>` invocation. Returns its exit code, or None
    when it could not be run at all. `--scope` is passed when the scope
    file exists (it carries the state root and the TTL); a scope ballast
    itself refuses to load (exit 2, a usage/scope error) falls back to the
    bare `--file` form, so a malformed ballast.json degrades the gate to
    its defaults instead of bricking every manifest write. No `--field` is
    ever sent: Ballast keys a token by target path alone and never reads
    the field, so the approval is FILE-scoped - passing a field would imply
    a per-field scope the mechanism does not deliver."""
    argv = [sys.executable, approve_py, action, "--file", target]
    try:
        if scope_path:
            proc = subprocess.run(argv + ["--scope", scope_path],
                                  capture_output=True, text=True, timeout=15)
            if proc.returncode != 2:
                return proc.returncode
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=15)
        return proc.returncode
    except Exception:
        return None


def _first_scope_under(state_root):
    """The first existing `<state-root>/<dir>/ballast.json`, in sorted
    directory order, or None - mirrors ballast-dispatch.py's
    any_scope_under. Ballast's gate/mint read only the state root (the
    parent of the scope's own directory) and the TTL from whatever scope
    they are handed, so for a target under THIS state root every scope
    under it resolves the same state root and answers the same. This just
    needs SOME real scope, because approve.py mint requires a readable
    --scope to resolve the state root, and 20 of the 43 live workstream
    dirs have no ballast.json of their own. Creates nothing."""
    try:
        with os.scandir(state_root) as entries:
            names = sorted(e.name for e in entries if e.is_dir())
    except OSError:
        return None
    for name in names:
        candidate = os.path.join(state_root, name, "ballast.json")
        if os.path.isfile(candidate):
            return candidate
    return None


def require_approval(vault, born_session, field, root=None, consume=True):
    """The gate every gated-field write passes through. Returns silently
    when the write is authorized; raises PermissionError with the exact
    next step otherwise.

    `consume=False` (a dry run) CHECKS the approval but does not spend it:
    a preview writes nothing, so it has nothing to consume, and leaving
    the token in place is what lets `--dry-run` be a real preview of the
    write that follows.

    Otherwise the CONSUME is the authorization, not bookkeeping after one:
    two gated writes racing on a single token both pass `check`, so only
    the consume can pick between them. That is why ballast_lib's
    `consume_approval` claims the token with an exclusive create and
    ballast.py's own half of this gate refuses when the claim fails - this
    half, the second, refuses on the same signal, so one yes can never
    authorize two writes."""
    manifest_path = wslib.manifest_path_for(vault, born_session, root)
    ws_dir = os.path.dirname(manifest_path)
    state_root = os.path.dirname(ws_dir)

    if os.path.isfile(os.path.join(state_root, GATE_DISABLED_FILENAME)):
        sys.stderr.write(GATE_OFF_NOTE % (GATE_DISABLED_FILENAME, state_root,
                                          field) + "\n")
        return

    approve_py = wslib.ballast_script("approve.py")
    if approve_py is None:
        raise PermissionError(BALLAST_ABSENT % (field, GATE_DISABLED_FILENAME))

    scope_path = os.path.join(ws_dir, "ballast.json")
    if not os.path.isfile(scope_path):
        # This dir has no ballast.json of its own (20 of the 43 live dirs).
        # approve.py's `check` works from the bare --file, but `mint`
        # REQUIRES a readable --scope to resolve the state root - so fall
        # back to any existing scope under the SAME state root (they all
        # resolve the same state root and TTL for a target under it). Never
        # emit an angle-bracket placeholder as a command argument.
        scope_path = _first_scope_under(state_root)

    if scope_path:
        refusal = NO_APPROVAL % (field, approve_py, scope_path,
                                 manifest_path, manifest_path)
    else:
        # No scope anywhere under the state root - minting is impossible
        # until one exists, so say exactly that instead of a placeholder.
        refusal = NO_SCOPE % (field, state_root, GATE_DISABLED_FILENAME)
    if _run_approve(approve_py, "check", manifest_path, scope_path) != 0:
        raise PermissionError(refusal)
    if consume and _run_approve(approve_py, "consume", manifest_path,
                                scope_path) != 0:
        # Another writer claimed this token between the check above and
        # here (or it could not be claimed at all): this write has no
        # approval left, and an unclaimable approval is never assumed.
        raise PermissionError(refusal)


def create_manifest(vault, born_session, name, focus, spawned_from=None,
                    spawned_from_session=None, root=None, dry_run=False):
    """Mint a fresh workstream.json (state: active). Refuses (raises
    FileExistsError) if one already exists at this born_session - adopt
    never overwrites (L4). `dry_run=True` (spec 4.3) runs the existence
    check and builds the record, but never writes it - see
    workstream_lib.atomic_write_json."""
    if not (isinstance(born_session, str) and wslib.SAFE_ID_RE.match(born_session)):
        raise ValueError("unsafe born_session: %r" % (born_session,))
    path = wslib.manifest_path_for(vault, born_session, root)
    if os.path.isfile(path):
        raise FileExistsError("manifest already exists at %s" % path)
    data = {
        "name": name.strip(),
        "state": "active",
        "focus": focus.strip() if isinstance(focus, str) else "",
        "created": wslib.iso_now(),
        "born_session": born_session,
        "spawned_from": spawned_from,
        "spawned_from_session": spawned_from_session,
        "direct_report": None,
        "collaborate": [],
        "refocused": [],
        "previous_names": [],
        "projects": [],
        "absorbed": [],
        "maintains": [],
    }
    wslib.atomic_write_json(path, data, dry_run=dry_run)
    return path


def repair_manifest(vault, born_session, name, focus="", spawned_from=None,
                    spawned_from_session=None, root=None, dry_run=False):
    """The repair route (R0-3): a workstream.json that no longer parses has
    no other write path - `set` refuses it (read_manifest returns 'malformed
    JSON', set_field raises FileNotFoundError) and the direct-Write guard
    refuses a hand edit, so a corrupt manifest would otherwise be permanently
    unrepairable. repair moves the unusable file aside to a timestamped
    `.corrupt-<ts>` backup (history is never deleted) and writes a fresh
    canonical-empty manifest through the schema.

    Refuses (ValueError) when the manifest is ALREADY well-formed - repair
    never clobbers a good manifest; `set` is the route for a field on a
    readable one. `dry_run=True` (spec 4.3) runs every check and reports the
    backup path it WOULD use, writing nothing and moving nothing."""
    if not (isinstance(born_session, str) and wslib.SAFE_ID_RE.match(born_session)):
        raise ValueError("unsafe born_session: %r" % (born_session,))
    path = wslib.manifest_path_for(vault, born_session, root)
    if not os.path.isfile(path):
        raise FileNotFoundError("no manifest to repair at %s" % path)
    manifest, err = wslib.read_manifest(vault, born_session, root)
    if manifest is not None:
        raise ValueError("manifest at %s is already well-formed - use `set`; "
                         "repair never overwrites a good manifest" % path)
    # manifest is None with err set (malformed/oversized/unreadable/not-an-
    # object) - the file exists but cannot be used.
    ts = wslib.iso_now().replace(":", "").replace("-", "")
    backup = "%s.corrupt-%s" % (path, ts)
    if dry_run:
        return path, backup, err
    os.replace(path, backup)
    try:
        create_manifest(vault, born_session, name, focus, spawned_from,
                        spawned_from_session, root=root)
    except BaseException:
        os.replace(backup, path)   # restore the corrupt original on any failure
        raise
    return path, backup, err


def read(vault, born_session, root=None):
    return wslib.read_manifest(vault, born_session, root)


def set_field(vault, born_session, field, value, root=None, dry_run=False):
    """Single-field write, schema-validated, additive-never-assign for the
    list fields is the CALLER's job (collaborate_add/remove, append_absorbed
    below) - this is the raw primitive a verb calls for scalar fields
    (name, focus, state, direct_report as a whole object, projects as a
    whole list when the caller already computed the union, etc). `dry_run=
    True` (spec 4.3) validates everything but skips the write."""
    if field in LEGACY_FIELDS:
        raise ValueError("field %r is retired (L3/R1) - never written" % (field,))
    manifest, err = wslib.read_manifest(vault, born_session, root)
    if manifest is None:
        raise FileNotFoundError("no manifest for %s (%s)" % (born_session, err or "not found"))
    validator = SCHEMA_FIELDS.get(field)
    if validator is not None and not validator(value):
        raise ValueError("field %r rejected value %r - fails shape check" % (field, value))
    # Shape first, then the gate: a value that could never be written is
    # refused for the reason it is actually wrong, and never spends an
    # approval. A no-op assignment needs no approval either - there is no
    # change for anyone to have reviewed.
    if field in GATED_FIELDS and manifest.get(field) != value:
        require_approval(vault, born_session, field, root, consume=not dry_run)
    manifest[field] = value
    wslib.atomic_write_json(wslib.manifest_path_for(vault, born_session, root), manifest, dry_run=dry_run)
    return manifest


def collaborate_add(vault, born_session, peer_session, peer_name, scope=None, root=None, dry_run=False):
    """Additive-never-assign (R1, R5): appends one {name,session,scope}
    entry; refuses a duplicate peer_session (idempotent no-op instead).
    `dry_run=True` (spec 4.3) skips only the final write."""
    manifest, err = wslib.read_manifest(vault, born_session, root)
    if manifest is None:
        raise FileNotFoundError("no manifest for %s (%s)" % (born_session, err or "not found"))
    collab = manifest.get("collaborate")
    if not isinstance(collab, list):
        collab = []
    if any(isinstance(e, dict) and e.get("session") == peer_session for e in collab):
        return manifest   # already present - additive, idempotent, nothing to approve
    require_approval(vault, born_session, "collaborate", root, consume=not dry_run)
    entry = {"name": peer_name, "session": peer_session}
    if isinstance(scope, str) and scope.strip():
        entry["scope"] = scope.strip()
    collab.append(entry)
    manifest["collaborate"] = collab
    wslib.atomic_write_json(wslib.manifest_path_for(vault, born_session, root), manifest, dry_run=dry_run)
    return manifest


def collaborate_remove(vault, born_session, peer_session, root=None, dry_run=False):
    manifest, err = wslib.read_manifest(vault, born_session, root)
    if manifest is None:
        raise FileNotFoundError("no manifest for %s (%s)" % (born_session, err or "not found"))
    collab = manifest.get("collaborate")
    if not isinstance(collab, list):
        collab = []
    remaining = [e for e in collab
                 if not (isinstance(e, dict) and e.get("session") == peer_session)]
    if remaining != collab:
        require_approval(vault, born_session, "collaborate", root, consume=not dry_run)
    manifest["collaborate"] = remaining
    wslib.atomic_write_json(wslib.manifest_path_for(vault, born_session, root), manifest, dry_run=dry_run)
    return manifest


def append_absorbed(vault, overtaker_born_session, name, absorbed_born_session,
                    dir_path, transcript_path=None, root=None, dry_run=False):
    """AB3: the determinism anchor - append one {name, born_session, dir,
    transcript} entry to the OVERTAKER's own manifest (its own field, its
    own session - not the cross-manifest write). Additive; refuses a
    duplicate absorbed_born_session (idempotent no-op). `dry_run=True`
    (spec 4.3) skips only the final write."""
    manifest, err = wslib.read_manifest(vault, overtaker_born_session, root)
    if manifest is None:
        raise FileNotFoundError("no manifest for %s (%s)" % (overtaker_born_session, err or "not found"))
    absorbed = manifest.get("absorbed")
    if not isinstance(absorbed, list):
        absorbed = []
    if any(isinstance(e, dict) and e.get("born_session") == absorbed_born_session for e in absorbed):
        return manifest   # additive, idempotent - nothing to approve
    require_approval(vault, overtaker_born_session, "absorbed", root, consume=not dry_run)
    entry = {"name": name, "born_session": absorbed_born_session, "dir": dir_path}
    if transcript_path:
        entry["transcript"] = transcript_path
    absorbed.append(entry)
    manifest["absorbed"] = absorbed
    wslib.atomic_write_json(wslib.manifest_path_for(vault, overtaker_born_session, root), manifest, dry_run=dry_run)
    return manifest


# On Windows, a bare "bash" on PATH can resolve to the WSL launcher
# (C:/Windows/System32/bash.exe) rather than Git Bash - that launcher
# cannot see a Windows path at all ("No such file or directory" even with
# forward slashes), and System32 sorts ahead of Git's own PATH entries on
# a stock install. Mirrors vault-lock's own write_lock_lib.find_bash():
# explicit Git-Forge homes are checked FIRST on Windows (deterministic,
# known-good), falling back to PATH resolution only when none exist -
# the inverse of "trust PATH first" specifically because PATH is the
# thing known to be ambiguous here. On non-Windows this is just
# shutil.which("bash").
_WINDOWS_BASH_FALLBACKS = (
    "C:/Program Files/Git/bin/bash.exe",
    "C:/Program Files/Git/usr/bin/bash.exe",
    "C:/Program Files (x86)/Git/bin/bash.exe",
)


def _find_bash():
    if os.name == "nt":
        for cand in _WINDOWS_BASH_FALLBACKS:
            if os.path.isfile(cand):
                return cand
    import shutil as _shutil
    found = _shutil.which("bash")
    if found:
        return found
    if os.name != "nt":
        return None
    for cand in _WINDOWS_BASH_FALLBACKS:
        if os.path.isfile(cand):
            return cand
    return None


def _vault_lock_acquire(vault_lock_sh, rel_path, session_id):
    bash = _find_bash()
    if not bash:
        return False
    try:
        proc = subprocess.run([bash, vault_lock_sh, "acquire", rel_path, "--session", session_id],
                              capture_output=True, text=True, timeout=15)
        return proc.returncode == 0
    except Exception:
        return False


def _vault_lock_release(vault_lock_sh, rel_path, session_id):
    bash = _find_bash()
    if not bash:
        return
    try:
        subprocess.run([bash, vault_lock_sh, "release", rel_path, "--session", session_id],
                       capture_output=True, text=True, timeout=15)
    except Exception:
        pass


def absorb_close(vault, stale_born_session, by_name, by_session, vault_lock_sh=None,
                 lock_session=None, root=None, dry_run=False):
    """The ONE sanctioned cross-manifest write (AB7): sets state=absorbed +
    absorbed_by/absorbed_by_session on the STALE workstream's own manifest.
    Soft-dependency degrade: with vault_lock_sh given, wraps the write in
    acquire/release (best-effort - a failed acquire still proceeds, since
    AB1's failure mode was blindness, not a torn write; a loud warning is
    returned either way when the lock could not be taken). Without
    vault_lock_sh, writes directly and returns a warning note. `dry_run=
    True` (spec 4.3) skips BOTH the lock acquire/release and the write -
    a preview has no business taking a lock on anyone's behalf."""
    manifest, err = wslib.read_manifest(vault, stale_born_session, root)
    if manifest is None:
        raise FileNotFoundError("no manifest for %s (%s)" % (stale_born_session, err or "not found"))
    manifest_path = wslib.manifest_path_for(vault, stale_born_session, root)
    rel_path = os.path.relpath(manifest_path, vault).replace(os.sep, "/")

    note = None
    locked = False
    if dry_run:
        note = "absorb-close: DRY-RUN - would set state=absorbed on %s (no lock taken)." % rel_path
    elif vault_lock_sh and os.path.isfile(vault_lock_sh):
        locked = _vault_lock_acquire(vault_lock_sh, rel_path, lock_session or by_session)
        if not locked:
            note = ("absorb-close: vault-lock acquire on %s did not succeed (held by "
                    "another session, or the lock CLI errored) - writing anyway "
                    "(AB7's cross-manifest write proceeds; the stale manifest's "
                    "state is what must land, not the lock)." % rel_path)
    else:
        note = ("absorb-close: no vault-lock available - writing "
               "%s UNGUARDED (soft dependency absent)." % rel_path)

    manifest["state"] = "absorbed"
    manifest["absorbed_by"] = by_name
    manifest["absorbed_by_session"] = by_session
    try:
        wslib.atomic_write_json(manifest_path, manifest, dry_run=dry_run)
    finally:
        if locked:
            _vault_lock_release(vault_lock_sh, rel_path, lock_session or by_session)
    return manifest, note


def main(argv):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace", newline="\n")

    p = argparse.ArgumentParser(prog="manifest.py")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_read = sub.add_parser("read")
    p_read.add_argument("born_session")

    p_create = sub.add_parser("create")
    p_create.add_argument("born_session")
    p_create.add_argument("--name", required=True)
    p_create.add_argument("--focus", default="")
    p_create.add_argument("--spawned-from")
    p_create.add_argument("--spawned-from-session")
    p_create.add_argument("--dry-run", action="store_true", dest="dry_run")

    p_repair = sub.add_parser("repair")
    p_repair.add_argument("born_session")
    p_repair.add_argument("--name", required=True)
    p_repair.add_argument("--focus", default="")
    p_repair.add_argument("--spawned-from")
    p_repair.add_argument("--spawned-from-session")
    p_repair.add_argument("--dry-run", action="store_true", dest="dry_run")

    p_set = sub.add_parser("set")
    p_set.add_argument("born_session")
    p_set.add_argument("field")
    p_set.add_argument("value", help="JSON-encoded value")
    p_set.add_argument("--dry-run", action="store_true", dest="dry_run")

    p_cadd = sub.add_parser("collaborate-add")
    p_cadd.add_argument("born_session")
    p_cadd.add_argument("--peer-session", required=True)
    p_cadd.add_argument("--peer-name", required=True)
    p_cadd.add_argument("--scope")
    p_cadd.add_argument("--dry-run", action="store_true", dest="dry_run")

    p_crem = sub.add_parser("collaborate-remove")
    p_crem.add_argument("born_session")
    p_crem.add_argument("--peer-session", required=True)
    p_crem.add_argument("--dry-run", action="store_true", dest="dry_run")

    p_absadd = sub.add_parser("append-absorbed")
    p_absadd.add_argument("born_session")
    p_absadd.add_argument("--name", required=True)
    p_absadd.add_argument("--absorbed-born-session", required=True)
    p_absadd.add_argument("--dir", required=True)
    p_absadd.add_argument("--transcript")
    p_absadd.add_argument("--dry-run", action="store_true", dest="dry_run")

    p_close = sub.add_parser("absorb-close")
    p_close.add_argument("stale_born_session")
    p_close.add_argument("--by-name", required=True)
    p_close.add_argument("--by-session", required=True)
    p_close.add_argument("--vault-lock")
    p_close.add_argument("--lock-session")
    p_close.add_argument("--dry-run", action="store_true", dest="dry_run")

    args = p.parse_args(argv)
    vault = os.getcwd()

    try:
        if args.cmd == "read":
            manifest, err = read(vault, args.born_session)
            if manifest is None:
                sys.stderr.write("manifest: %s\n" % (err or "not found"))
                return 1
            print(json.dumps(manifest, indent=2))
            return 0

        if args.cmd == "create":
            path = create_manifest(vault, args.born_session, args.name, args.focus,
                                   args.spawned_from, args.spawned_from_session,
                                   dry_run=args.dry_run)
            print("manifest: %s%s" % ("DRY-RUN: would create " if args.dry_run else "created ", path))
            return 0

        if args.cmd == "repair":
            path, backup, err = repair_manifest(vault, args.born_session, args.name,
                                                args.focus, args.spawned_from,
                                                args.spawned_from_session,
                                                dry_run=args.dry_run)
            if args.dry_run:
                print("manifest: DRY-RUN: would move malformed %s (%s) aside to %s "
                      "and write a fresh canonical manifest" % (path, err, backup))
            else:
                print("manifest: repaired %s (moved malformed original [%s] aside to %s)"
                      % (path, err, backup))
            return 0

        if args.cmd == "set":
            value = json.loads(args.value)
            set_field(vault, args.born_session, args.field, value, dry_run=args.dry_run)
            print("manifest: %sset %s.%s" % ("DRY-RUN: would " if args.dry_run else "",
                                             args.born_session, args.field))
            return 0

        if args.cmd == "collaborate-add":
            collaborate_add(vault, args.born_session, args.peer_session, args.peer_name, args.scope,
                           dry_run=args.dry_run)
            print("manifest: %scollaborate-add %s <-> %s" % ("DRY-RUN: would " if args.dry_run else "",
                                                              args.born_session, args.peer_session))
            return 0

        if args.cmd == "collaborate-remove":
            collaborate_remove(vault, args.born_session, args.peer_session, dry_run=args.dry_run)
            print("manifest: %scollaborate-remove %s -x- %s" % ("DRY-RUN: would " if args.dry_run else "",
                                                                 args.born_session, args.peer_session))
            return 0

        if args.cmd == "append-absorbed":
            append_absorbed(vault, args.born_session, args.name, args.absorbed_born_session,
                           args.dir, args.transcript, dry_run=args.dry_run)
            print("manifest: %sappend-absorbed %s <- %s" % ("DRY-RUN: would " if args.dry_run else "",
                                                             args.born_session, args.absorbed_born_session))
            return 0

        if args.cmd == "absorb-close":
            _manifest, note = absorb_close(vault, args.stale_born_session, args.by_name,
                                           args.by_session, args.vault_lock, args.lock_session,
                                           dry_run=args.dry_run)
            if note:
                sys.stderr.write(note + "\n")
            print("manifest: %sabsorb-close %s -> absorbed_by %s" % ("DRY-RUN: would " if args.dry_run else "",
                                                                      args.stale_born_session, args.by_name))
            return 0
    except (FileNotFoundError, FileExistsError, ValueError, PermissionError) as e:
        sys.stderr.write("manifest: %s\n" % e)
        return 2

    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
