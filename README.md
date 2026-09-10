# workstream

Gives a Claude Code session a durable name, a job, and relationships that
survive every restart and compaction. Extracted from `cos` 0.20.0's fused
`workstream-*` skills + boot/log/stop/precompact hooks into its own
persona-less plugin (04-workstreams-plugin.md and its component pages,
`_decisions.md` I1-I7/L1-L8/R1-R6/X1-X10/E1-E7).

This repo ships the **core** - identity primitives, lifecycle-writing
primitives, the boot/remind/precompact hooks, the fleet-views generator,
and the Ballast wiring - plus **16 verb skills**: the ones built against
that core (`adopt · fork · refocus · close · absorb · notify · list ·
graph · connect · manifest · sidecar`), the workstream-owned `global-policy`
(which drives Ballast's `policy.py` against the shared `_global/` scope),
the read-only `status`, and the ones vendored from Ballast's own templates
(`policy · glossary · check`). Ballast's engine is **vendored** into
`vendor/ballast/`, so the install is self-contained. See
`docs/dependencies.md` and each script's own module docstring.

## 0.1.6 changes

- **Ballast is vendored - self-contained install.** Ballast is now a
  repo-only library, not an installed plugin dependency. Its engine
  (`ballast.py`, `ballast_lib.py`, `approve.py`, `policy.py`, `glossary.py`,
  `validate-scope.py`) is copied byte-identical into `vendor/ballast/` by
  the ballast repo's `sync-templates.py`, and the shim + `workstream_lib`
  resolve that vendored copy FIRST (a separately-installed `ballast` is only
  a fallback). `"ballast"` is dropped from `plugin.json` `dependencies`;
  `adopt_precheck()` now refuses only when NO engine resolves at all.
- **`global-policy` is workstream's own verb.** Ballast dropped its
  `global-policy` template - "global" is just a second policy scope - so
  this plugin owns the verb, driving the vendored `policy.py` against the
  `_global/` scope. The shared file is renamed `_global/global-policy.md`
  -> `_global/policy.md`.
- **New vendored `check` skill.** A read-only scope health check (vendored
  engine version vs `min_engine`, part sizes, gate, freshness); the vendored
  template set is now `policy · glossary · check`.
- **Re-vendored from Ballast 0.2.0.** shim + engine + the `policy`/
  `glossary` templates re-synced; `sync-templates.py --check` clean.

## 0.1.5 changes

- **A retired verb** (Adam, 2026-09-09). The on-demand, `when:`-matched
  situational-recipe verb and file — Ballast's retired fifth artifact
  class — are removed from this consumer in full: the vendored skill, its
  SessionStart `--part`, its approval-gate entry and its scope keys. The
  gate now covers three files (`policy` / `global-policy` / `glossary`),
  the SessionStart split is three parts plus the global delivery, and
  `/workstream:status` prints five artifacts, not six.
- **Re-vendored from Ballast 0.1.3.** `shim/ballast-shim.py` stays
  byte-identical to Ballast 0.1.3's; the three skill templates and the
  default scope (`fixtures/scope-example/ballast.json`, verbatim) are
  re-synced from it via its own `sync-templates.py`.
- **Broken-manifest boot NOTE aligned** to the flag-once +
  repair-keeping-content pattern: a present-but-broken `workstream.json`
  now names `manifest.py repair`, which moves the bad file aside to a
  `.corrupt-<ts>` backup (content preserved) and rewrites the canonical
  shape; a genuinely absent manifest still points at adopt/fork self-heal.

## 0.1.4 changes

Against Ballast 0.1.2 as shipped:

- **SessionStart splits per part.** `hot`, `policy`, `glossary`
  each ride their own hook command, because the harness caps
  each command's stdout independently at 9,687 B; three parts on one
  command would share one envelope and defeat it. A fourth command
  delivers the **global scope** (`--part policy --scope-kind global`),
  silent until an operator seeds `<state_root>/_global/`.
- **A PreToolUse entry** on `Write|Edit|NotebookEdit`, carrying two
  refusals: `ballast-dispatch.py`'s own (a direct write of any
  `workstream.json` under the state root) and Ballast's approval gate for
  the three gated files. The wrapper now returns the shim's exit code
  VERBATIM - without that, a refusal reads as an allow and the entry is
  wired but inert.
- **The manifest gate covers four fields.** `maintains`, `direct_report`,
  `collaborate` and `absorbed` need a fresh approval; every other field,
  `last_touched` included, is untouched by it. The approval is scoped to
  the manifest FILE, not to one field - Ballast keys a token by target
  path, so one yes authorizes the next gated write to that manifest.
- **Scope defaults + migration.** A new scope is written from Ballast
  0.1.2's `fixtures/scope-example/ballast.json` verbatim; an existing one
  still byte-identical to the 0.1.1 default is replaced with it. A
  customized scope is never touched.
- **Three vendored skills** (`policy`, `global-policy`, `glossary`) and
  the new read-only **`/workstream:status`**.
- **A whole-output bound** on `boot.py`, `remind.py` and `precompact.py`.
- `shim/ballast-shim.py` re-copied byte-identical from Ballast 0.1.2.

## What

- `config.json` - the ONE config value: `state_root` (default
  `.vault-meta/workstreams` - the 42 existing dirs keep working with no move
  tonight).
- `scripts/workstream_lib.py` - shared, import-only module: config/path
  resolution, the session->workstream binding read, the ONE manifest-scan
  function (`discover_manifests`/`find_problems`/`resolve_ref_display`)
  boot/list/graph/connect all share, the three dependency-degrade
  detectors (`ballast_available`, `vault_lock_available`,
  `grill_available`), `ballast_script()` (the path of one of Ballast's own
  scripts, for the callers that must RUN one), and `guard_delivery()`
  (the whole-output bound every hook here writes through).
- `scripts/sidecar.py` - the sidecar primitive (I5, L3): maps a transcript
  id -> `born_session`; writes, resolves, and SELF-HEALS (a missing/stale
  sidecar gets a loud line, never silence).
- `scripts/manifest.py` - the manifest primitive (I3-I5): schema authority
  for `workstream.json`, single-writer-per-manifest, the file-scoped
  approval gate over `maintains`/`direct_report`/`collaborate`/`absorbed`,
  plus the ONE sanctioned cross-manifest write (`absorb-close`,
  AB1-AB7), vault-lock-wrapped when vault-lock is available.
- `scripts/status.py` - the read-only artifact printer behind
  `/workstream:status`: the five files, each with its size against its cap
  (hot.md also per-slot), then the contents verbatim. Never writes,
  never interprets.
- `scripts/boot.py` - `SessionStart` (no matcher - fires on every source
  including compact, V1/X2/X3): the session-id echo line, then (when
  bound) the ~1 KB identity block read straight from the manifest.
- `scripts/remind.py` - `UserPromptSubmit`: re-shows the identity block
  every N=25 turns, deduped via the boot-stamped counter (X4).
- `scripts/precompact.py` - `PreCompact`: a short ~0.3 KB note so identity
  survives the compaction summary itself.
- `scripts/views.py` - regenerates `index.md` (roll-call table) and
  `workstream-graph.md` (mermaid lineage picture) from every manifest
  (V2, V4) - the primitive every lifecycle verb calls after it writes a
  manifest.
- `scripts/ballast-dispatch.py` + `shim/ballast-shim.py` - the Ballast
  wiring (see below).
- `docs/dependencies.md`, `docs/migration.md`, `docs/workstream-model.md`.

No `agents/` directory and no persona fields (`tier`/`model`/`home`) in
the manifest - hooks plus primitives plus 15 skills, contributed the way
`vault-lock`/`grill` are, never a persona a session talks to.

## Why this exists

Bare Claude gives a session two ids and no memory of its job - a rotating
transcript id and a sidebar uuid unreadable without the session-mgmt MCP.
Two sessions in one repo look identical. `cos` 0.20.0 fixed this once, but
fused inside a persona plugin whose own boot payload (22-37 KB across four
workstream blocks) blew past the harness's ~9.7 KB inline cap and only a
~2 KB preview ever landed (defect D1) - the identity block, buried last,
never showed up at all. Extracted now because the staff plugins turn OFF
after the next restart for a rethink (D5): anything workstreams needs from
a staff plugin (the write guard, the interview skill) comes out first.

## How identity works

- **born_session** - the uuid of the session that first adopted this
  workstream; the folder name; never rotates (I1).
- **manifest** (`workstream.json`) - who the workstream is + its edges,
  written only by its own session, only through `manifest.py` (I3).
- **sidecar** - a tiny file mapping THIS transcript id -> born_session;
  disposable, self-heals (I5, L3).
- **title** - the sidebar label `ws-<name>`, a view, never the source of
  truth.

Every SessionStart (startup, resume, fork, **and compact**), `boot.py`
reads the manifest directly and injects the ~1 KB identity block - a
deterministic file read, never a judgment-refreshed summary, so a
malformed manifest gets a loud degraded block, never a silent drop. This
fixes D1 by construction: the identity block is this plugin's OWN hook's
entire output, nowhere near the 9.7 KB cap, not the fourth block inside
someone else's 22-37 KB payload.

## Continuity runs on Ballast's engine, vendored in

`hot.md`/`log.md`/`policy.md`/`glossary.md`/`index.md`
inside each workstream's own dir are a **Ballast scope** (B1-B5), and
`<state_root>/_global/` is a second, shared one every bound session reads
on top of its own. Ballast's engine is **vendored** into `vendor/ballast/`
(byte-identical copies of its runtime scripts, synced by the ballast repo's
`sync-templates.py`), so the install is self-contained; the shim resolves
that vendored copy first, and a separately-installed `ballast` is only a
fallback. Because this plugin owns *many* scopes -
one per bound workstream directory, resolved at runtime from the
session's own sidecar, not one static scope per plugin - it cannot use
Ballast's static per-plugin hook template as-is (Ballast's own docs call
this case out: `docs/consumer-hooks.json.snippet`). `scripts/ballast- dispatch.py` is the small wrapper that resolves the bound scope first,
then runs the byte-identical `shim/ballast-shim.py` with `--scope <ws_dir>/ballast.json` (written from the documented default scope on
first use; an existing one is migrated only when it is still byte-for-byte
the 0.1.1 default, and a customized scope is never overwritten). It
forwards `--part` untouched, resolves `<state_root>/_global/ballast.json`
under `--scope-kind global`, and returns the shim's exit code VERBATIM so
a refusal stays a refusal. Unbound sessions are a near-zero-cost no-op -
no ballast invocation at all - on every event but `PreToolUse`, where a
write TARGETING a path under `<state_root>/` is passed to the gate anyway,
against a scope resolved from the target: the gate protects those files,
not sessions, and unbound is most sessions. Full wiring:
`docs/workstream-model.md`.

## Install

Add the `workstream` entry from this marketplace's
`.claude-plugin/marketplace.json`. Ballast is **VENDORED** (bundled under
`vendor/ballast/` - no separate install; a manifest mint still refuses if
NO engine resolves at all, `workstream_lib.adopt_precheck()`). Declares two
soft plugin dependencies - `vault-lock` (one boot warning) and `grill` (an
inline numbered-question round) - plus `session-mgmt` (soft - cross-session
features off, docs pointer). See `docs/dependencies.md` for the full degrade
table and exact messages.

## Verify

Interpreter-shim caveat, once, for every `python3 ...` invocation on this
page: neither `python3` nor `python` resolves on every host (a Windows
Store alias once silently killed a harness) - this repo's own
`hooks/hooks.json` probes `python3`, `python`, `py -3`, `py` in that order
and uses whichever actually runs; do the same by hand (`py -3` in place of
`python3` is the common Windows substitution below).

`tests/` - plain `unittest`, no pytest, a temp directory as the vault root
throughout (never touches a real vault). Run with:

```
python3 tests/test_workstream_lib.py    # (repeat per test_*.py, or a runner)
```

or on Windows:

```
py -3 tests\test_workstream_lib.py
```

255 tests total, including the crafted-stdin cases for `boot.py`/
`remind.py`/`precompact.py` (32 across the three - see each file's own
module docstring for the enumerated cases), the manifest single-writer +
absorb cross-write (including every mutating primitive's `--dry-run`/
`dry_run` path, spec 4.3), sidecar self-heal, views regeneration, and the
dependency-degrade detectors. Verified green on both WSL (`python3`) and
Windows (`py -3`, including a real cross-platform bash-resolution fix in
`manifest.py`'s vault-lock wrapper - see its module docstring).

To sanity-check the plugin loads at all after install: open a session in a
vault carrying `staff/`, confirm the boot line
`<!-- workstream-session-id: ... -->` appears in context (visible via any
transcript/debug view), then run `python3 scripts/views.py regen` (or
`py -3`) from the vault root and confirm it reports `0 manifest(s)` (or
however many exist) with no traceback.

## Rollback

This plugin makes exactly one class of durable write to the vault: inside
each workstream's own `<state_root>/<born_session>/` directory (the
manifest, sidecar-adjacent counters live under the sessions dir instead)
plus the two regenerated fleet views (`index.md`, `workstream-graph.md`).
To roll back a bad install: disable the plugin in `settings.json`
(`enabledPlugins`) and restart - no hook it ships holds a lock or leaves a
half-written file (every write goes through
`workstream_lib.atomic_write_lf`/`atomic_write_json`, tmp+`os.replace`).
Nothing here deletes vault content, so disabling is always sufficient to
stop it. One data migration DOES run, and 0.1.4 is where it starts: a
bound session's first SessionStart replaces a `ballast.json` still
holding the 0.1.1 default with the 0.1.2 one (a customized scope is never
touched). `docs/migration.md`, section "0.1.4 - the scope-default
migration", names every key that changes, which scopes are eligible, and
the `git checkout` that reverses it. The workstream dirs themselves never
moved.

## Memory home

Persona-less - no `agents/` directory, no home department, no standing
memory of its own. State lives entirely in the vault's own
`<state_root>/` tree (manifests, sidecars, generated views) and in each
workstream's Ballast scope (`hot.md`/`log.md`/`policy.md`/`index.md`),
never in a plugin-side memory file.
