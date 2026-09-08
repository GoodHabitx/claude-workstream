---
name: glossary
description: Add, edit or delete a term in glossary.md, through Ballast's approval gate - show Adam the exact entry and the naming failure that justifies it, wait for his yes, mint, then let the primitive write. Trigger on "add a glossary entry", "we are using that word two ways". Do NOT use for standing rules (policy), a situational recipe (playbook), or to define a term nothing has actually confused.
---
<!-- ballast-template: glossary v1 -->

# glossary

Edits `glossary.md`: the terms, names and concepts this scope pins so they
stay unambiguous across sessions and compactions. Injected whole at every
boot, so it is a short list, not a lexicon.

Approval-gated: a direct Write or Edit is refused by Ballast's PreToolUse
gate — unless the glossary toggle disarms it
(`docs/approval-gate.md`).

Ballast never registers this skill; it ships this template and a consumer
vendors it. Everything below the consumer-extras marker at the bottom is
the consumer's own and survives a re-sync.

## What earns an entry

An OBSERVED naming failure — a term used two ways, a name that collided, a
new coinage someone will meet cold. Not a definition of something nothing
has confused: the file is injected on every boot, and length costs
reasoning even when every line is true. Apply the per-line test: would
removing this entry cause a mistake?

An entry that revises an older one is welcome — a stale definition
contradicting a newer one is the real failure, not growth.

## Steps

1. **Resolve the scope** and read the current `glossary.md` in full.
2. **Draft the entry** as one line: `- **term** — a one-line gloss`. The
   REASON is not part of the entry; it goes to `log.md`, because a
   definition with an argument attached is two things on one line.
3. **Show Adam the EXACT entry**, plus the observed naming failure that
   justifies it, and — for an edit or a delete — the entry as it stands.
4. **Wait for an explicit yes.**
5. **Mint the approval, then write.**

   ```
   approve.py mint --scope <path to ballast.json> --file <path to glossary.md>
   glossary.py add --scope <path to ballast.json> --term "<term>" --gloss "<gloss>" --reason "<the observed failure>"
   ```

   `edit` takes the same options; `delete` takes `--term` and `--reason`.
   `--reason` is required on every action. The primitive consumes the
   approval, writes the entry, and logs the reason.
6. **Confirm**: report the new byte size against `glossary_cap_bytes` and
   the log line written.

## Not this skill's job

- Standing rules: `policy.md`, its own verb.
- A situational recipe: `playbook.md`, its own verb.
- Automatic pruning by how often a term appears: frequency alone evicts
  exactly the rare proper nouns and one-off coinages a glossary exists to
  protect. Entries come and go by judgment, with a reason.

<!-- consumer-extras -->
## Which scope (workstream's own resolution)

The scope is **this session's own bound workstream**, always. Read the
`<!-- workstream-session-id: ... -->` line `boot.py` injected this
session, then resolve it: `python3 scripts/sidecar.py resolve <session_id>`
prints `{born_session, ws_dir}` and exits 0. Exit 1 means this session is
not bound - refuse and point at `/workstream:adopt`. The scope file is
`<ws_dir>/ballast.json`; `glossary.md` sits beside it. There is no
cross-session target argument.

*(interpreter-shim caveat: neither `python3` nor `python` resolves on
every host - try `python3`, then `python`, then `py -3`, then `py`.)*

**Single-writer.** This verb edits only THIS session's own workstream's
`glossary.md`. To change another workstream's, notify that session
(`workstream:notify`) and let it reconcile there - never a direct edit,
and never a vault project/spine node.

**What belongs here rather than elsewhere.** A term this workstream has
actually seen used two ways - a name that collided, a coinage a later
session will meet cold. Not a rule (that is `policy.md`), not a recipe
(that is `playbook.md`), and not a duty (that is the manifest's
`maintains[]`, through `workstream:manifest`).

`/workstream:status glossary` prints the file and its byte count against
`glossary_cap_bytes` before you add to it.
