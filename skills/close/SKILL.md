---
name: close
description: Mark a workstream done — set its manifest state to closed via workstream:manifest, surface its lineage-up plus its reporters and collaborators, re-home each reporter one level and drop each collaborator's reciprocal link, then note completion. Never deletes history; close takes no successor. Trigger on "close this workstream", "this is done", "retire it". Do NOT use to complete a single task (the vault's own task-manager done verb), fold into an overtaker (workstream:absorb), or adopt.
---

# Workstream close

Shares the shared model: `${CLAUDE_PLUGIN_ROOT}/docs/workstream-model.md`.

Mark a finished workstream done — the "satisfied" ending, distinct from
`workstream:absorb`'s "superseded." Every manifest field write routes
through `workstream:manifest`'s named routes — never hand-rolled JSON.
Never touches a vault project/spine node.

*(interpreter-shim caveat: neither `python3` nor `python` resolves on
every host — try `python3`, then `python`, then `py -3`, then `py`.)*

## `close [name]`

1. **Target** = THIS session's bound workstream (`python3 scripts/sidecar.py resolve <session_id>`), or the named `<name>`
   resolved by scanning the state root's manifests. Hold the target's
   `born_session` uuid — every dependent edge resolves against it.

2. **Surface the dependents this close affects — read-only, BEFORE any
   write.** Scan every manifest, resolving edges by their session half:
   - **Lineage up** — the target's own `spawned_from`/
     `spawned_from_session`. **Never touched by close** — surfaced only
     so the principal sees where the branch sat.
   - **Reporters** — every manifest whose `direct_report.session`
     equals the target's `born_session`. Re-homed in step 4.
   - **Collaborators** — every manifest with a `collaborate[]` entry
     whose `session` equals the target's `born_session`. Dropped in
     step 5.

   Present all three. Closing a node with reporters/collaborators is
   not a hard block, but get an explicit go before proceeding.

3. **Mark it closed.** `workstream:manifest` "state — set" → `closed`
   on the target. Close takes **NO** successor — do NOT write
   `absorbed_by`/`absorbed_by_session` (that belongs only to
   `workstream:absorb`). Never touch `spawned_from`. When the target is
   THIS session's own workstream, write directly; when closing a named
   OTHER workstream, notify its own session (live) or vault-lock it
   (closed, `--session <this-session-id>`) — single-writer, never a
   reach-in. **Ordering is deliberate**: the state flip lands here,
   ahead of steps 4-5, so step 5's collaborate reciprocity-remove into
   the now-closed target can take the clean closed-target vault-lock
   path.

4. **Re-home the reporters — one level up.** For each reporter from
   step 2, re-point its `direct_report` to the **target's own
   `direct_report`** (read off the target manifest in step 2), or to
   `null` if the target had none. Each reporter makes this edit on its
   OWN manifest via `workstream:manifest` ("direct_report — set/clear")
   — driven by notify (live reporter self-applies) or vault-lock
   (closed reporter, `--session <this-session-id>`). Announce, never
   silent. Zero reporters → note "no reporters to re-home."

5. **Drop the collaborators' reciprocal links — both sides.** For each
   collaborator from step 2, the counterpart runs
   `workstream:manifest` ("collaborate — remove") on its OWN manifest
   — via notify (live) or vault-lock (closed, `--session <this-session-id>`). This route is inherently two-sided: dropping
   the counterpart's entry pointing at the target ALSO reciprocity-
   removes the mirror on the target's manifest (now closed, so this is
   the closed-target vault-lock write). Zero collaborators → note it.

6. **Record what shipped.** Note the closing summary — Ballast's
   `hot.md` continuity, not a hand-write by this skill (X7/B2): if the
   target is THIS session, the closing note lands the normal way this
   session already reports its own state; for a named other
   workstream, fold the summary into the `notify` message (live) so
   that session records it, or leave a short note via vault-lock
   (closed) if that's the only path.

7. **Regenerate the fleet views.** `python3 scripts/views.py regen`.

8. **Never destroy.** close marks state; it never deletes the
   manifest, caches, or history. Archiving the session itself is a
   separate, principal-confirmed step (`mcp__ccd_session_mgmt__archive_session`,
   which always prompts) — offer it, never do it unprompted.

9. **Confirm** in the report: the target's final state (`closed`), its
   surfaced lineage-up, how many reporters re-homed and to where, how
   many collaborators dropped their link, who was notified, and
   whether archival was offered.

## Discipline

`closed` is terminal — never reopened by hand-editing `state` back to
`active`; a revived mission is a new workstream. close differs from
`absorb` on exactly one axis: close writes no successor, absorb names
one. Both run identical re-home choreography over reporters and
collaborators, and both leave `spawned_from` untouched. Single-writer-
per-manifest governs every step — a cross-manifest change happens only
via `notify` or `vault-lock` (`--session <this-session-id>`), never a
direct reach-in. Never touches a vault project/spine node.
