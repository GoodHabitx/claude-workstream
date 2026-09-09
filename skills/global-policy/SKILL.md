---
name: global-policy
description: Edit global-policy.md, the ONE rules file every workstream reads at boot, through Ballast's approval gate - show Adam the exact text, say plainly that it is global, wait for his yes, mint, then let the primitive write. Trigger on "add a global rule", "every workstream should...". Do NOT use for one workstream's own rules (that is policy) or a glossary entry.
---
<!-- ballast-template: global-policy v1 -->

# global-policy

Edits `global-policy.md`.

**This file is GLOBAL. It changes the rules for every workstream.** Every
bound session injects it at boot on top of its own `policy.md`, so a line
added here reaches sessions that will never know this conversation
happened. Say that plainly to Adam on every edit — not once, every time.

It is approval-gated exactly like `policy.md`: a direct Write or Edit is
refused by Ballast's PreToolUse gate (`docs/approval-gate.md`).

Ballast never registers this skill; it ships this template and a consumer
vendors it. Everything below the consumer-extras marker at the bottom is
the consumer's own and survives a re-sync.

## Steps

1. **Resolve the global scope.** It is its own scope — a `ballast.json`
   in a directory of its own under the state root, holding
   `global-policy.md`. If it does not exist yet, create it from Ballast's
   `fixtures/scope-global/ballast.json` with an EMPTY `global-policy.md`,
   and say so before writing anything.
2. **Read the whole current file** before proposing a change.
3. **Draft the WHOLE file.** `policy.py set` replaces it entirely. A
   global rule earns its place only if it would prevent a mistake in a
   workstream you are not thinking about right now; anything narrower
   belongs in that workstream's own `policy.md`.
4. **Show Adam the EXACT text, and say it is global.** Print the complete
   new file verbatim, name what changed, and state that this reaches every
   workstream. Do not summarise the text — he is approving the text.
5. **Wait for an explicit yes.** Silence or "sounds good" to something
   else is not a yes.
6. **Mint the approval, then write.** Write the approved text to a temp
   file, then:

   ```
   approve.py mint --scope <path to the global ballast.json> --file <path to global-policy.md>
   policy.py set --scope <path to the global ballast.json> --file global-policy.md --from <path to temp file> --reason "<why>"
   ```

7. **Confirm.** Report the new byte size against `policy_cap_bytes`, the
   log line written, and that the change is now live for every workstream
   from its next boot.

## Not this skill's job

- One workstream's own standing rules: `policy.md`, its own verb.
- A term definition (`glossary.md`), its own verb.
- Wiring the global scope to Stop or PreCompact: SessionStart only — see
  the global-scope recipe in Ballast's `docs/ballast.json.md` for why.

<!-- consumer-extras -->
## Which scope (workstream's own resolution)

The global scope is `<state-root>/_global/`, a sibling of every
workstream's own directory. `<state-root>` is
`workstream_lib.state_root()` - `config.json`'s `state_root`, default
`.vault-meta/workstreams`.

**If `_global/` does not exist, create it before anything else, and say
so.** Two files, no more:

- `_global/ballast.json` - a copy of Ballast's
  `fixtures/scope-global/ballast.json`. It declares `policy:
  "global-policy.md"`, `policy_title: "Global policy"` (so the block
  reads `## Global policy` and cannot be mistaken for a workstream's own
  `## Policy`), `required_slots: []` and no `hot`, so the scope raises no
  Stop-gate nag, and `significant_write_rule: "never"`.
- `_global/global-policy.md` - **empty**. Never seeded with example
  rules: a rule nobody asked for still reaches every session at every
  boot.

This plugin's `hooks.json` already wires the global delivery, on
SessionStart only (`ballast-dispatch.py SessionStart --part policy
--scope-kind global`). It is silent until `_global/ballast.json` exists,
so creating the two files is the whole activation step - no hook edit.
Do NOT wire it to Stop or PreCompact; Ballast's `docs/ballast.json.md`
gives the reasons.

*(interpreter-shim caveat: neither `python3` nor `python` resolves on
every host - try `python3`, then `python`, then `py -3`, then `py`.)*

## Say it out loud, every time

Every bound session in this vault injects `global-policy.md` at boot on
top of its own `policy.md`. A line added here reaches workstreams that
will never know this conversation happened, including ones that do not
exist yet. State that to Adam on every edit - not once, every time - and
show him the whole file, not the diff.
