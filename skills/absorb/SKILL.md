---
name: absorb
description: Fold a still-open, stale workstream into an overtaker that already covers its job — deterministic grab (log-append + itemized hot-block fold), point (a stored absorbed[] pointer on the overtaker's manifest), carry (focus/projects/policy/maintains), grill on a policy/maintains collision, unconditional notify, then absorb-close under vault-lock. Trigger on "absorb <X> into <Y>", "<X> was overtaken by <Y>", "merge this workstream into <Y>". Do NOT use to complete a single task (task-manager's done verb), close a workstream nothing supersedes (workstream:close), or rename (workstream:refocus).
---

# Workstream absorb

Shares the shared model: `${CLAUDE_PLUGIN_ROOT}/docs/workstream-model.md`.

End a workstream by folding it into another that overtook it — the
"superseded" ending, distinct from `workstream:close`'s "finished."
This redesign (AB1-AB7) fixes an observed failure: an overtaker that
knew NOTHING about what it absorbed, because the old carry was a lossy
judgment copy with no durable pointer. The fix: a stored pointer
(`absorbed[]`), a deterministic slice of live state, nothing deleted.
Every *routed* manifest write goes through `workstream:manifest`'s
named routes; the one exception is the `projects[]` union (schema-
trivial list-merge with no dedicated route — a direct own-manifest
write). Never touches a vault project/spine node.

*(interpreter-shim caveat: neither `python3` nor `python` resolves on
every host — try `python3`, then `python`, then `py -3`, then `py`.)*

## `absorb <stale-name> into <overtaker-name>`

1. **Confirm the pair explicitly.** Never auto-detect-and-merge — "who
   overtook whom" is the principal's call. Surfacing candidates is
   fine; the merge itself runs only on direct go.

2. **Grab (deterministic) — the stale workstream's log + hot fold.**
   This is a deterministic, itemized merge, not a free-judgment
   re-summary (AB2, per the delta-curation research finding):
   - Read the stale workstream's `hot.md` STATE BLOCK. Fold it into the
     overtaker's `hot.md` **itemized, add/update/delete per slot** —
     never a whole-file re-summary. If the overtaker is THIS session's
     bound workstream, this is a normal continuity write (Ballast's
     territory — compose the merged block content and let the write
     land through the usual channel this session already uses for its
     own hot state); if the overtaker is a different live session,
     `notify` it to perform the fold in its own session (never write
     into another workstream's `hot.md` directly); if closed,
     `vault-lock acquire` its `hot.md` path (`--session
     <this-session-id>`), write the merged block, `release`.
   - Append one absorption-record line to the overtaker's `log.md`
     (never to the stale one — provenance stays where it happened,
     AB6). Same single-writer choreography.
   - Rewrite the stale workstream's `hot.md` to a short **tombstone**
     naming its successor — "absorbed by `<overtaker-name>` on
     `<date>` — see its own hot.md for the folded state." `log.md` is
     **never merged** — it stays as provenance, untouched.

3. **Point — the determinism anchor (AB3).** `workstream:manifest`
   ("absorbed[] — append", underlying: `python3 scripts/manifest.py
   append-absorbed <overtaker-born-session> --name <stale-name>
   --absorbed-born-session <stale-born-session> --dir
   <stale-ws-dir-path> [--transcript <stale-transcript-path-if-still-
   on-disk>]`) — a direct write on the overtaker's OWN manifest field.
   Resolve the transcript path via the stale workstream's matching
   sidecar (`born_session` match) — **best-effort**: transcripts can
   vanish from disk (measured), so omit `--transcript` rather than
   guess. The dir path is durable — always include it.

4. **Carry — the overtaker adopts the mission.**
   - `focus` → `workstream:manifest` ("focus") on the overtaker's own
     manifest, reflecting that it now owns the stale workstream's work.
   - `projects[]` → union of both manifests' `projects` lists,
     deduped — the one direct write not routed through a named
     `manifest.py` primitive route (schema-trivial, no invariant to
     enforce): `workstream:manifest` ("projects[] — union/add") with
     the merged array.
   - **policy + `maintains[]`** — fold the stale workstream's
     `policy.md` rules and manifest `maintains[]` entries into the
     overtaker's. Dir artifacts (everything else under the stale
     workstream's own dir) stay in place — nothing deleted.
   - **Collision handling (AB5)** — if a stale policy rule or
     `maintains[]` entry conflicts with something the overtaker
     already holds (contradictory rules, or the same path claimed by
     both under different terms): **escalate to grill, never a silent
     overwrite.** Check `workstream_lib.grill_available()`: if True,
     invoke `grill:grilling` to interrogate the conflict to a
     resolution; if False, run one inline numbered round (❓/➡️ shape)
     instead. Apply the resolved answer through `workstream:policy` /
     `workstream:manifest` as appropriate.

5. **Close the stale manifest — as absorbed, not closed.** This is the
   ONE sanctioned cross-manifest write in the whole plugin:
   `workstream:manifest` ("absorb-close" — underlying: `python3
   scripts/manifest.py absorb-close <stale-born-session> --by-name
   <overtaker-name> --by-session <overtaker-born-session>
   [--vault-lock .vault-meta/bin/vault-lock.sh --lock-session
   <this-session-id>]`). Sets `state: absorbed` +
   `absorbed_by`/`absorbed_by_session` together, vault-lock-wrapped
   when the lock is available, degrading to an unlocked write + stderr
   warning otherwise (never blocks on the lock's absence — AB1's
   failure mode was blindness, not a torn write). **Never touches
   `spawned_from`/`spawned_from_session`** — immutable everywhere,
   absorption included.

6. **Re-home edges.** Scan every manifest for reporters whose
   `direct_report.session` equals the stale node's `born_session` —
   each re-homes to the stale node's OWN `direct_report` (or `null`),
   via `workstream:manifest` on its own manifest (notify live /
   vault-lock closed, `--session <this-session-id>`). If the overtaker
   itself reported to the stale node, it re-homes the same way — the
   report graph is a single-parent tree, so this can never cycle. Then
   drop dangling `collaborate[]` entries pointing at the stale node
   (`workstream:manifest` "collaborate — remove" on each counterpart).
   Zero matches on either scan → note "no direct-reports to re-home" /
   "no collaborators to drop" — don't skip the scan itself.

7. **Notify UNCONDITIONALLY** — every affected session, regardless of
   whether the session-mgmt MCP reports it running (measured
   unreliable — a send has delivered to a session the index reported
   not-running):
   - **The stale session** — told explicitly it was absorbed by
     `<overtaker-name>` and should wind down.
   - **The overtaker session** — told it absorbed `<stale-name>` and
     inherited its mission.
   - **Every workstream whose manifest names `<stale-name>` in
     `spawned_from`** (fork-descendants, including unreconciled
     `(fork)` sessions) — scan for `spawned_from == <stale-name>` and
     notify each: "Your spawned_from ancestor `<stale-name>` was
     absorbed by `<overtaker-name>`." Heads-up only — their own
     `spawned_from` is immutable and untouched. Don't skip this scan
     even with zero matches.
   - A failed notify never blocks the absorption — the manifest write
     already stands (step 5).

8. **Offer archival** of the stale session to the principal — always
   confirmed, never automatic.

9. **Regenerate the fleet views.** `python3 scripts/views.py regen`.

10. **Confirm** what moved: the folded hot-cache content, the
    overtaker's updated focus/projects, how many direct-reports
    re-homed and any collaborators dropped, who was notified, whether
    archival was offered/accepted, and the final state of both
    manifests.

## Discipline

`absorbed` is terminal like `closed` — never reopened by hand-editing
`state` back to `active`; a revived mission is a new workstream. A
fitting rename often pairs with an absorption — that's `workstream:refocus`'s
job, not this skill's. Every routed manifest write goes through
`workstream:manifest`'s named routes; `absorb-close` is the sole
sanctioned cross-manifest write in this plugin. Single-writer-per-
manifest governs everything else exactly as in `connect`/`close`. A
policy/maintains collision always goes to grill (or its inline
fallback) — never a silent overwrite. `log.md` is never merged. Never
touches a vault project/spine node.
