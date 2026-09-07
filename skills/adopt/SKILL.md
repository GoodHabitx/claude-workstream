---
name: adopt
description: Turn THIS session into a workstream — birth a new manifest, sidecar, and hot cache. Bare /workstream:adopt, optionally with a name + one-line focus (else guessed from context). Self-heals if this session already owns a matching-titled manifest; refuses on a mismatched or taken name. Trigger on "make this a workstream", "adopt this session", "bind this session", "track this session". Do NOT use to fork a tangent (native /fork then workstream:fork), or to roll-call (workstream:list).
---

# Workstream adopt

Shares the shared model: `${CLAUDE_PLUGIN_ROOT}/docs/workstream-model.md`.

Turn the current session into a workstream — the bare, default entry to
the `/workstream:*` suite (renamed from `/cos:workstream`, E1). This
skill only **births a brand-new chain by promotion of THIS session**.
For a tangent's child session use native `/fork` then `workstream:fork`;
for renaming an existing workstream use `workstream:refocus`. `spawn`
and `rebind` are both **retired** (L2, L3) — there is no "open a second
session for an existing workstream" verb any more; the sidecar
primitive's self-heal (`workstream:sidecar`) covers the one real
adjacent need (a missing/stale sidecar).

*(interpreter-shim caveat: neither `python3` nor `python` resolves on
every host — try `python3`, then `python`, then `py -3`, then `py`.)*

## `/workstream:adopt [name] [— focus]`

1. **Hard-dependency precheck FIRST — before touching anything.** Call
   `workstream_lib.adopt_precheck()` (import the core lib, or run a
   one-liner: `python3 -c "import sys,os;
   sys.path.insert(0,'scripts'); import workstream_lib as w;
   ok,msg=w.adopt_precheck(); print(msg) if not ok else None;
   sys.exit(0 if ok else 1)"` from the plugin root). On `(False, msg)`
   — Ballast is not installed — **print `msg` verbatim and STOP.**
   Identity without continuity is the defect this plugin exists to fix,
   not a feature to ship anyway (the HARD dependency, per
   `docs/dependencies.md`).

2. **Learn this session's identity.** Read the `<!--
   workstream-session-id: ... -->` line boot.py injected. This is the
   candidate `born_session` (the bare uuid). If the line is missing,
   boot.py didn't fire — say so, don't guess.

3. **Check whether THIS session's own uuid is already a workstream —
   BEFORE touching any name.** `python3 scripts/manifest.py read
   <this-session-id>` (exit 0 = exists):
   - **Exists and this session's title matches `^ws-<that manifest's
     name>`** → plain **self-heal**: `python3 scripts/sidecar.py
     self-heal <session_id> <born_session> --name <name> --focus
     <focus>` (values from the existing manifest — no new mint). Stop —
     don't ask, don't re-confirm the title, don't proceed further.
   - **Exists but the title doesn't match** → REFUSE: report which
     workstream this session is already bound to (its manifest's
     `name`/`state`) and point to `workstream:refocus` to rename it —
     never silently re-birth over `spawned_from`/`created` history.
     Stop.
   - **No manifest for this session's uuid** → proceed.

4. **Resolve the target name.** Parse `<name>` as `[<name>] [—
   <one-line focus>]`. Derive a short kebab name from context when none
   is given — a guess is fine, renaming is never locked
   (`workstream:refocus`).

5. **Check for an existing manifest with this NAME** (a different
   `born_session` already using it): scan the state root's manifests
   (`python3 scripts/views.py regen --dry-run` reads them all; or list
   `staff/cos/workstreams/*/workstream.json` and grep each `name`
   field) for a `name` match:
   - **A live session is already bound to it** → STOP, report it — one
     session per workstream.
   - **A manifest with this name exists but no live session** → REFUSE
     (adopt is birth-by-promotion only). Report the existing
     workstream's state; there is no spawn/rebind path any more — if
     the principal insists this really is a lost-session recovery
     case, that is a judgment call outside this skill's scope now
     (rebind retired, L3) — escalate to the principal directly, don't
     invent a write.
   - **No manifest with this name** → proceed to birth.

6. **Birth the manifest** via the primitive's schema-init route:
   `python3 scripts/manifest.py create <this-session-id> --name <name>
   [--focus "<focus>"]` — writes the full canonical-empty shape
   (`spawned_from`/`spawned_from_session: null`, `direct_report: null`,
   `collaborate: []`, `refocused: []`, `previous_names: []`,
   `projects: []`, `maintains: []`, `absorbed: []`; **no `parents`
   key**). Refuses (exit 2) if the manifest already exists — step 3
   already ruled that out for THIS session's uuid, and step 5 for the
   name, so this should never fire; if it does, something raced —
   re-read and report rather than retry blindly.

7. **Set the title.** `set_session_title("self", "ws-<name>")` — just
   `ws-` + the name, no focus suffix.

8. **Write the sidecar.** `python3 scripts/sidecar.py write
   <session_id> <this-session-id> --name <name> --focus "<focus>"`.

9. **Ballast owns `hot.md`/`log.md`/`policy.md`/`index.md` creation for
   the new scope** — not this skill (X7/B2). If the scope isn't picked
   up automatically on the next Ballast checkpoint, that's a Ballast-
   side gap to report, not something to hand-write here.

10. **Regenerate the fleet views.** `python3 scripts/views.py regen`.

11. **Confirm** in one line: the workstream's name, and that it's newly
    born.

## Discipline

`born_session` is the durable binding; the title is a view; the sidecar
is disposable and self-heals. Single-writer-per-manifest: adopt writes
only this session's own manifest, and only through `manifest.py`. Never
overwrites an existing manifest. Never touches a vault project/spine
node. `spawn` and `rebind` no longer exist in this plugin — do not
improvise either; see `docs/migration.md` for the retirement rationale
and the open compat-alias question.
