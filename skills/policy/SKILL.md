---
name: policy
description: Edit this workstream's standing rules in policy.md, through Ballast's approval gate - show Adam the exact text, wait for his yes, mint the approval, then let the primitive write. Trigger on "add a policy", "change the standing rules", "this workstream should always...". Do NOT use for the shared rules every workstream reads (that is global-policy) or for a glossary entry.
---
<!-- ballast-template: policy v1 -->

# policy

Edits `policy.md`: the standing rules THIS scope's sessions read at every
boot. A rule here changes how every later session behaves, so it is
approval-gated — a direct Write or Edit to the file is refused by
Ballast's PreToolUse gate (`docs/approval-gate.md`).

Ballast never registers this skill; it ships this template and a consumer
vendors it. Everything below the consumer-extras marker at the bottom is
the consumer's own and survives a re-sync.

## Steps

1. **Resolve the scope.** Find this session's `ballast.json`. Read the
   current `policy.md` in full before proposing any change — you are
   replacing the whole file, not appending to it.
2. **Draft the WHOLE file.** `policy.py set` replaces `policy.md` entirely:
   the cadence for standing text is a periodic full re-statement, not a
   stream of small edits. Apply the per-line test to every line you keep —
   "would removing this line cause a mistake?" — and drop the ones that
   fail it. This file is injected at every boot; length costs reasoning.
3. **Show Adam the EXACT text.** Print the complete new `policy.md`,
   verbatim, in the chat. Name what changed and what was dropped. Do not
   summarise it — he is approving the text, not a description of it.
4. **Wait for an explicit yes.** Silence, "sounds good", or a reply to a
   different question is not a yes. If he asks for changes, go back to
   step 3 with the revised full text.
5. **Mint the approval, then write.** Write the approved text to a temp
   file, then:

   ```
   approve.py mint --scope <path to ballast.json> --file <path to policy.md>
   policy.py set --scope <path to ballast.json> --from <path to temp file> --reason "<why>"
   ```

   The approval is one-time and expires in ten minutes; the primitive
   consumes it and appends one line to `log.md`.
6. **Confirm.** Report the new byte size against `policy_cap_bytes` and
   the log line that was written.

## Not this skill's job

- The shared rules EVERY workstream reads: that is `global-policy.md`, a
  different file with a different scope and its own verb.
- A term definition (`glossary.md`) — its own gated verb and its own
  primitive.
- Writing `policy.md` by hand: the gate refuses it, and that is the point.

<!-- consumer-extras -->

> These commands run FROM THE VAULT ROOT: each script reads the vault as `os.getcwd()`, and `${CLAUDE_PLUGIN_ROOT}` resolves to this plugin's own install directory.

## Which scope (workstream's own resolution)

The scope is **this session's own bound workstream**, always. Read the
`<!-- workstream-session-id: ... -->` line `boot.py` injected this
session, then resolve it: `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/sidecar.py resolve <session_id>`
prints `{born_session, ws_dir}` and exits 0. Exit 1 means this session is
not bound - refuse and point at `/workstream:adopt`. The scope file is
`<ws_dir>/ballast.json`; `policy.md` sits beside it. There is no
cross-session target argument.

*(interpreter-shim caveat: neither `python3` nor `python` resolves on
every host - try `python3`, then `python`, then `py -3`, then `py`.)*

**Single-writer.** This verb edits only THIS session's own workstream's
`policy.md`. To change another workstream's, notify that session
(`workstream:notify`) and let it reconcile there - never a direct edit,
and never a vault project/spine node.

**No `## Maintains` section.** A duty list is data, so it lives on the
manifest's `maintains[]` field, written only through
`workstream:manifest` (which gates it). A `policy.md` written before
that cutover may still carry a populated `## Maintains`: leave it as it
stands on a rules-only edit, and redirect any maintains ask to
`workstream:manifest` rather than growing a second copy of the duty list
in two places.
