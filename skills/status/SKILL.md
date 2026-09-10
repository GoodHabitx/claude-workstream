---
name: status
description: Print this workstream's five artifacts exactly as they are on disk — manifest, hot, policy, global-policy, glossary — each with its byte count against its cap, hot.md with its per-slot lengths, then the contents verbatim. Read-only, no summary. Trigger on "workstream status", "show my hot state", "what is in my policy", "how close to the cap". Do NOT use to change any of them (each has its own gated verb) or to roll-call other workstreams (workstream:list).
---

# Workstream status

Shares the shared model: `${CLAUDE_PLUGIN_ROOT}/docs/workstream-model.md`.

> These commands run FROM THE VAULT ROOT: each script reads the vault as `os.getcwd()`, and `${CLAUDE_PLUGIN_ROOT}` resolves to this plugin's own install directory.

Shows what this workstream's continuity files ACTUALLY say — the bytes,
not a reading of them. It exists because every other route to these files
goes through a model that is already carrying the session's assumptions:
after a compaction, "what does my policy say" answered from memory is the
failure mode, and this verb is the one that cannot make it.

Read-only end to end. It never writes, never repairs, and never edits a
scope's caps — a diagnostic that fixes what it is describing cannot be
trusted to describe it.

*(interpreter-shim caveat: on the invocation below, neither `python3` nor
`python` resolves on every host — try `python3`, then `python`, then
`py -3`, then `py`.)*

## `/workstream:status [artifact]`

1. **Resolve this session.** Read the `<!-- workstream-session-id: ... -->`
   line `boot.py` injected this session; that value is the `--session`
   argument. Unbound → the script says so and exits 1; point at
   `/workstream:adopt` (new session) or `/workstream:connect`
   (pre-restart `ws-` session).

2. **Run it, and print what it prints.**
   ```
   python3 ${CLAUDE_PLUGIN_ROOT}/scripts/status.py [artifact] --session <session_id>
   ```
   `artifact` is one of `manifest`, `hot`, `policy`, `global-policy`,
   `glossary`. With no argument all five print, in that order.
   `--born-session <born_session>` targets a workstream directly instead
   of resolving a session, which is how to look at one that is closed.

3. **Relay the output as-is.** Paste it; do not summarize it, re-order
   it, or replace a file's contents with a description of them. If Adam
   asked a narrower question ("how big is my policy?"), the answer is the
   size line from the block — quoted, not paraphrased. Adding a reading
   on top is fine only after the bytes themselves are on screen.

## What the output means

- **`absent`** — the file does not exist. Normal for `glossary.md` and the
  global scope's `_global/policy.md`, which are created only when something
  earns an entry.
- **`disabled`** — the scope's `ballast.json` sets that class to `null`.
  A decision, not an absence.
- **`no ballast.json at this scope`** — nothing has run a SessionStart
  for this workstream yet; ballast's defaults apply and one will be
  written on the next boot.
- **`- OVER by N B`** — arithmetic, not a verdict. A cap is a ceiling on
  what gets INJECTED at boot: over it, Ballast injects a trimmed copy and
  says so loudly, so the file is not broken, it is just not all arriving.
  Two honest answers: shorten the file, or raise the cap in the scope's
  own `ballast.json` (`hot_cap_bytes`, `policy_cap_bytes`,
  `glossary_cap_bytes`). Adam's call, not this skill's.
- **hot.md's per-slot lines** — each slot's length against schema v2's
  own cap for it. Slot caps are read from the vendored `ballast` (resolved
  vendored-first); if no ballast engine resolves the lengths still print
  and the caps read `unknown`.

## Not this skill's job

- Changing any of the five: `policy.md` → `workstream:policy`,
  `_global/policy.md` → `workstream:global-policy`, `glossary.md` →
  `workstream:glossary`, `workstream.json` → `workstream:manifest`.
  `hot.md` is the session's own to refresh, on Ballast's cadence.
- Looking at ANOTHER workstream's live state: this prints files, and a
  closed workstream's files are the whole story, but a live peer's
  current state lives in its own session — `workstream:notify` it.
- Roll-call across sessions (`workstream:list`) or the fleet picture
  (`workstream:graph`).
