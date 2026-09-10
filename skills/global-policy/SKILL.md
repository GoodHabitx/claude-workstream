---
name: global-policy
description: Edit this vault's global policy - the ONE rules file every bound workstream reads at boot, on top of its own policy.md - through Ballast's approval gate. Show the principal the exact text, say plainly that it is global, wait for a yes, mint, then let the vendored primitive write. Trigger on "add a global rule", "every workstream should...". Do NOT use for one workstream's own rules (that is policy) or a glossary entry.
---

# global-policy

Workstream's own verb. There is no `global-policy` mechanism in Ballast:
"global" is just a second policy scope, and this verb is workstream's
naming of it. It edits the shared policy every bound session reads at boot
by driving Ballast's `policy.py` primitive - vendored under
`vendor/ballast/` - against a scope of its own, the `_global/` scope,
instead of this session's own workstream directory.

**This file is GLOBAL. It changes the rules for every workstream.** Every
bound session injects it at boot on top of its own `policy.md`, so a line
added here reaches sessions that will never know this conversation
happened. Say that plainly to the principal on every edit - not once,
every time.

It is approval-gated exactly like a workstream's own `policy.md`: a direct
Write or Edit to the global scope's `policy.md` is refused by Ballast's
PreToolUse gate (`ballast-dispatch.py` resolves the gate against any scope
under the state root).

(interpreter-shim caveat: neither `python3` nor `python` resolves on every
host - try `python3`, then `python`, then `py -3`, then `py`.)

## The global scope

The global scope is `<state-root>/_global/`, a sibling of every
workstream's own directory. `<state-root>` is
`workstream_lib.state_root()` - `config.json`'s `state_root`, default
`.vault-meta/workstreams`.

**If `_global/` does not exist, create it before anything else, and say
so.** ONE file activates the scope:

- `_global/ballast.json` - the global-scope recipe: it declares
  `"policy": "policy.md"`, `"policy_title": "Global policy"` (so the block
  reads `## Global policy` and cannot be mistaken for a workstream's own
  `## Policy`), `"required_slots": []` and no `hot`, so the scope raises no
  Stop-gate nag, and `"significant_write_rule": "never"`:

  ```json
  {
    "root": ".",
    "log": "log.md",
    "index": "index.md",
    "policy": "policy.md",
    "policy_title": "Global policy",
    "glossary": null,
    "regen": null,
    "policy_cap_bytes": 7168,
    "required_slots": [],
    "significant_write_rule": "never",
    "reground_interval_turns": 25,
    "min_engine": "0.1.0"
  }
  ```

**Do NOT pre-create `_global/policy.md`.** It comes into being only when
the first global rule is written (step 6's `policy.py set` creates it).
Until then there is no file - an empty policy file is a file nobody asked
for, and the engine injects nothing for an absent OR empty policy, so a
global scope with no rules yet adds not one byte to any session's boot.

This plugin's `hooks.json` already wires the global delivery, on
SessionStart only (`ballast-dispatch.py SessionStart --part policy
--scope-kind global`). It is silent until `_global/ballast.json` exists,
so creating `ballast.json` is the whole activation step - no hook edit.
Do NOT wire it to Stop or PreCompact; a global scope injects, it does not
gate.

## Steps

1. **Resolve the global scope** (`<state-root>/_global/`), creating
   `_global/ballast.json` if it does not exist yet - and say so before
   writing it. Do NOT create `policy.md` here; step 6 writes it.
2. **Read the whole current `_global/policy.md`** before proposing a change
   - it may not exist yet (no rules set), in which case you are writing the
   first one and there is nothing to read.
3. **Draft the WHOLE file.** `policy.py set` replaces it entirely. A global
   rule earns its place only if it would prevent a mistake in a workstream
   you are not thinking about right now; anything narrower belongs in that
   workstream's own `policy.md`.
4. **Show the principal the EXACT text, and say it is global.** Print the
   complete new file verbatim, name what changed, and state that this
   reaches every workstream. Do not summarise it - they are approving the
   text, not a description of it.
5. **Wait for an explicit yes.** Silence, or "sounds good" to something
   else, is not a yes.
6. **Mint the approval, then write** (the primitives are the vendored
   copies under `${CLAUDE_PLUGIN_ROOT}/vendor/ballast/`). Write the approved
   text to a temp file, then:

   ```
   ${CLAUDE_PLUGIN_ROOT}/vendor/ballast/approve.py mint --scope <path to _global/ballast.json> --file <path to _global/policy.md>
   ${CLAUDE_PLUGIN_ROOT}/vendor/ballast/policy.py set --scope <path to _global/ballast.json> --from <path to temp file> --reason "<why>"
   ```

   (`--file` is omitted: the scope declares `policy.md`, and `policy.py`
   writes the scope's own declared policy file.)
7. **Confirm.** Report the new byte size against `policy_cap_bytes`, the log
   line written, and that the change is live for every workstream from its
   next boot.

## Not this skill's job

- One workstream's own standing rules: `policy.md`, its own verb.
- A term definition (`glossary.md`), its own verb.
- Wiring the global scope to Stop or PreCompact: SessionStart only.

## Say it out loud, every time

Every bound session in this vault injects the global `policy.md` at boot on
top of its own `policy.md`. A line added here reaches workstreams that will
never know this conversation happened, including ones that do not exist
yet. State that to the principal on every edit - not once, every time - and
show them the whole file, not the diff.
