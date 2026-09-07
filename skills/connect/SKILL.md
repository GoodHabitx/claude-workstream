---
name: connect
description: Bounded reconciliation AUDIT of the workstream graph — scan every manifest + sidecar, check the 8 fixed invariants (schema, lineage, reporting, reciprocal collaborate, no legacy fields on active nodes, sidecar self-heal, unreconciled forks, fresh views), then propose-and-confirm each fix. Run anywhere; idempotent. Trigger on "reconcile the workstreams", "audit the workstream graph", "connect-audit". Do NOT use to self-name a fork (workstream:fork), adopt (workstream:adopt), rename (workstream:refocus), or set one field (workstream:manifest).
---

# Workstream connect

Shares the shared model: `${CLAUDE_PLUGIN_ROOT}/docs/workstream-model.md`.

The general **reconciliation audit** over the workstream graph. Scans,
detects, and proposes-then-confirms fixes — recruits the primitives
(`workstream:manifest`, `workstream:policy`) and `workstream:fork` to
apply each one, in the current workstream directly or in a target
workstream via `workstream:notify`. Never self-names or births
anything — a fork self-names via `workstream:fork` (invariant 7 just
notifies it to). Run from anywhere — a bound session or an unrelated
one — as often as you like; it converges. Bounded to exactly 8
invariants — no open-ended detection. Never touches a vault
project/spine node.

*(interpreter-shim caveat: neither `python3` nor `python` resolves on
every host — try `python3`, then `python`, then `py -3`, then `py`.)*

## Procedure

1. **Gather.** `mcp__ccd_session_mgmt__list_sessions` + `get_session("self")`
   (merge self in; degrade to manifest-only if the MCP is unavailable
   — invariants 6/7 then can't be checked against live sessions, note
   that in the report). Scan every `workstream.json` under the state
   root and every sidecar under `<state_root's sibling>/workstream-sessions/`.
   Build the by-`born_session` manifest map; resolve every
   cross-manifest pointer by its session half, reading the target's
   CURRENT `name` — exactly as `views.py` does.

2. **Check the v1 invariants — bounded, in order:**
   1. **Schema.** Every manifest parses and matches the target schema:
      required fields present + well-typed; `direct_report` a single
      `{name, session}` object or `null` (never a list); `collaborate`
      a list of `{name, session, scope}`; `refocused`/`previous_names`/
      `projects`/`maintains`/`absorbed` are arrays; **`absorbed[]`
      shape check** — each entry has `name`, `born_session`, `dir`
      (required), `transcript` (optional, best-effort).
   2. **Lineage resolves.** Each `spawned_from_session` resolves to a
      real manifest by uuid, or is `null`. A dangling one is a flag.
      Lineage may point at a closed/absorbed manifest — that's fine.
   3. **Reporting.** `direct_report` is single and resolves, and its
      target is not `closed`/`absorbed`. A report to a dead node →
      flag **re-home**.
   4. **Collaboration reciprocity.** Every `collaborate` entry resolves
      by uuid AND the counterpart carries the reciprocal entry. A
      resolvable-but-unreciprocated edge → **one-sided** flag.
   5. **No legacy axis on active nodes.** No `active` manifest still
      carries `parents`/`parent`/`parent_session`/`rebound`. A frozen
      `closed`/`absorbed` manifest MAY still carry them — history, not
      a violation; check active nodes only.
   6. **Sidecar self-heal.** Every LIVE session's sidecar points to a
      real manifest, AND every `active` manifest with a live session
      has a sidecar. A sidecar with no manifest, or a live+active
      manifest with no sidecar, is a flag — fixed mechanically (not
      just surfaced) via `workstream:sidecar`'s `self-heal`.
   7. **Unreconciled forks.** Every session titled `(fork)` with no
      manifest → notify it to run `workstream:fork`. connect does not
      birth or self-name it.
   8. **Views not stale.** `<state_root>/index.md` and
      `<state_root>/workstream-graph.md` are not older than any
      manifest that changed — regenerated in step 4 regardless.

3. **Fix style = propose + confirm EACH — never a blanket write.** For
   every flagged anomaly, state the concrete fix and get the
   principal's confirm before applying. Apply by recruiting the right
   verb — never hand-roll a manifest/policy write here:
   - **In THIS session's OWN workstream** → call `workstream:manifest`
     (or `workstream:policy`) directly.
     - Inv 1/5: a field write through `workstream:manifest` also drops
       legacy `parents`/`parent`/`parent_session`/`rebound` from an
       active manifest as part of that same write (the migration
       self-heal path). When Inv 5 is the ONLY flag and no other field
       needs changing (the "default neither" case — `direct_report:
       null`, `collaborate: []` already correct), the proposed fix is
       a **standalone strip trigger**: `workstream:manifest`
       "direct_report — clear" (writes `null`, the value it already
       holds — changes no relationship, just fires the write path that
       drops the legacy keys). In a TARGET workstream, `notify` that
       session to run the same clear.
     - Inv 3 (re-home): the reporter runs "direct_report — set" (to
       the dead node's own `direct_report`) or "clear" on its own
       manifest.
     - Inv 4 (one-sided): add the missing mirror via "collaborate —
       add," or remove the dangling local half via "collaborate —
       remove."
     - Inv 6: `workstream:sidecar` `self-heal` in this session.
     - Inv 1 (`absorbed[]` shape): a malformed entry is reported loud
       — flag it as a data-integrity issue for the principal to decide
       how to fix (no automatic route exists for repairing a bad
       `absorbed[]` entry; it's not a field this skill can safely
       guess a correction for).
   - **In a TARGET (other) workstream** → `workstream:notify` its own
     session to self-apply (single-writer). A relationship-change
     notify leads with why + whose authority it carries + a verify
     path, never a bare directive. If the target is closed/absorbed
     and the fix genuinely must land, `vault-lock acquire` its
     manifest (`--session <this-session-id>`), write the single field
     via `manifest.py set`, `release` — the only reach-in, and only
     for a dead target. Never reach into a live target.
     - Inv 7: notify the fork session — "You are an unreconciled fork;
       run `/workstream:fork` here to self-name and finalize." Report
       as *pending self-fork*.
   - **Genuinely ambiguous fix** (no obvious correct target — e.g.
     which node a mis-linked report should re-home to, or a dangling
     lineage with no recoverable parent) → check
     `workstream_lib.grill_available()`; escalate to `grill:grilling`
     (or its inline numbered-round fallback if absent) to interrogate
     it into a decision. Apply the answer through the same routes. Do
     not guess.

4. **Regenerate the derived views** (invariant 8, always): `python3
   scripts/views.py regen`.

5. **Report the reconciliation.** One compact section: each
   invariant's status (pass/flagged/fixed), what was flagged, what was
   fixed and where (this session's manifest vs. which target), and
   what is pending a target session (a notified self-apply, a pending
   self-fork). A clean pass on the first post-migration run is the
   expected outcome.

## Discipline

Bounded — the 8 invariants above and nothing more; a new check is a
deliberate v2, not improvisation mid-run. Single-writer-per-manifest:
connect never edits another workstream's manifest or `policy.md`
directly — a correction in the current workstream routes through the
primitive; a correction elsewhere goes via `notify` (self-applies), and
a `vault-lock` direct write is reserved for a closed target only
(`--session <this-session-id>`). Never a reach-in to a live peer.
connect never self-names a fork — that's `workstream:fork`'s job;
invariant 7 only notifies it to. Never blanket-migrates — every fix is
proposed and confirmed one at a time. Frozen manifests are history, not
bugs — a `closed`/`absorbed` manifest carrying legacy fields, or a
lineage edge pointing at a dead node, is correct-by-design; flag
neither. Never touches a vault project/spine node.
