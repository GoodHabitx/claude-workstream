---
name: policy
description: Add, edit, or remove a workstream's standing policy in policy.md's Policies section (rules only — Maintains lives in the manifest now, not here). Edits ONLY this session's own bound workstream's policy.md (single-writer); bare invocation shows it read-only. Trigger on "add a policy", "this workstream should…", "workstream policy", "/workstream:policy". Do NOT use for a maintains/ownership declaration (workstream:manifest — maintains[] lives on the manifest), to adopt (workstream:adopt), rename (workstream:refocus), or close a workstream.
---

# Workstream policy

Shares the shared model: `${CLAUDE_PLUGIN_ROOT}/docs/workstream-model.md`.

Maintain a workstream's durable rules layer — `<ws_dir>/policy.md`,
one of Ballast's continuity files beside `hot.md` (recent memory) and
`log.md` (append-only trail). Rules only: `## Maintains` is **retired
from this file** (I6) — a duty list is data, so it moved to the
manifest's `maintains[]` field, written via `workstream:manifest`
("maintains[] — add/remove"), never here. Run this **in the session**
whose policy you're changing. Never touches a vault project/spine node.

## `/workstream:policy [natural-language change]`

1. **Target = THIS session's bound workstream, always.** Resolve
   exactly as `workstream:manifest` does: read the `<!--
   workstream-session-id: ... -->` line, `python3 scripts/sidecar.py
   resolve <session_id>` → `{born_session, ws_dir}`. Exit 1 → refuse,
   point to `/workstream:adopt`. No cross-session target argument.

2. **Single-writer, extended to `policy.md`.** Edits ONLY this
   session's own workstream's `policy.md`. To change another
   workstream's policy: notify that session (`workstream:notify`) and
   let it reconcile there — never a direct edit.

3. **Bare invocation is read-only.** Read and show `<ws_dir>/policy.md`
   verbatim, or report none exists yet. Stop — no write, no creation.

4. **For a real change, create `policy.md` first if absent** — always
   under the `ws_dir` step 1 resolved:
   ```markdown
   # <workstream-name> — policy

   ## Policies
   ```
   (No `## Maintains` header — target schema drops it from this file.
   A pre-existing `policy.md` from before this cutover may still carry a
   populated `## Maintains` section: leave it as-is on a `## Policies`-only
   edit, but if the ask concerns a maintains item, redirect it to
   `workstream:manifest` rather than editing the stale section here —
   don't grow a second copy of the duty list in two places.)

5. **Parse the change and apply it under `## Policies`** — a strategy,
   behavior, or standing directive ("always do X", "prefer Y over Z",
   "never do W without asking"). Add, edit, or remove a line: a bare
   new statement appends; "stop doing X" removes the matching line; a
   rephrase of an existing line modifies it in place.

   If the ask reads as an ownership/maintenance statement instead
   ("maintain X", "I own Y", a bare wikilink/absolute path with no
   other framing) — that's `maintains[]`, not a policy: redirect to
   `workstream:manifest`, don't write it here.

6. **Write via Write/Edit — never a Bash append that doesn't name the
   `born_session` uuid dir directly** (a shell-variable-indirected
   append gets misread by the log/staleness machinery — same reasoning
   as the manifest primitive's own write rule).

7. **Confirm** in one line: what was added, removed, or changed.

## Discipline

`policy.md` is injected whole (≤7 KB), approval-gated, into this
workstream's bound sessions on the same cadence as `hot.md` (Ballast's
job, not this skill's). A session writes only its OWN workstream's
`policy.md` (single-writer); it never edits another workstream's
directly, only via `notify`. Rules only — no duty list; `maintains[]`
lives exclusively on the manifest (I6), written only through
`workstream:manifest`. Never touches a vault project/spine node.
