---
name: manifest
description: Set a field on this session's own workstream manifest — who it reports to, a collaborator (with scope), the focus, or an append-only history entry. Edits ONLY this session's own workstream.json (single-writer); bare invocation shows + validates it read-only. Trigger on "set who this reports to", "add/remove a direct report", "collaborate with X", "update the manifest field", "/workstream:manifest". Do NOT use for policy (workstream:policy), rename/refocus (workstream:refocus), adopt (workstream:adopt), or close/absorb a workstream.
---

# Workstream manifest

Shares the shared model: `${CLAUDE_PLUGIN_ROOT}/docs/workstream-model.md`.

Own `.vault-meta/workstreams/<born_session>/workstream.json` (path from
`workstream_lib.state_root()` / `config.json`'s `state_root`) — the
schema-authority primitive every composite verb (`fork`, `refocus`,
`connect`, `absorb`, `close`, `adopt`) routes its manifest writes
through, rather than hand-rolling JSON. All writes go through
`scripts/manifest.py` (core, already built — do not edit it). Run this
**in the session** whose manifest you're changing. Never touches a vault
project/spine node.

*(interpreter-shim caveat: on any script invocation below, neither
`python3` nor `python` resolves on every host — try `python3`, then
`python`, then `py -3`, then `py`.)*

## `/workstream:manifest [natural-language field change]`

1. **Target = THIS session's bound workstream, always.** Read the
   `<!-- workstream-session-id: ... -->` line boot.py injected this
   session for the transcript `session_id`. Resolve: `python3 scripts/sidecar.py resolve <session_id>` → JSON `{born_session, ws_dir}` on stdout, exit 0. **Exit 1 (no sidecar) → refuse** — this
   session isn't bound — and point to `/workstream:adopt`. There is no
   cross-session target argument; this verb never takes a `<name>`
   naming a different workstream.

2. **Single-writer, extended to every field.** This verb edits ONLY
   this session's OWN `workstream.json`. To change another workstream's
   manifest: notify its own session (live, see `workstream:notify`) and
   let it self-apply, or — closed target only — `bash .vault-meta/bin/vault-lock.sh acquire <path> --session <this-session-id>`, write directly via `manifest.py set`, `release`.
   Never a direct reach-in on a live peer.

3. **Bare invocation = show + validate + verify, read-only, STOP.**
   `python3 scripts/manifest.py read <born_session>` and print it.
   Validate: `direct_report` a single `{name, session}` object or
   `null` (never a list); `collaborate` a list of `{name, session, scope}`; `refocused`/`previous_names`/`projects`/`maintains`/
   `absorbed` are arrays. An **active** manifest carrying legacy
   `parents`/`parent`/`parent_session`/`rebound` is flagged (dropped
   fields per the schema — offer a strip via step 5's field write, any
   field write on this manifest already drops them). Confirm `hot.md`
   exists under the ws_dir (Ballast's job, not this skill's to create).

4. **Four fields are APPROVAL-GATED. Show Adam the exact change, then
   mint.** `maintains`, `direct_report`, `collaborate` and `absorbed`
   reshape the fleet — who this workstream answers to, who it works
   with, what it owns, what it swallowed — and each shows up in another
   workstream's own graph, so an unreviewed edit silently rewires the
   org chart. `manifest.py` refuses a change to one of them without a
   fresh approval token (exit 2, the mint command on stderr). Every
   other field — `state`, `focus`, `name`, lineage, the immutables, the
   append-only arrays, `last_touched` — is written with no ceremony.

   Before any write marked **(gated)** below:

   a. Print the change EXACTLY: the field, its value NOW (read it from
      the manifest — do not recall it), and its value AFTER, both in
      full. For a list, show the whole list before and after, not the
      delta; the point is that he sees what will be on disk.
   b. Wait for an explicit yes. Silence, "sounds good" to something
      else, or a reply to a different question is not a yes.
   c. Mint the one-time approval:
      ```
      approve.py mint --scope <ws_dir>/ballast.json --file <ws_dir>/workstream.json --field <field>
      ```
      (`approve.py` lives in the installed `ballast` plugin's `scripts/`;
      `manifest.py` resolves it the same way, so run it from there.)
   d. Run the `manifest.py` command. It consumes the approval — one
      approval, one write. A second write needs a second yes.

   No approval can be minted if `ballast` is not installed; the refusal
   says so and names the deliberate escape hatch
   (`ballast-gate.disabled` under the state root), which is an operator
   decision, never this skill's to take.

5. **Field-change writes — route to the right `manifest.py` subcommand,
   never hand-roll the JSON:**
   - **focus** → `manifest.py set <born_session> focus '"<text>"'`. Do
     **not** also touch the sidecar — that's `workstream:refocus`'s
     front-facing job, not this primitive's.
   - **direct_report — set (gated)** → resolve the target's `born_session` by
     reading ITS manifest (`manifest.py read <target-born-session>` — a
     read, never a write), then `manifest.py set <born_session> direct_report '{"name":"<target-name>","session":"<target-born-session>"}'`.
     Refuse a self-report. Refuse (or warn loudly if the ask is
     explicit) a target whose `state` is `closed`/`absorbed` — that's
     an immediate connect invariant-3 violation; point at the target's
     own `direct_report` instead. This same route is what a **re-home**
     is (used by `close`/`absorb`).
   - **direct_report — clear (gated)** → `manifest.py set <born_session> direct_report null`.
   - **collaborate — add (gated)** → resolve the peer's `born_session` (read
     its manifest; refuse if it doesn't resolve; refuse a
     self-collaboration) → `manifest.py collaborate-add <born_session> --peer-session <peer-born-session> --peer-name <peer-name> [--scope <text>]` (idempotent on a duplicate peer-session — the
     core script no-ops rather than double-adding). Then **reciprocity**:
     notify the peer's live session (via `workstream:notify`) to add its
     own mirror entry the same way; if the peer is closed/unreachable,
     `vault-lock acquire` its manifest, `manifest.py collaborate-add`
     on it directly, `release`. **Graceful notify**: a failed send to a
     target that looked reachable is WARN + PROCEED — the local half
     already stands; `connect`'s audit reconciles the one-sided edge
     later. Never hard-fail the op on a notify failure.
   - **collaborate — remove (gated)** → `manifest.py collaborate-remove <born_session> --peer-session <peer-born-session>`. Reciprocity-
     remove the mirror the same way (notify live / vault-lock closed).
   - **refocused — append** → `manifest.py set <born_session> refocused '[{"date":"<iso-date>","from_focus":"<old>","to_focus":"<new>","note":"<why>"}]'` — the WHOLE array (read current, append
     `{date, from_focus, to_focus, note}`, write back — append-only,
     never edit/trim an existing entry). Called by `workstream:refocus`.
   - **name / previous_names** → **redirect the caller to
     `workstream:refocus`** — this primitive writes `name` +
     `previous_names` only as refocus's *delegate*; a bare rename here
     would leave the session title + sidecar stale. If invoked as that
     delegate: `manifest.py set <born_session> name '"<new>"'` then
     `manifest.py set <born_session> previous_names '["<old-name>"]'` — the WHOLE array, with the old name appended.
   - **state — set** (`active|closed|absorbed`) → `manifest.py set <born_session> state '"<state>"'`. Normally the delegate of
     `workstream:close` (→ closed) or `workstream:absorb` (→ absorbed),
     which own the surrounding dependents-surfacing and re-home work
     before flipping this field. A flip to `absorbed` MUST pair with
     `absorbed_by`/`absorbed_by_session` (below); `closed` takes no
     successor. **Never touch `spawned_from`/`spawned_from_session`**
     — lineage is immutable.
   - **absorbed_by — set** → resolve the successor's `born_session`
     (read its manifest), refuse a self-succession, then `manifest.py set <born_session> absorbed_by '"<successor-name>"'` and
     `manifest.py set <born_session> absorbed_by_session '"<successor-born-session>"'` — set together with `state: absorbed`. This is `workstream:absorb`'s delegate write.
   - **absorbed[] — append (gated)** → `manifest.py append-absorbed <born_session> --name <name> --absorbed-born-session <id> --dir <path> [--transcript <path>]`. This is `workstream:absorb`'s
     delegate write (AB3, the determinism anchor) — never hand-rolled.
   - **maintains[] — add/remove (gated)** → `manifest.py set <born_session> maintains '["<wikilink-or-abs-path>"]'` — the WHOLE array (read current, add/remove the
     wikilink or absolute path, write back). Lives in the manifest, not
     `policy.md` (I6) — `workstream:policy`'s `## Maintains` route is
     retired; this is the only place `maintains[]` is written.
   - **projects[] — union/add** → `manifest.py set <born_session> projects '["<project-a>","<project-b>"]'` — the WHOLE array. Informational only, never validated.
   - **schema init (adopt/fork delegate)** → `manifest.py create <born_session> --name <name> [--focus <text>] [--spawned-from <parent> --spawned-from-session <parent-born>]`
     — the core script writes the full canonical-empty shape (no
     `parents` key, `direct_report: null`, `collaborate: []`,
     `refocused: []`, `previous_names: []`, `projects: []`,
     `maintains: []`, `absorbed: []`). Refuses (exit 2) if the manifest
     already exists — never call this on an existing `born_session`.
     Used by `workstream:adopt` and `workstream:fork`.
   - **absorb-close (the ONE sanctioned cross-manifest write)** →
     `manifest.py absorb-close <stale_born_session> --by-name <overtaker-name> --by-session <overtaker-born-session> [--vault-lock .vault-meta/bin/vault-lock.sh --lock-session <this-session-id>]` — sets `state: absorbed` +
     `absorbed_by`/`absorbed_by_session` on the STALE manifest,
     vault-lock-wrapped when the lock path is given, degrading to an
     unlocked write + stderr warning otherwise. This is
     `workstream:absorb`'s delegate write for step 4 of that skill —
     the only route in this whole plugin that touches a manifest other
     than the caller's own.

6. **Regenerate the fleet views** after any write: `python3 scripts/views.py regen`.

7. **Confirm** in one line: which field changed, old → new, and any
   reciprocity notify performed.

## Discipline

`maintains`/`direct_report`/`collaborate`/`absorbed` are approval-gated
(step 4): show the exact before/after, get an explicit yes, mint, write —
one approval, one write, never a standing pass. The gate proves only that
the sanctioned path was used; what actually asks Adam is step 4's own
procedure, so skipping it and minting first is the one way to make the
whole mechanism meaningless.

Single-writer-per-manifest, extended to every field write: a session
sets a field only on its OWN `workstream.json`; a cross-manifest change
happens only via `notify` (live target self-applies) or `vault-lock`
(closed target, `--session <this-session-id>`) — never a direct
reach-in, except the one sanctioned `absorb-close` route. Append-only
fields (`refocused`, `previous_names`) are never edited or trimmed.
`direct_report` is a single object or `null`, never a list; reciprocity
is `collaborate`-only. Calls `workstream:notify` for every
reciprocity/re-home message — never a raw MCP `send_message` with a
bare uuid. A failed notify to a target that looked reachable is a
WARN-and-PROCEED, never a hard-fail. Never touches a vault project/spine
node.
