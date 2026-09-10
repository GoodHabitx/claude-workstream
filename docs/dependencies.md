# Dependencies + degrade table

Ported from `04h-dependencies.md` and `_decisions.md` (D6, E1-E7, R3),
updated for 0.1.6's vendoring. `ballast` is no longer an installed plugin
dependency: its engine is **VENDORED** into `vendor/ballast/` (see
"Vendoring `ballast`" below), so this plugin is self-contained. The
`.claude-plugin/plugin.json` `dependencies` array now names only the two
soft plugin dependencies - `vault-lock` and `grill`; `session-mgmt` (the
`ccd_session_mgmt` MCP server - not itself a Claude Code plugin) is declared
here for visibility, not in that array.

| dependency | what this plugin uses it for | strength | degrade when absent |
|---|---|---|---|
| `ballast` (VENDORED) | hot/log/policy/glossary/index continuity for every workstream dir (a Ballast scope per bound session), plus the approval gate `manifest.py` reaches through `approve.py` | **vendored (bundled)** | the engine ships INSIDE this plugin under `vendor/ballast/` (byte-identical copies of the ballast repo's runtime scripts + the shim), so it is present by construction - no separate install. `ballast-dispatch.py` and `workstream_lib` resolve that vendored copy FIRST, falling back to a separately-installed `ballast` only if the vendored copy is somehow missing. `adopt_precheck()` still HARD-refuses a manifest mint when NO ballast engine resolves at all (vendored or installed): *"workstream:adopt refuses - no `ballast` engine is available: none is vendored under this plugin's vendor/ballast/, and none is separately installed. ... Re-vendor the engine (sync-templates.py from the ballast repo), or install `ballast`, then retry."* Separately, a write to one of `manifest.py`'s four approval-gated fields (`maintains`, `direct_report`, `collaborate`, `absorbed`) refuses with its own message naming the escape hatch (`ballast-gate.disabled` under the state root); every UNgated field still writes normally, so an existing workstream keeps working. |
| `vault-lock` | the ONE sanctioned cross-manifest write (`manifest.py absorb-close`, AB7) is wrapped in acquire/release when available | soft | `boot.py` prints one NOTE line when `.vault-meta/bin/vault-lock.sh` is not materialized in this vault: *"NOTE (workstream boot): vault-lock is not installed/materialized in this vault - writes are unguarded across sessions this session."* `manifest.py absorb-close` itself still writes (never blocks on the lock's absence - AB1's failure mode was blindness, not a torn write) but returns/prints a warning note: *"absorb-close: no vault-lock available - writing `<path>` UNGUARDED (soft dependency absent)."* |
| `grill` | conflict resolution in absorb (a policy/maintains collision, AB5) and the refocus/connect interviews | soft | the verb runs one inline numbered-question round instead (the fallback contract documented in the `grill` plugin's own README: "if grill is absent, a consumer runs one inline round in the same ❓ Qn - title: body / ➡️ recommendation shape, then proceeds without further grilling"). `workstream_lib.grill_available()` is the detector a verb calls before deciding which path to take. |
| `session-mgmt` (`ccd_session_mgmt` MCP) | `list`'s live-session join, `notify`'s address resolution (R2/R3) | soft | cross-session features (list's liveness column, notify) are unavailable; `list` degrades to a manifest-only listing (state, focus, edges, no isRunning column). Desktop-app-only by design (R3) - always present in Adam's own setup, but a plugin consumer running elsewhere must degrade gracefully rather than assume it. |

## Vendoring `ballast` (0.1.6)

Ballast is a repo-only library, not an installed plugin. Its runtime engine
(`ballast.py`, `ballast_lib.py`, `approve.py`, `policy.py`, `glossary.py`,
`validate-scope.py`), the shim (`shim/ballast-shim.py`), and the gated skill
templates (`policy`, `glossary`, `check`) are copied BYTE-IDENTICAL into
this plugin by the ballast repo's `sync-templates.py`:

```
python3 <ballast-repo>/scripts/sync-templates.py <this-plugin-dir>
```

The engine lands under `vendor/ballast/`; the shim under `shim/`; the
templates under `skills/` (a template's `<!-- consumer-extras -->` tail is
this plugin's own and survives a re-sync). Rules:

- **Never edit the vendored copy.** Fixes go to the ballast repo, then
  re-vendor. `sync-templates.py --check` is the author-side drift lint.
- **Version pin (pip-style).** A scope's `min_engine` is the floor; the
  vendored engine's `ENGINE_VERSION` must satisfy it, or every event no-ops
  (fails open). This plugin updates the ballast version it vendors only when
  that version is known compatible - it does NOT auto-sync to latest.
- **Self-contained.** After install a session runs entirely on the vendored
  engine; a separately-installed `ballast` is only a fallback the resolver
  tries after the vendored copy.

## Detection mechanics (`workstream_lib.py`)

- `ballast_available()` - true when a ballast engine resolves: the copy
  VENDORED under `vendor/ballast/` (checked FIRST), else a separately-
  installed `ballast` found by searching every plausible plugins-root
  (`CLAUDE_PLUGIN_ROOT`'s parent(s), flat dev-clone and versioned-cache
  both tried, then the `installed_plugins.json` registry's conventional
  home). Mirrors `shim/ballast-shim.py`'s own vendored-first resolution, so
  this plugin's degrade checks never disagree with what the shim finds at
  hook time. `grill_available()` still searches only for the installed
  sibling (`grill/skills/grilling/SKILL.md`) - grill is not vendored.
- `ballast_script(name)` - the same vendored-first resolution, returning
  the PATH of `vendor/ballast/<name>` (else an installed
  `ballast/scripts/<name>`) so a caller can run it (`manifest.py`'s gate
  runs `approve.py`; `status.py` imports `ballast_lib` for hot.md's
  per-slot caps). Deliberately without the registry step
  `ballast_available()` ends on: a registry key is a belief, and there is
  nothing to execute at the end of it.
- `vault_lock_available(vault)` - checks the MATERIALIZED path
  `.vault-meta/bin/vault-lock.sh` in THIS vault, not merely whether the
  `vault-lock` plugin is installed somewhere - a fresh vault clone can have
  the plugin installed but not yet have run its own SessionStart boot, and
  what matters to a write this session might make is whether the CLI is
  actually there.
- `session-mgmt` has no detector in this repo (no CLI/file marker to check
  from a script) - a verb that needs it calls the MCP directly and handles
  the tool-not-found case per the harness's own MCP-unavailable contract;
  this is documented here for completeness, not implemented as a Python
  function.

## What a skill (the second builder's job) must do with these

- `adopt` (and `fork`, which also mints a manifest): call
  `workstream_lib.adopt_precheck()` FIRST, before any write; on `(False, message)` print `message` and stop - never mint a manifest without
  Ballast able to keep it current.
- `boot.py`/`remind.py` already call `vault_lock_available()` themselves
  (see `scripts/boot.py`) - no skill action needed for that degrade.
- `absorb` (built by the second builder) should pass its resolved
  `.vault-meta/bin/vault-lock.sh` path (or `None`) into
  `manifest.absorb_close()`'s `vault_lock_sh` parameter; the primitive
  handles the rest (acquire, write, release, degrade note).
- `refocus`/`connect`/`absorb` should call `workstream_lib.grill_available()`
  before invoking `Skill(skill: "grill:grilling")`; on `False`, run one
  inline numbered-question round instead (grill's own documented fallback
  shape).
- `list`/`notify` should attempt the `ccd_session_mgmt` MCP tools and
  catch the tool-unavailable case gracefully, degrading to a manifest-only
  view / a "cross-session messaging unavailable, install the desktop app"
  note respectively.

## Extraction context (why these left `cos`)

Two of these (`vault-lock`, `grill`) used to be fused inside `cos` 0.20.0.
Both are extracted into their OWN plugins tonight (E3, E4 - Adam accepted
2026-09-07 AM) because a hook must register **exactly once** fleet-wide -
Claude Code does not deduplicate identical hook commands across plugins,
so a vendored copy inside `workstream` would mean two guards/interviews
firing per event once `cos` is also still installed - and because both
must keep working after the staff plugins turn off (D5). `workstream`
never vendors either; it depends on them the same way `task-manager`,
`obsidian-tasks-sme`, `vault-engineer`, and `verifier` already do.

Ballast never existed before the 2026-09-07 rebuild (E5) and was first
shipped as its own plugin. In 0.1.6 it became a repo-only library that
consumers VENDOR (see "Vendoring `ballast`" above), so it is no longer an
installed dependency at all - it is bundled. It remains the engine
everything downstream builds continuity on; what changed is that the engine
now travels inside the consumer rather than beside it.

> **Why `session-mgmt` is not in `plugin.json` `dependencies`** (2026-09-07): the harness resolves that array as *plugin* names in the marketplace and errors on an MCP server name (`Dependency "session-mgmt@staff-plugins" is not installed`). The session-mgmt MCP is therefore declared HERE, with its degrade rule, and probed at run time - not in the manifest.
