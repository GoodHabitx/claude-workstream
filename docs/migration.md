# Migration: cos 0.20.0 -> the standalone `workstream` plugin

Ported from `04i-shell-migration.md` and `_decisions.md` (E1, E2, V3, V4,
V6). Nothing here has run yet - this build is core scripts + tests only;
the second builder's 12 skills and the eventual `reinstall.sh` cut are
separate steps.

## 1. The config-value move (no file move tonight)

Today (cos 0.20.0): 16 real `os.path.join(vault, "staff", "cos", ...)`
calls + 42 literal `"staff/cos"` strings scattered across
`cos_workstream_lib.py`, `cos-boot.py`, `workstream-index.py`,
`workstream-graph.py`, `workstream-log.py`, `workstream-stop.py`,
`workstream-precompact.py`. No `config.json` at all.

Target (this repo): ONE value, `config.json`'s `state_root` (default
`.vault-meta/workstreams`), read through exactly one helper
(`workstream_lib.state_root()`) everywhere a script needs the state root;
`workstream_lib.sessions_root()` derives the sidecar directory as that
root's own sibling (`workstream-sessions`), never a second config value.

**The 42 existing workstream dirs and 43 sidecars do not move tonight** -
the default `state_root` points at exactly where they already live
(`.vault-meta/workstreams`), so this plugin reads and writes the SAME dirs
cos 0.20.0 used, with zero data migration. A future actual move (e.g. to
`staff/workstream/...` once cos's own directory is retired) becomes a
one-line `config.json` edit plus a single `git mv` of the directory tree -
never a file-by-file rewrite - because every script already goes through
the one config-driven helper.

## 2. Namespace rename (not yet executed - the second builder's skills)

`/cos:workstream` (bare) -> `/workstream:adopt`; `/cos:workstream-fork` ->
`/workstream:fork`; and so on for the other verbs (E1). This repo ships
NO skills yet, so no rename has happened in this repo - it is entirely the
second builder's concern when they author the 12 `skills/<verb>/SKILL.md`
files under this plugin's own namespace (`/workstream:*`, harness-
auto-namespaced, no hand-rolled prefix needed).

903 textual mentions of the old `/cos:workstream*` prefix family were
measured across the vault (2026-09-06) - journals, dated docs, generated
views, manifests/policy prose, auto-memory, daily notes. **None of those
are touched by this build** (or by the eventual skill port) - E1's
decided rule is that journals and dated docs are NEVER rewritten on
rename, at any depth, kept as history regardless of what the live verb
names say. Only forward-looking, non-dated prose (this vault's CLAUDE.md
skills table, live routing docs) needs a pass once the second builder's
skills land and the reinstall/restart actually happens - that pass is out
of THIS build's scope.

## 3. The compat-alias question (flagged for Adam - Q1 below)

`04i` names a **time-boxed compat alias**: the bare `/cos:workstream`
verb keeps answering, aliased to `/workstream:adopt`, until lagging
surfaces (prose, manifests) truth up - retired on that condition, not a
fixed clock. The other ten renamed verbs get no alias at all.

This build does not implement the alias (there is no `cos:workstream`
skill inside THIS plugin to alias from - an alias would have to live
INSIDE `cos` itself, aliasing outward to `workstream:adopt`). **Once cos
is off** (D5, the same restart that ships this plugin), there is no `cos`
process left to host a `cos:workstream` alias skill at all - the alias's
home cannot exist inside a plugin that is turned off in the same event
that requires the alias to still work.

**Question for Adam** (recorded, not answered here - see the top-level
`questions` field of this build's report): does the compat alias:
(a) get dropped entirely, since the restart that ships `workstream` is
the SAME restart that turns `cos` off (E7 - no restart in the middle), so
there is never a moment where `cos:workstream` needs to keep working
while `workstream:adopt` also exists; or
(b) need a tiny separate mechanism (e.g. a one-line note in `cos`'s own
still-installed-but-disabled skill file, or a short-lived note in the
vault's own routing doc) for the narrow window between "the new plugins
are installed" and "everyone has learned the new verb names"?
This build takes no position - 04i's own text calls it a "time-boxed
compat alias" without specifying where it lives once cos turns off in the
same event, and that gap is native to the spec, not something core-only
scripts can resolve on their own.

## 4. Vault-marker check (residual, not the Vault-Lock page's V3 rule)

`workstream_lib.looks_like_vault()` keeps the literal `staff/` directory
marker (same narrowed single-marker class cos-boot.py and
workstream-index.py already use) rather than adopting the Vault-Lock
plugin's own V3 rule (`VAULT_LOCK_VAULT` env var -> walk up to the nearest
`.vault-meta/` dir -> no-op with one notice line). V3 is scoped in the
model pages to vault-lock's OWN root detection for the lock CLI, not
restated anywhere as this plugin's own vault-marker rule. Kept as the
simpler, already-measured-safe check `views.py`'s CLI refusal relies on;
revisit if Adam wants the two plugins' root-detection conventions unified.

## 5. Tests-first (E2) - satisfied by this build

E2's precondition ("tests-first: 16 stdin cases + log/stop/precompact
coverage, config-driven state root, write-lock's home named before the
cut, grill dependency declared") is met here: 101 tests total, 25
crafted-stdin cases across `boot.py`/`remind.py`/`precompact.py` (this
repo's own count against the model page's "16" - no concrete 16-case
enumeration was found anywhere in the source models to reproduce exactly,
so this repo authored its own set and exceeded it; see the top-level
`residuals` field), `config.json`-driven state root throughout, vault-lock
declared as a soft dependency with `.vault-meta/bin/vault-lock.sh`
already the extracted plugin's own stable path, `grill` declared as a
soft dependency with `workstream_lib.grill_available()` as the detector.

## State root moved 2026-09-07

The principal moved the tree: `staff/cos/workstreams` -> `.vault-meta/workstreams` (manifests + hot/log/policy caches + index, tracked in git) and `staff/cos/workstream-sessions` -> `.vault-meta/workstream-sessions` (sidecars, gitignored). `config.json` `state_root` now defaults to `.vault-meta/workstreams`; the sidecar dir stays the sibling `workstream-sessions` of the root's parent. An empty `staff/cos/workstreams/` shell with a README tombstone may linger until the process holding it lets go.
