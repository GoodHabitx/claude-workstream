---
name: notify
description: Hand a message to another workstream session — resolve the target ws-<name> (or legacy cos:<name>) session via list_sessions, take its MCP id, and send_message (arrives labelled "From <this title>", auto-processed by the receiver). Never gates on isRunning (unreliable). Trigger on "notify <workstream>", "message the <workstream> session", "tell <workstream> ...", "ping the other session". Do NOT use to adopt (workstream:adopt), roll-call (workstream:list), or reconcile forks (workstream:connect).
---

# Workstream notify

Shares the shared model: `${CLAUDE_PLUGIN_ROOT}/docs/workstream-model.md`.

Hand a message to another workstream session. Never reads or writes a
manifest or a vault node — it only sends a message. Degrades gracefully
if the `ccd_session_mgmt` MCP is unavailable (soft dependency, R3/R6 in
`docs/dependencies.md`).

## `notify <name> <message>`

1. **Resolve the target.** Call `mcp__ccd_session_mgmt__list_sessions`
   and find the live session titled `^ws-<name>` (or legacy `^cos:<name>`)
   — take **that session's MCP id** (`local_...` prefix), never a
   transcript id or a bare uuid pasted from a doc/digest/old
   transcript. **A raw id copied from anywhere is never an address** —
   this list-join must run fresh every time (standing check S1 — a
   published close-digest once pointed at a transcript id instead and
   `send_message` returned "Session not found"). No match → say so and
   stop (there is no `spawn` any more to offer as a fallback — point to
   `workstream:list` to check the roster, or `workstream:adopt` if the
   workstream genuinely has no bound session yet).
   **MCP unavailable** (tool-not-found) → degrade: report "cross-session
   messaging unavailable — the session-mgmt MCP isn't reachable
   (desktop-app-only by design); install/open the desktop app to notify
   `<name>`" and stop.

2. **Do not gate on `isRunning`.** It is measured unreliable — a send
   has delivered to, and round-tripped from, a session the index
   reported `isRunning: false` (verified 2026-08-25). Attempt the send
   regardless and let `send_message` report a genuine failure.

3. `mcp__ccd_session_mgmt__send_message(session_id=<target MCP id>,
   message=<message>)`. Arrives in the target as a user turn labelled
   "From `<this title>`" with a link back; the receiver auto-processes
   it. Cannot reach truly unattended sessions (scheduled-task/remote
   runs) — if refused for that reason, report it plainly.

4. **Graceful degradation — the send-fail-to-closed contract.** When
   this notify is a **reconciliation step** — a fork's parent-notify, a
   re-home, a `collaborate` reciprocity mirror, a `connect` fix — and
   the target is closed/unreachable/the send is refused: the caller
   **WARNS and PROCEEDS; never hard-fails its own operation.** The
   caller's own manifest change already stands (single-writer);
   `workstream:connect`'s audit reconciles the undelivered side later.
   A closed target that must change NOW, not eventually, is the
   vault-lock path, not notify.

5. **Confirm** what was sent and to which workstream. notify never
   edits any manifest or cache.

## Discipline

notify only sends a message — it writes nothing. It never edits another
workstream's manifest (single-writer-per-manifest); to change content
elsewhere, notify the owner and let it reconcile. Never fabricate
delivery; report exactly what `send_message` returned. Always address
the target by its `local_`-prefixed MCP id, never a bare transcript
uuid. A reconciliation caller treats a failed/refused send as
warn-and-proceed, never a hard-fail (step 4) — the local write stands
and the audit reconciles the far side.
