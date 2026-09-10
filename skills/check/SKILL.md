---
name: check
description: Read-only health check for one Ballast scope this consumer owns - validates ballast.json, reports the vendored engine version against the scope's min_engine, projects each SessionStart part against the inline ceiling, dry-runs the hook events, reports the gate and freshness, and whether a Stop would pass. Trigger on "check this scope", "is my scope valid", "why is ballast blocking my stop". Do NOT use to write or repair hot/log/policy (the consumer's own verbs), or to lint template drift (author-side).
---
<!-- ballast-template: check v1 -->

# check

Read-only. Runs the vendored engine only in `--dry-run` / validate mode;
never appends to a log, rewrites a hot block, edits a policy, or mints or
consumes an approval. If the scope is unhealthy, this skill SAYS what is
wrong and which of this consumer's own verbs must fix it — it does not fix
it.

Ballast never registers this skill; it ships this template and a consumer
vendors it, alongside the engine under `vendor/ballast/`. Everything below
the consumer-extras marker at the bottom is the consumer's own and
survives a re-sync.

(interpreter-shim caveat: neither `python3` nor `python` resolves on every
host — on Windows Git Bash use the same loop the hooks use:
`for c in python3 python "py -3" py; do ...; done`.)

## Input

`check [path-to-ballast.json]` — the path defaults to `./ballast.json` in
the current working directory. The engine and its validator are the copies
this consumer vendors under `${CLAUDE_PLUGIN_ROOT}/vendor/ballast/`; this
check needs no ballast source checkout.

## Steps

1. **Run the report.** One command does the deterministic work:
   `python3 "${CLAUDE_PLUGIN_ROOT}/vendor/ballast/validate-scope.py" <path> --consumer "${CLAUDE_PLUGIN_ROOT}"`.
   Report its verdict verbatim (OK / WARN / FAIL + the field it names). A
   FAIL ends the check here: every later step assumes a loadable scope. It
   writes nothing. Its sections are:
   - **engine** — the vendored engine's version against the scope's
     `min_engine`. STALE means this consumer's vendored engine is older
     than the scope was pinned to, so every event fails open and no-ops
     until the consumer re-vendors a compatible ballast release.
   - **parts** — the projected inline bytes of each SessionStart delivery
     (`hot`, `policy`, `glossary`, and combined `all`) against the
     per-command ceiling, worst case. A part marked TRIMMED is losing
     content, and the line names WHICH budget cut it — the delivery guard,
     a per-artifact cap, or a per-slot cap. The three call for different
     fixes.
   - **gate** — whether the approval gate is armed, how many tokens are
     outstanding, and which switch files are present.
   - **gate wiring** — whether this consumer's `hooks/hooks.json` wires the
     gate's `PreToolUse` entry at all (an armed gate nothing runs refuses
     nothing) and whether the vendored `shim/ballast-shim.py` still carries
     `REFUSAL_EXIT` (a shim from before the exit-2 forwarding turns every
     refusal into an allow).
   - **templates** — SKIPPED here: the vendored-copy drift lint compares
     against the ballast source, so it is an author-side check run from the
     ballast repo, not part of this consumer check.
2. **Dry-run the six events.** The report covers SessionStart's parts; the
   other events need a run each. For `UserPromptSubmit`, `PreToolUse`,
   `PostToolUse`, `PreCompact` and `Stop` run
   `python3 "${CLAUDE_PLUGIN_ROOT}/vendor/ballast/ballast.py" <EVENT> --scope <path> --dry-run`
   with an empty JSON object (`{}`) on stdin, and capture stdout and
   stderr. `--dry-run` guarantees no file under the scope's root changes.
3. **Freshness + completion.** From the Stop dry-run output, report whether
   the freshness gate would pass (hot's `updated` slot versus the newest
   log entry) and whether the completion check would pass (every
   `required_slots` entry non-empty — the documented convention is the
   literal `none` for a field legitimately at rest). A scope with
   `required_slots: []` and no `hot.md` is not broken: it gates nothing by
   design.
4. **Report** in one short table: field → status → what to do. Name this
   consumer's own verb for any fix; never perform it.

## Not this skill's job

- Refreshing a stale hot block or appending to the log: this consumer's
  own verbs, which call the vendored `ballast.py` WITHOUT `--dry-run`.
- Editing `policy.md` or `glossary.md`: approval-gated by design. The
  sanctioned writers are the vendored `policy.py` and `glossary.py`, each
  driven by a skill template that shows the exact text and waits for a yes.
  This check REPORTS the gate's state; it never mints, consumes, or writes.
- Linting vendored-template drift against the ballast source: author-side —
  run `sync-templates.py --check` from the ballast repo.

<!-- consumer-extras -->
## This session's own scope (workstream)

For a bound session, the scope to check is this session's own
`<state-root>/<born_session>/ballast.json` - resolve `<born_session>` from
the sidecar (the id `boot.py` echoed as `workstream-session-id`), or read
`/workstream:status`, which prints it. `<state-root>` is
`workstream_lib.state_root()` (default `.vault-meta/workstreams`).

To check the shared global scope instead, point `check` at
`<state-root>/_global/ballast.json`.

The engine and validator this skill runs are the copies this plugin vendors
under `${CLAUDE_PLUGIN_ROOT}/vendor/ballast/`; no separately-installed
`ballast` is required.
