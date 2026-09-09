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

## 0.1.3 (2026-09-07) - four fixes

- **`ballast_available()` found ballast even without `CLAUDE_PLUGIN_ROOT`.**
  The first post-restart `/workstream:fork` had its `adopt_precheck()`
  refuse to mint even though ballast 0.1.0 was installed and its hooks
  were running - the check ran from a skill's shell, not a hook, so
  `CLAUDE_PLUGIN_ROOT` was unset and the old single-path HOME fallback
  looked for `cache/ballast/...` when the real installed-cache shape
  nests a plugin under its marketplace name (`cache/<marketplace>/ ballast/<semver>/scripts/ballast.py`). Fixed with a three-step fallback
  in `scripts/workstream_lib.py`: (1) the existing CLAUDE_PLUGIN_ROOT
  sibling lookup, unchanged; (2) a marketplace-glob scan of
  `~/.claude/plugins/cache/*/ballast/*/scripts/ballast.py`; (3)
  `~/.claude/plugins/installed_plugins.json` carrying a `ballast@...`
  key, as a last, weakest signal. True if any step succeeds.
- **`boot.py`'s SessionStart was fully silent for an unbound session.**
  Past its own session-id echo line, a session with no sidecar got
  nothing - no NOTE, no hint that `/workstream:connect` or
  `/workstream:adopt` existed. This is the same "if-absent-silent"
  discipline ported from `cos-boot.py`, but it violates this plugin's own
  I5 ("never silent"). Now emits exactly one short NOTE (under 200
  bytes) naming both verbs; the ~1 KB identity block and the
  session-id echo line are unaffected.
- **`boot.py`'s identity block also silently dropped a MALFORMED
  `direct_report`** - the same silent-failure class as the fix above, one
  level in: a bound session whose `direct_report` is a bare string (not
  `{name,session}|null`) rendered "reports to: nobody - a root" with no
  hint anything was wrong, since `normalize_direct_report()` just returns
  `None` for a non-dict value. `render_identity_block` now runs the
  manifest through the shared `workstream_lib.find_problems()` checker
  (the same one `views.py`'s `regenerate()` calls, V4) and appends one
  short NOTE line naming the schema problem when it finds one -
  restricted to the shape-only problem kinds (invalid state, malformed
  direct_report/collaborate/absorbed, legacy fields) so it never
  false-positives on an ordinary cross-reference to a sibling workstream.
- **`manifest.py`'s module docstring claimed a phantom top-level `scope`
  field** ("scope (NEW - the ballast scope declaration, X10)") that
  neither `create_manifest()` nor `SCHEMA_FIELDS` ever implemented - the
  ratified model has no such field; `scope` only exists as a per-peer key
  inside `collaborate[]`. Doc-only fix; `docs/workstream-model.md` carried
  the same phantom "gains ...scope" wording and is corrected too.

## 0.1.4 - the scope-default migration (the one thing to undo)

This IS a data migration, unlike everything above it on this page: 0.1.4
rewrites `ballast.json` files that already exist in the vault. It is
named here because the Rollback section of the README sends you here to
find out what a rollback has to reverse.

**What runs.** `ensure_scope()` in `scripts/ballast-dispatch.py`, on the
SessionStart of each BOUND session, for that session's own scope only -
lazily, one directory at a time, never a sweep. A workstream whose
session never boots again is never touched.

**Which scopes are eligible.** Exactly those whose `ballast.json` is
byte-identical to the 0.1.1 default this wrapper itself used to write
(held verbatim as `OLD_DEFAULT_SCOPE_TEXT` in that file, CRLF-normalized
before comparison). Those bytes mean the file was written by the wrapper
and never edited by anyone, so replacing them loses no choice a person
made. **Anything else - a raised cap, a dropped file class, a
hand-written scope - is left untouched**, and `/workstream:status` prints
its caps so the owner can see what it is still running on.

**What changes**, old -> new (ballast 0.1.3's
`fixtures/scope-example/ballast.json`, copied verbatim):

| key | 0.1.1 default | new default |
|---|---|---|
| `hot_cap_bytes` | 1024 | 4096 |
| `glossary` | absent | `glossary.md` |
| `glossary_cap_bytes` | absent | 4096 |

Everything else (`root`, `log`, `hot`, `index`, `policy`, `regen`,
`policy_cap_bytes`, the nine `required_slots`, `significant_write_rule`,
`reground_interval_turns`, `min_engine`) is unchanged. A scope with no
`ballast.json` at all gets the same new default written fresh - a
creation, not a migration.

**Measured against the live state root, 2026-09-08** (43 workstream
dirs): 23 hold the 0.1.1 default byte-for-byte and will be REWRITTEN, 20
have no `ballast.json` and will have one CREATED, 0 are customized. So
the first post-install boots touch 23 existing files.

**The undo.** Each write is atomic (tmp + `os.replace`), and the state
root is git-tracked and auto-committed, so the previous bytes are in
history:

```
git -C <vault> log --oneline -- .vault-meta/workstreams/*/ballast.json
git -C <vault> checkout <commit-before> -- .vault-meta/workstreams/<born_session>/ballast.json
```

Disabling the plugin stops further rewrites, but does not restore the 23
already replaced - that is what the `git checkout` above is for. Nothing
else this plugin writes needs reversing.
