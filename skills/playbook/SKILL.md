---
name: playbook
description: Add, edit or delete a situational recipe in playbook.md, through Ballast's approval gate - show Adam the exact entry and its when triggers, wait for his yes, mint, then let the primitive write. Trigger on "add a playbook entry", "next time this happens, do X". Do NOT use for standing rules (policy), a term definition (glossary), or a recipe for something the code already enforces.
---
<!-- ballast-template: playbook v1 -->

# playbook

Edits `playbook.md`: situational recipes, each gated by a `when:` trigger
so only the matched ones are injected. It is the one artifact allowed to
grow, because matching keeps it on-demand rather than always-loaded.

Approval-gated: a direct Write or Edit is refused by Ballast's PreToolUse
gate (`docs/approval-gate.md`).

Ballast never registers this skill; it ships this template and a consumer
vendors it. Everything below the consumer-extras marker at the bottom is
the consumer's own and survives a re-sync.

## What earns an entry

An entry is a NON-MECHANIZED situational recipe — something a session has
to remember to do, that nothing checks. Never write an entry for something
the code already enforces: the Stop gate, the approval gates and the
compact-time refresh are deterministic mechanisms, and a recipe reminding
a session about one only spends the injection envelope.

## Steps

1. **Resolve the scope** and read the current `playbook.md` in full.
2. **Draft the entry**: a title, its `when:` triggers, and the recipe
   body. A trigger is either a hook-event name (`sessionstart`,
   `userpromptsubmit`, `posttooluse`, `precompact`, `stop`, `compact`) or
   a keyword matched case-insensitively against `hot.md`'s `focus`,
   `next`, `blocked` and `mode`. Pick triggers that will actually appear
   in those slots — a trigger that never matches is an entry that never
   fires, and one with no trigger at all is a lint error.
3. **Show Adam the EXACT entry** — title, `when:` line and body,
   verbatim — plus, for an edit or a delete, the entry as it stands now.
4. **Wait for an explicit yes.**
5. **Mint the approval, then write.** Put the body in a temp file, then:

   ```
   approve.py mint --scope <path to ballast.json> --file <path to playbook.md>
   playbook.py add --scope <path to ballast.json> --title "<title>" --when "<triggers>" --body-from <path to temp file> --reason "<why>"
   ```

   `edit` takes the same options and addresses the entry by its exact
   title; `delete` takes `--title` alone. The primitive validates `when:`,
   consumes the approval, and appends one line to `log.md`.
6. **Confirm**: report which entries now match the current `hot.md` and
   the total injected size against `playbook_inject_cap_bytes`.

## Not this skill's job

- Standing rules that apply always: `policy.md`, its own verb.
- A term definition: `glossary.md`, its own verb.
- Reminding a session of a mechanism the code enforces — see above.

<!-- consumer-extras -->

> These commands run FROM THE VAULT ROOT: each script reads the vault as `os.getcwd()`, and `${CLAUDE_PLUGIN_ROOT}` resolves to this plugin's own install directory.

## Which scope (workstream's own resolution)

The scope is **this session's own bound workstream**, always. Read the
`<!-- workstream-session-id: ... -->` line `boot.py` injected this
session, then resolve it: `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/sidecar.py resolve <session_id>`
prints `{born_session, ws_dir}` and exits 0. Exit 1 means this session is
not bound - refuse and point at `/workstream:adopt`. The scope file is
`<ws_dir>/ballast.json`; `playbook.md` sits beside it. There is no
cross-session target argument.

*(interpreter-shim caveat: neither `python3` nor `python` resolves on
every host - try `python3`, then `python`, then `py -3`, then `py`.)*

**Single-writer.** This verb edits only THIS session's own workstream's
`playbook.md`. To change another workstream's, notify that session
(`workstream:notify`) and let it reconcile there - never a direct edit,
and never a vault project/spine node.

**Triggers that actually fire here.** Keyword triggers match against
this workstream's own `hot.md` slots (`focus`, `next`, `blocked`,
`mode`), so pick words that appear in THIS workstream's state, not
generic ones. Event triggers (`sessionstart`, `userpromptsubmit`,
`posttooluse`, `precompact`, `stop`, `compact`) fire on the event and
are never matched as keywords.

`/workstream:status playbook` prints the file and its caps, which is the
quickest way to see what is already there before adding to it.
