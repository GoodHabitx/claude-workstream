---
name: fork
description: Birth a fork — run INSIDE a spawned session after the principal's native /fork to self-name it, write its own birth manifest (spawned_from + spawned_from_session) via manifest.py create, ASK whether to direct-report to and/or collaborate with the parent (default NEITHER), and notify the parent. Trigger on "reconcile this fork", "wire up this forked session", "/workstream:fork", or a session whose title ends "(fork)". Do NOT use for the general reconciliation audit (workstream:connect), to adopt a root (workstream:adopt), or to rename (workstream:refocus).
---

# Workstream fork

Shares the shared model: `${CLAUDE_PLUGIN_ROOT}/docs/workstream-model.md`.

Birth a fork into the workstream graph — runs **inside the spawned
`(fork)` session** after the principal's native `/fork`. Every manifest
write routes through `scripts/manifest.py` — this skill decides WHEN,
the primitive owns HOW. Reconciling forks from *elsewhere* (an audit
that drives a remote `(fork)` session to run this verb) is
`workstream:connect`, not this. Adopting a brand-new root is
`workstream:adopt`. Renaming is `workstream:refocus`.

*(interpreter-shim caveat: neither `python3` nor `python` resolves on
every host — try `python3`, then `python`, then `py -3`, then `py`.)*

## Procedure

1. **Hard-dependency precheck FIRST** — fork mints a manifest, same as
   adopt: `workstream_lib.adopt_precheck()`. On `(False, msg)`, print
   `msg` and STOP.

2. **Runs ONLY inside THIS spawned session.** Confirm this session's
   own title ends `(fork)`. If not, STOP — point to `workstream:connect`
   for reconciliation from elsewhere.

3. **Idempotency first — key on the stable sidebar uuid, not the
   transcript id.** Get this session's own bare uuid (the `<!--
   workstream-session-id: ... -->` line's value works for a
   root/fork session — it IS the sidebar uuid here). `python3
   scripts/manifest.py read <this-session-id>` (exit 0 = already
   birthed by a prior, possibly partial, run):
   - **Exists** → do NOT re-mint or re-ask Q2. **Resume the binding**:
     `python3 scripts/sidecar.py self-heal <session_id>
     <this-session-id> --name <name> --focus <focus>` (values from the
     existing manifest) if `python3 scripts/sidecar.py check
     <session_id> <this-session-id>` reports `missing`; re-apply the
     title `ws-<name>`. **Re-run step 8's parent notify** (reconstruct
     from the resumed manifest's `spawned_from` + any `direct_report`/
     `collaborate`) — a birth run can crash between sidecar-write and
     notify. **Re-pose Q2 (step 6)** if the resumed manifest is still
     birth-empty (`direct_report: null` AND `collaborate: []`) — that
     state is indistinguishable from a genuine "answered neither," so
     re-asking is the safe recovery. Then STOP; skip minting.
   - **Doesn't exist** → proceed.

4. **Recover the parent from the base title.** Strip one trailing `
   (fork)`. If the base title still ends `(fork)`, the parent is
   itself an unreconciled fork — tell the principal to reconcile the
   parent first, STOP. Otherwise strip a leading `ws-` to get the
   parent name. Scan the state root's manifests for that name — no
   match → the parent may have been renamed since the native `/fork`
   (this lookup is by name, not uuid — the one non-rename-safe edge).
   Ask the principal to confirm the parent (offer `workstream:list`'s
   roster); if they can't, flag orphaned and STOP.

5. **Self-name a short kebab `<new-name>`** from this session's own
   distinct work since the fork. If nothing distinguishes it yet, pick
   a provisional name and say it's renameable any time
   (`workstream:refocus`) — never locked. Check the name is free
   (scan manifests); on a collision, disambiguate with a variant (fork
   cannot refuse — the session is already forked).

6. **Birth this session's OWN manifest.** `python3 scripts/manifest.py
   create <this-session-id> --name <new-name> --focus "<mission>"
   --spawned-from <parent-name> --spawned-from-session
   <parent-born-session>` (read the parent's `born_session` from its
   own manifest first — a read, never a write). Writes the canonical-
   empty relationship fields (`direct_report: null`, `collaborate: []`,
   no `parents` key) automatically.

   **Then bind the session immediately — before step 7's relationship
   writes.** `python3 scripts/sidecar.py write <session_id>
   <this-session-id> --name <new-name> --focus "<mission>"`. This makes
   step 7's `workstream:manifest` calls resolve normally (via the
   sidecar) instead of needing a delegate bypass.

7. **The Q2 ask — always, at every real fork birth. Default NEITHER.**
   Ask the principal:
   - **(a) "Direct-report to `<parent>`?"** — yes → `workstream:manifest`
     "direct_report — set" targeting the parent.
   - **(b) "Collaborate with `<parent>` — over what scope?"** — yes →
     `workstream:manifest` "collaborate — add" (its reciprocity notify
     covers the parent).
   - "no"/"skip"/silence → leave both at their birth-empty values. This
     ask is the whole reason `fork` exists as its own verb — never skip
     it, never assume a relationship the principal didn't ask for.

8. **Retitle this session.** `set_session_title("self", "ws-<new-name>")`
   (dropping ` (fork)`). Sidecar already written at step 6.

9. **Notify the parent** (via `workstream:notify`): "new child
   `<new-name>` bound; spawned_from = you" plus, if set in step 7, "and
   it reports to you" / "and it collaborates with you over `<scope>`."
   Avoid a double-message: if step 7 added a collaborate entry, that
   already sent its own reciprocity notify — this is the single
   lineage announcement, mentioning collaboration only as FYI. Never
   edit the parent's manifest directly. **Graceful notify:** a failed
   send to a reachable-looking parent is WARN + PROCEED — the child's
   manifest is already correct; `workstream:connect` reconciles later.

10. **Regenerate the fleet views.** `python3 scripts/views.py regen`.

11. **Confirm** in one line: child born and bound as `ws-<new-name>`,
    `spawned_from = <parent>`, and which of direct_report/collaborate
    (if any) were set.

## Discipline

Single-writer-per-manifest: this skill writes only THIS session's own
manifest; the parent learns via `notify` and self-applies anything it
wants recorded. The assistant never creates the session — native
`/fork` is the principal's action. Lineage (`spawned_from`/
`spawned_from_session`) is minted once here and never rewritten by
anything. The name is never locked. Default NEITHER on the Q2 ask.
Every manifest write routes through `workstream:manifest`'s underlying
`manifest.py`, every message through `workstream:notify`. Never touches
a vault project/spine node.
