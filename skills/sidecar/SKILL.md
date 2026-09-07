---
name: sidecar
description: Resolve, write, self-heal, or check this session's sidecar — the tiny file mapping the current transcript id to its workstream's born_session. A missing/stale sidecar gets a LOUD line, never silence. Trigger on "sidecar", "self-heal my binding", "why did boot say I'm unbound", "/workstream:sidecar". Do NOT use to birth a new workstream (workstream:adopt) or to bind a fresh session to an EXISTING one (that's adopt's own self-heal branch — this skill only repairs, never mints).
---

# Workstream sidecar

Shares the shared model: `${CLAUDE_PLUGIN_ROOT}/docs/workstream-model.md`.

The sidecar primitive (I5, L3): `.vault-meta/workstream-sessions/<session_id>.json`
maps THIS conversation's rotating transcript id → the workstream's
durable `born_session`. Disposable and self-healing by design — losing
one is never a failure of the workstream itself, only of this one
lookup, and this skill's whole job is to make that repair mechanical
instead of silent. All operations route through `scripts/sidecar.py`
(core, already built — do not edit it). Never touches a manifest field
or a vault project/spine node.

*(interpreter-shim caveat: neither `python3` nor `python` resolves on
every host — try `python3`, then `python`, then `py -3`, then `py`.)*

## `/workstream:sidecar [check|self-heal]`

1. **Get this session's transcript id.** Read the `<!--
   workstream-session-id: ... -->` line boot.py injected this session
   (echoed first, every SessionStart including compact — V1). If the
   line is missing entirely, boot.py itself didn't fire correctly — say
   so plainly and stop; this skill cannot recover an id boot never
   echoed.

2. **Bare invocation / `check` with no expected `born_session`** =
   resolve and report:
   `python3 scripts/sidecar.py resolve <session_id>` → JSON
   `{born_session, ws_dir}` on stdout, exit 0 = bound (report which
   workstream, and confirm its manifest at `<ws_dir>/workstream.json`
   still exists and matches); exit 1 = **unbound** — this is the
   documented **loud** case (target behavior, replacing the old
   silent-drop): report plainly *"no sidecar for this session —
   unbound; if this session was previously bound to `<name>`, run
   `self-heal <born_session>` below, or `/workstream:adopt` to birth a
   new one."* Never silently say nothing.

3. **`check <born_session>`** (when the caller already believes this
   session belongs to a specific workstream — e.g. a title reads
   `ws-<name>` but boot reported unbound): `python3 scripts/sidecar.py
   check <session_id> <born_session>` → prints `match` / `missing` /
   `mismatch`, exit 0 always. Report the verdict plainly:
   - `match` — sidecar already correct, nothing to do.
   - `missing` — no sidecar exists; offer `self-heal` below.
   - `mismatch` — the sidecar points at a DIFFERENT `born_session` than
     expected. Do not silently overwrite — show both (the sidecar's
     current target vs. the expected one) and ask the caller to confirm
     which is right before healing; a mismatch can mean the session was
     legitimately re-titled/rebound and self-healing over it would erase
     that.

4. **`self-heal <born_session>`** — the repair itself, run only after
   step 2/3 established this session really should point at
   `<born_session>` (its title reads `ws-<name>`, or the caller just
   confirmed a mismatch): read that manifest's `name`/`focus` first
   (`python3 scripts/manifest.py read <born_session>`), then `python3
   scripts/sidecar.py self-heal <session_id> <born_session> --name
   <name> --focus <focus>` — writes the sidecar and prints a loud
   one-line confirmation. This never mints a new manifest and never
   touches `workstream.json` — it only repairs the transcript-id →
   born_session pointer. If no manifest exists at `<born_session>` at
   all, this isn't a sidecar repair — stop and point to
   `/workstream:adopt` (a fresh workstream) instead.

5. **Write** (the lower-level primitive underneath `self-heal`, used
   directly only by another skill's own composite flow — e.g.
   `workstream:fork`'s birth, `workstream:adopt`'s schema-init delegate
   path): `python3 scripts/sidecar.py write <session_id> <born_session>
   [--name <name>] [--focus <focus>] [--dry-run]`. Prefer `self-heal`
   for a direct caller-facing repair; `write` is the primitive other
   skills compose with.

6. **Confirm** what was resolved, checked, or healed — one line, plain
   language, never silent on an unbound result.

## Discipline

The sidecar is disposable by design (I2) — it exists only to make the
common-case lookup (transcript id → born_session) cheap; the manifest
under `.vault-meta/workstreams/<born_session>/` is the durable record.
This skill never mints a `born_session` and never writes a manifest
field — a genuinely new workstream is `workstream:adopt`'s job, not
this one's. A missing or stale sidecar is reported LOUDLY, never
silently (the model's target behavior, correcting the old
"documented as not a failure" silence). Never touches a vault
project/spine node.
