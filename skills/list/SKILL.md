---
name: list
description: Roll-call every workstream session — merge this session, keep the ws-/cos-prefixed titles, join to each workstream's own manifest for state/focus/provenance/maintains/absorbed, and flag any (fork) sessions not yet connected. Read-only; degrades to a manifest-only listing (no liveness column) when the session-mgmt MCP is unavailable. Trigger on "list workstreams", "what workstreams are running", "workstream roll-call", "show my workstreams". Do NOT use to adopt (workstream:adopt), reconcile forks (workstream:connect), or message one (workstream:notify).
---

# Workstream list

Shares the shared model: `${CLAUDE_PLUGIN_ROOT}/docs/workstream-model.md`.

Roll-call of every workstream session. Read-only — writes nothing.
Never reads or joins against any vault project/spine data — only
session titles and workstream manifests.

## Procedure

1. **Gather sessions.** Call `mcp__ccd_session_mgmt__list_sessions`
   and `get_session("self")`, merge self in (`list_sessions` excludes
   the caller). **MCP unavailable** → degrade: skip this step entirely
   and go straight to step 2b's manifest-only listing (state, focus,
   edges — no `isRunning`/liveness column), noting the degrade in the
   report header.
   Keep only titles matching `^ws-` (canonical) or `^cos:` (legacy).

2. **Gather manifests.** Scan every `workstream.json` under the state
   root (`config.json`'s `state_root`, default `staff/cos/workstreams`)
   via `python3 scripts/views.py regen --dry-run` (reads and reports
   without writing) or by reading each manifest directly.

   a. **With sessions available**: split the kept titles into **bound
      workstreams** (`ws-<name>`/legacy `cos:<name> — <focus>`, no
      trailing `(fork)`) and **unreconciled forks** (`(fork)`-suffixed
      — flag as needing `workstream:connect`). Join each bound title to
      its manifest by `name`. No manifest found for a matched title →
      flag as an anomaly.

   b. **Manifest-only (degraded or by choice)**: list every manifest
      directly — `name`, `state`, `focus`, `created`,
      `spawned_from`/`direct_report`/`collaborate` (resolved by
      session-half to each target's CURRENT name), `maintains[]`,
      `absorbed[]`, `projects[]`. No liveness column.

3. **Report a table**: name | title | running? (if available) | last
   activity (if available) | state | `spawned_from` (resolve
   `spawned_from_session` to the target's CURRENT name, else "root") |
   `direct_report` (resolve `.session` to the target's CURRENT name,
   else "—") | `collaborate` (each entry resolved + its scope,
   comma-joined, else "—") | `maintains` (count + list) | `projects`
   (if any). **Badge terminal targets**: if a resolved
   `spawned_from`/`direct_report`/`collaborate[]` target's own `state`
   is `closed`/`absorbed`, append `[closed]`/`[absorbed]`. **Show a
   node's own succession**: when a listed workstream's own `state` is
   `absorbed`, resolve `absorbed_by_session` to the overtaker's
   CURRENT name and render beside the state (`absorbed → <overtaker>`)
   — and if it carries `absorbed[]` entries, list what it swallowed
   (name + dir, best-effort transcript). A `session` half resolving to
   no manifest at all is an UNRESOLVED stub — flag it.

4. **Also surface, informationally only:** bound sessions whose
   title's name has no manifest (anomaly); and the **three derived
   axes** (a workstream may appear in one, several, or none): a
   **lineage tree** (`spawned_from` edges), a **report tree**
   (`direct_report` edges — a target's inbound reporters are derived
   by scanning for manifests whose `direct_report.session` matches its
   `born_session`, stored nowhere), and **collaborate links**
   (bidirectional, peer, no authority — flag any one-sided link where
   the counterpart doesn't list the mirror). Point to
   `staff/cos/workstreams/index.md` and `staff/cos/workstream-graph.md`
   as the durable, regenerated cross-session views when they exist.

5. **Never write anything** — no title, sidecar, or manifest. Pure
   report.

## Discipline

Reconstructs its view from titles + every manifest every run; trusts no
sidecar as a registry; fixes nothing on its own — a gap it surfaces is
a human decision or a `connect`/`close`/`absorb` call. Renders three
orthogonal axes — lineage, reporting, collaboration — never the retired
multi-parent `parents` axis. Every cross-manifest reference is resolved
fresh by its session half to the target's CURRENT name; the stored
name-half alongside each edge is only a display cache. Degrades to a
manifest-only listing without the session-mgmt MCP, with no liveness
column — never fails outright for its absence.
