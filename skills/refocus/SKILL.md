---
name: refocus
description: Change a workstream's focus, name, policy, or links from its own session — a grilling-driven refocus. Invokes grill:grilling (degrading to one inline numbered round if grill isn't installed) to settle the changes, then routes every manifest write through workstream:manifest and policy write through workstream:policy, and owns the front-facing rename (title + sidecar). Renaming is never locked; re-parenting is gone. Trigger on "rename this workstream", "fix the focus", "change who this reports to". Do NOT use to adopt (workstream:adopt) or reconcile forks (workstream:connect).
---

# Workstream refocus

Shares the shared model: `${CLAUDE_PLUGIN_ROOT}/docs/workstream-model.md`.

> These commands run FROM THE VAULT ROOT: each script reads the vault as `os.getcwd()`, and `${CLAUDE_PLUGIN_ROOT}` resolves to this plugin's own install directory.

Change what a workstream *is* after birth — focus phrase, name, standing
policy, or relationship links. Interviews the principal to a settled
set of changes, then applies each by routing it through the owning
primitive. Run this **in the session** whose workstream you're
refocusing. Never touches a vault project/spine node.

**Re-parenting is GONE.** `spawned_from` is immutable and never
rewritten by refocus; relationship links change only through
`workstream:manifest`'s `direct_report`/`collaborate` routes.

*(interpreter-shim caveat: neither `python3` nor `python` resolves on
every host — try `python3`, then `python`, then `py -3`, then `py`.)*

## Procedure

1. **Target = THIS session's bound workstream, always.** Resolve via
   `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/sidecar.py resolve <session_id>`. Exit 1 → refuse,
   point to `/workstream:adopt`. No cross-session target.

2. **Grill to a settled set of changes.** Check
   `workstream_lib.grill_available()` first. If **True**: invoke the
   Skill tool with `grill:grilling`, interrogating the principal over:
   - **focus** — is the one-line mission still right? What should it
     become?
   - **name** — should the workstream be renamed? To what?
   - **policy** — does a standing policy or a maintains declaration
     need adding/editing/removing?
   - **links** — should `direct_report` or a `collaborate` entry
     change? Who does it report to now; who does it collaborate with,
     over what scope?
   If **False** (grill not installed): run one inline numbered round in
   the same shape — number each open question, give a recommended
   answer with `➡️`, ask them all in one turn, then proceed with the
   answers. Never start writing off a one-line ask either way — pin
   down the concrete change list first.

3. **Apply the settled changes by routing each write through the
   owning primitive** — never hand-roll a manifest or policy edit:
   - **focus** → `workstream:manifest` ("focus"), THEN update the
     sidecar's focus mirror to match, right here (`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/sidecar.py write <session_id> <born_session> --name <name> --focus "<new-focus>"`) — **including a focus-only refocus
     with no rename** (step 4 is rename-gated and never runs then).
   - **name** (+ its `previous_names` append) → `workstream:manifest`
     ("name"/"previous_names" — refocus's delegate) — but the
     front-facing half (collision check, title, sidecar) is refocus's
     own, step 4.
   - **direct_report — set/clear** → `workstream:manifest`.
   - **collaborate — add/remove** → `workstream:manifest` (scope is
     freeform text from the grill).
   - **policy / maintains** → policy edits via `workstream:policy`;
     a `maintains[]` add/remove goes through `workstream:manifest`
     ("maintains[]" route) — `## Maintains` no longer lives in
     `policy.md` (I6).

4. **Front-facing rename — refocus OWNS this half. Run ONLY when the
   settled changes include a rename:**
   - **Check the new name is free** — scan the state root's manifests.
     A collision → REFUSE the rename, offer a different name — the
     only thing that blocks a rename in this model. A refused rename
     does NOT abort the rest — still apply every other settled change
     from step 3.
   - Let `workstream:manifest` write `name` + append `previous_names`
     first (step 3), THEN: `set_session_title("self", "ws-<new-name>")`
     and `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/sidecar.py write <session_id> <born_session> --name <new-name> --focus <current-focus>` (the sidecar's title
     mirror; focus mirror untouched here if it didn't also move —
     step 3 already wrote it if it did).
   - **The uuid-keyed cache dir never moves.**
   - **No ripple required.** Every cross-manifest reference (`spawned_from`,
     `direct_report.name`, each `collaborate[].name`, `absorbed_by`) is
     a re-derivable display cache — every view resolves by the
     immutable session half, then reads the target's CURRENT name.
     The instant the manifest+title write lands, every stale
     `<old-name>` reference elsewhere is already harmless.
   - **Optional cosmetic ripple (never required, skipping is fully
     supported)**: if wanted, scan other manifests for a name half
     referring to `<old-name>` and update only that half — via notify
     (live owner self-updates) or vault-lock (closed, `--session <this-session-id>`), never a direct reach-in.
   - **Regenerate the fleet views** (recommended, not load-bearing):
     `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/views.py regen`.

5. **Always append a `refocused` history entry** — via
   `workstream:manifest` ("refocused — append"): `{date, from_focus, to_focus, note}` (`note` records what else the refocus touched —
   name/links/policy). Append-only.

6. **Confirm exactly what changed** — focus (old → new), name (with the
   retitle and whether the cosmetic ripple ran, plus its count), each
   link change, any policy/maintains edit, a rename refused for a name
   collision, and the `refocused` entry appended.

## Discipline

A composite verb: decides WHEN, calls the primitives to change it —
every manifest write via `workstream:manifest`, every policy write via
`workstream:policy`, the interview via `grill:grilling` (or its inline
fallback). Renaming is never locked — only a name collision refuses it.
Re-parenting is gone. Append-only fields (`refocused`, `previous_names`)
are never edited or trimmed. Single-writer: a session refocuses only
its OWN workstream. Never touches a vault project/spine node.
