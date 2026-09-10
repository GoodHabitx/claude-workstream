#!/usr/bin/env python3
"""workstream_lib.py — shared, import-only module for the workstream plugin.

Never itself a hook, never invoked directly. Every hook/CLI script in this
plugin does:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import workstream_lib as wslib
before use (same convention cos_workstream_lib.py established).

Model source: .vault-meta/workstreams/74a8ddcb.../models/src/04*.md +
_decisions.md (I1-I7, L1-L8, R1-R6, X1-X10, E1-E7). Ported from cos 0.20.0's
cos_workstream_lib.py, cos-boot.py's workstream-binding helpers, and
workstream-index.py's manifest-scan/anomaly logic - config-driven, no
"staff/cos" literal path joins (04i: one config value for the state root).

Vault root convention: always os.getcwd() (spec 3.5), same single notion of
"the vault" every hook in this ecosystem holds - never a second path guess.
"""
import json
import os
import re
import sys

SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
VALID_STATES = ("active", "closed", "absorbed")

# The one positive assertion this plugin treats as "looks like a managed
# vault" - same narrowed single-marker class cos-boot.py/workstream-index.py
# use (2026-08-17 shakedown). Residual: the Vault-Lock page (05, V3) replaces
# ITS OWN root detection with an env-var + walk-up-to-.vault-meta rule; that
# decision is scoped to vault-lock's root-finding, not restated here for this
# plugin's own vault-marker check, so this stays the literal staff/ marker
# until Adam says otherwise (see workstream/docs/migration.md).
VAULT_MARKERS = ("staff",)

WS_BINDING_CAP = 8192       # byte cap on a sidecar file we will read
MANIFEST_CAP = WS_BINDING_CAP * 4   # byte cap on a workstream.json we will read

DEFAULT_CONFIG = {"state_root": ".vault-meta/workstreams"}


# --------------------------------------------------------------------------
# plugin root / config
# --------------------------------------------------------------------------

def plugin_root():
    """This plugin's own install root: scripts/ is always one level under
    it, so dirname(dirname(__file__)) is correct whether this is a flat dev
    clone or a versioned-cache install - no CLAUDE_PLUGIN_ROOT dependency
    for callers that just need config.json / shim/ alongside this file."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_config(root=None):
    """{"state_root": "..."} - fail-soft to DEFAULT_CONFIG on any read
    problem (missing file, malformed JSON, wrong shape, blank value) so a
    broken config.json degrades the plugin to its documented default
    instead of crashing every hook that imports this module."""
    root = root or plugin_root()
    path = os.path.join(root, "config.json")
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            data = json.loads(f.read())
    except (OSError, ValueError):
        return dict(DEFAULT_CONFIG)
    if not isinstance(data, dict):
        return dict(DEFAULT_CONFIG)
    out = dict(DEFAULT_CONFIG)
    sr = data.get("state_root")
    if isinstance(sr, str) and sr.strip():
        out["state_root"] = sr.strip().strip("/").replace("\\", "/")
    return out


def state_root(vault, root=None):
    """Absolute path to the workstreams state root (config-driven; default
    .vault-meta/workstreams - the 42 existing dirs keep working with no move
    tonight, per 04i)."""
    cfg = load_config(root)
    parts = cfg["state_root"].split("/")
    return os.path.join(vault, *parts)


def sessions_root(vault, root=None):
    """Absolute path to the sidecar directory - the SIBLING of the state
    root's own parent, named workstream-sessions (matches cos's
    .vault-meta/workstreams + .vault-meta/workstream-sessions layout exactly;
    not a second config value, since it is always the state root's own
    sibling by construction)."""
    sr = state_root(vault, root)
    return os.path.join(os.path.dirname(sr), "workstream-sessions")


def looks_like_vault(vault):
    return any(os.path.isdir(os.path.join(vault, m)) for m in VAULT_MARKERS)


# --------------------------------------------------------------------------
# small io primitives
# --------------------------------------------------------------------------

def iso_now():
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_text(path, cap=None):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            data = f.read()
    except OSError:
        return None
    if cap is not None and len(data.encode("utf-8")) > cap:
        clipped = data.encode("utf-8")[:cap].decode("utf-8", "ignore")
        return clipped + "\n[... truncated at %d bytes ...]" % cap
    return data


def read_json(path, cap=None):
    """(data, error) - fail-soft. error is a short label only when the file
    EXISTS but could not be used (oversized/unreadable/malformed/not an
    object); both None means "file does not exist" (the common, silent
    case), never an exception."""
    if not os.path.isfile(path):
        return None, None
    try:
        if cap is not None and os.path.getsize(path) > cap:
            return None, "oversized (over %d bytes)" % cap
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            data = json.loads(f.read())
    except OSError:
        return None, "unreadable"
    except ValueError:
        return None, "malformed JSON"
    if not isinstance(data, dict):
        return None, "not a JSON object"
    return data, None


def atomic_write_lf(path, text, dry_run=False):
    """tmp + os.replace, PID-unique tmp name, LF newlines - never a torn
    file on a crash mid-write; try/finally cleans up a failed tmp so no
    litter survives in a git-tracked, auto-committing tree.

    Auto-writer safety (spec 4.3): `dry_run=True` performs every check a
    real write would (nothing here short-circuits before that point) but
    SKIPS the actual write - no os.makedirs, no tmp file, no os.replace.
    Every primitive in this plugin that ends up here (sidecar.py,
    manifest.py, views.py) threads its own `--dry-run` flag through to
    this parameter rather than reimplementing the skip locally."""
    if dry_run:
        return
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = "%s.tmp.%d" % (path, os.getpid())
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def atomic_write_json(path, obj, dry_run=False):
    atomic_write_lf(path, json.dumps(obj, indent=2, ensure_ascii=False, sort_keys=False) + "\n",
                    dry_run=dry_run)


# --------------------------------------------------------------------------
# the whole-output guard (B5) - one bound per hook command's entire stdout
# --------------------------------------------------------------------------

# The harness caps EACH hook command's stdout independently: over this, the
# whole payload is persisted to a file and only a ~2 KB preview is inlined,
# which is defect D1 (the identity block buried in someone else's oversized
# payload) in a new costume. Same measured figure ballast guards against,
# and the same margin, because it IS measured rather than documented -
# landing exactly on it would bet a whole delivery on the measurement being
# exact.
INLINE_CEILING = 9687
GUARD_MARGIN_BYTES = 256

DELIVERY_TRIM_MARKER = ("## Workstream - TRIMMED %s: %d B over the %d B guard "
                        "(the %d B inline cap less a %d B margin)")
DELIVERY_TRIM_PREFIX = "## Workstream - TRIMMED "


def delivery_limit():
    """The byte budget ONE hook command's stdout may use."""
    return INLINE_CEILING - GUARD_MARGIN_BYTES


def guard_delivery(text, label):
    """Bound one whole hook delivery just before it is written.

    Under budget: returned unchanged. Over: ONE loud marker line naming
    the delivery and by how much, then the text trimmed to fit - the model
    is told what it is missing rather than losing the entire payload to the
    harness's persist-and-preview fallback. Never raises, and never stacks
    a second marker on already-marked text.

    Each of this plugin's three own hooks already caps its own content
    (boot's identity block, precompact's note), so in practice this guard
    is the backstop rather than the working limit; it exists because a cap
    that is only enforced where someone remembered to enforce it is not a
    bound at all."""
    limit = delivery_limit()
    raw = (text or "").encode("utf-8")
    if len(raw) <= limit:
        return text
    if text.startswith(DELIVERY_TRIM_PREFIX):
        return raw[:max(0, limit)].decode("utf-8", "ignore")
    marker = (DELIVERY_TRIM_MARKER
              % (label, len(raw) - limit, limit, INLINE_CEILING,
                 GUARD_MARGIN_BYTES)) + "\n"
    room = limit - len(marker.encode("utf-8"))
    if room <= 0:
        return marker.rstrip("\n")
    return marker + raw[:room].decode("utf-8", "ignore")


# --------------------------------------------------------------------------
# session -> workstream binding (the sidecar read side of the primitive;
# sidecar.py owns the write/self-heal side)
# --------------------------------------------------------------------------

def sidecar_path(vault, session_id, root=None):
    return os.path.join(sessions_root(vault, root), session_id + ".json")


def resolve_bound_dir(vault, session_id, root=None):
    """session_id -> (born_session, ws_dir), or (None, None) if unbound.
    Mirrors cos_workstream_lib.resolve_bound_dir exactly, config-driven.
    Near-zero-cost common case: one os.path.isfile check for the (by far
    most common) unbound session."""
    if not (isinstance(session_id, str) and SAFE_ID_RE.match(session_id)):
        return None, None
    sidecar = sidecar_path(vault, session_id, root)
    data, _err = read_json(sidecar, cap=WS_BINDING_CAP)
    if data is None:
        return None, None
    born_session = data.get("born_session")
    if not (isinstance(born_session, str) and SAFE_ID_RE.match(born_session)):
        return None, None
    ws_dir = os.path.join(state_root(vault, root), born_session)
    if not os.path.isdir(ws_dir):
        return None, None
    return born_session, ws_dir


def manifest_path_for(vault, born_session, root=None):
    return os.path.join(state_root(vault, root), born_session, "workstream.json")


def read_manifest(vault, born_session, root=None):
    return read_json(manifest_path_for(vault, born_session, root), cap=MANIFEST_CAP)


# --------------------------------------------------------------------------
# manifest scan + edge resolution - ONE function shared by boot/list/graph/
# connect (V4: "one manifest-scan function inside the plugin"), ported from
# workstream-index.py's discover_manifests/find_problems/_resolve_ref_display.
# --------------------------------------------------------------------------

SAFE_DIR_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def discover_manifests(ws_root):
    """{dirname: manifest_dict}, [(dirname, reason), ...] orphans, for every
    dir under the state root. dirname == born_session by construction (the
    dir's own name), since every born_session both mints and is named after
    the folder (I1)."""
    manifests, orphans = {}, []
    if not os.path.isdir(ws_root):
        return manifests, orphans
    with os.scandir(ws_root) as it:
        entries = sorted(e.name for e in it if e.is_dir())
    for dirname in entries:
        if not SAFE_DIR_RE.match(dirname):
            continue
        mpath = os.path.join(ws_root, dirname, "workstream.json")
        if not os.path.isfile(mpath):
            orphans.append((dirname, "no workstream.json"))
            continue
        try:
            with open(mpath, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError) as e:
            orphans.append((dirname, "workstream.json unreadable/malformed (%r)" % e))
            continue
        if not isinstance(data, dict):
            orphans.append((dirname, "workstream.json is not a JSON object"))
            continue
        manifests[dirname] = data
    return manifests, orphans


def _usable(v):
    return isinstance(v, str) and v.strip()


def normalize_direct_report(m):
    """The manifest's direct_report, filtered to usability - a dict with a
    usable name or session half, else None (I4: single object|null, never
    a list; L1/R1)."""
    entry = m.get("direct_report")
    if isinstance(entry, dict) and (_usable(entry.get("name")) or _usable(entry.get("session"))):
        return entry
    return None


def normalize_collaborate(m):
    """The manifest's collaborate[], filtered to usable dict entries (R1:
    reciprocal peer edges with a scope note)."""
    raw = m.get("collaborate")
    if not isinstance(raw, list):
        return []
    return [e for e in raw if isinstance(e, dict) and (_usable(e.get("name")) or _usable(e.get("session")))]


def normalize_absorbed(m):
    """The manifest's absorbed[] (AB3, I4 - new field: the determinism
    anchor an overtaker uses to find what it swallowed without a scan).
    Each usable entry needs at least a name or a born_session half."""
    raw = m.get("absorbed")
    if not isinstance(raw, list):
        return []
    return [e for e in raw if isinstance(e, dict)
           and (_usable(e.get("name")) or _usable(e.get("born_session")))]


def resolve_ref_display(entry, manifests):
    """One reference entry (direct_report or one collaborate entry) ->
    (display_name, target_state). Resolves by the immutable session/
    born_session uuid against THIS scan (manifests keyed by born_session,
    since a workstream's dir name IS its own born_session) - a rename on
    the target needs no cross-manifest ripple write; falls back to the
    entry's stored name half, badged ' [unresolved]', only when the
    session half is missing/blank or doesn't resolve in this scan. Ported
    from workstream-index.py's _resolve_ref_display."""
    session = entry.get("session") or entry.get("born_session")
    if isinstance(session, str) and session.strip() and session in manifests:
        target = manifests[session]
        resolved_name = target.get("name")
        if not (isinstance(resolved_name, str) and resolved_name.strip()):
            resolved_name = session
        return resolved_name, target.get("state")
    stored_name = entry.get("name")
    if isinstance(stored_name, str) and stored_name.strip():
        return stored_name + " [unresolved]", None
    return None, None


def find_problems(manifests):
    """Flag-only anomalies across the discovered (successfully-parsed)
    manifests - a SUBSET of connect's 8 invariants (schema, lineage,
    direct_report/collaborate resolution, legacy parents), shared so boot,
    list, graph and connect all report the SAME count from the SAME
    function (V4). Returns [(kind, detail), ...]; never mutates."""
    problems = []

    # duplicate name across dirs
    by_name = {}
    for dirname, m in manifests.items():
        name = m.get("name")
        if isinstance(name, str) and name.strip():
            by_name.setdefault(name.strip(), []).append(dirname)
    for name in sorted(by_name):
        dirs = by_name[name]
        if len(dirs) > 1:
            problems.append(("duplicate name", "%r used by: %s" % (name, ", ".join(sorted(dirs)))))

    for dirname in sorted(manifests):
        m = manifests[dirname]

        # Inv-1: schema - state valid, direct_report object|null, collaborate
        # a list, absorbed[] shape (I4).
        state = m.get("state")
        if not (isinstance(state, str) and state.strip() in VALID_STATES):
            problems.append(("invalid state", "%s: state=%r (expected one of %s)"
                            % (dirname, state, "/".join(VALID_STATES))))
        dr = m.get("direct_report")
        if dr is not None and not isinstance(dr, dict):
            problems.append(("malformed direct_report", "%s: direct_report=%r "
                            "(expected an object or null, never a bare string)"
                            % (dirname, dr)))
        collab = m.get("collaborate")
        if collab is not None and not isinstance(collab, list):
            problems.append(("malformed collaborate", "%s: collaborate=%r (expected a list)"
                            % (dirname, collab)))
        absorbed = m.get("absorbed")
        if absorbed is not None:
            if not isinstance(absorbed, list):
                problems.append(("malformed absorbed", "%s: absorbed=%r (expected a list)"
                                % (dirname, absorbed)))
            else:
                for i, e in enumerate(absorbed):
                    if not isinstance(e, dict) or not (_usable(e.get("name")) or _usable(e.get("born_session"))):
                        problems.append(("malformed absorbed entry",
                                        "%s: absorbed[%d]=%r (expected {name, born_session, dir[, transcript]})"
                                        % (dirname, i, e)))

        # Inv-1b: born_session IS the directory name (I1) - the dir's name
        # both mints and equals the born_session. A mismatch means the
        # immutable identity was rewritten (the very edit set_field now
        # refuses) or the dir was moved; either way this node's edges resolve
        # against the wrong key.
        bs = m.get("born_session")
        if isinstance(bs, str) and bs.strip() and bs != dirname:
            problems.append(("born_session mismatch",
                            "%s: born_session=%r does not match its directory name"
                            % (dirname, bs)))

        # Inv-2: lineage - spawned_from_session resolves or is null/absent
        sfs = m.get("spawned_from_session")
        if isinstance(sfs, str) and sfs.strip() and sfs not in manifests:
            problems.append(("dangling lineage", "%s: spawned_from_session %r does not resolve"
                            % (dirname, sfs)))

        # Inv-3: direct_report resolves, target not closed/absorbed
        report_entry = normalize_direct_report(m)
        if report_entry is not None:
            _name, target_state = resolve_ref_display(report_entry, manifests)
            sess = report_entry.get("session")
            if isinstance(sess, str) and sess.strip() and sess not in manifests:
                problems.append(("dangling direct_report", "%s: direct_report session %r does not resolve"
                                % (dirname, sess)))
            elif target_state in ("closed", "absorbed"):
                problems.append(("stale direct_report (re-home)", "%s: reports to a %s workstream"
                                % (dirname, target_state)))

        # Inv-4: collaborate resolves + reciprocated
        for entry in normalize_collaborate(m):
            peer = entry.get("session")
            if not (isinstance(peer, str) and peer.strip()):
                continue
            if peer not in manifests:
                problems.append(("dangling collaborate", "%s: collaborate session %r does not resolve"
                                % (dirname, peer)))
                continue
            peer_collab = normalize_collaborate(manifests[peer])
            if not any(e.get("session") == dirname for e in peer_collab):
                problems.append(("one-sided collaborate", "%s <-> %s not reciprocated" % (dirname, peer)))

        # Inv-5: no legacy parents on an ACTIVE manifest
        if isinstance(state, str) and state.strip() == "active":
            has_parents = isinstance(m.get("parents"), list) and len(m.get("parents")) > 0
            has_parent_session = _usable(m.get("parent_session"))
            has_parent = _usable(m.get("parent"))
            has_rebound = isinstance(m.get("rebound"), list) and len(m.get("rebound")) > 0
            if has_parents or has_parent_session or has_parent or has_rebound:
                problems.append(("legacy field on active manifest",
                                "%s: carries retired parents/parent/parent_session/rebound (L3, R1)"
                                % dirname))

    return problems


# --------------------------------------------------------------------------
# dependency degrade detection (E1-E7): ballast vendored (HARD), vault-lock/
# grill soft. Mirrors the resolution shim/ballast-shim.py itself uses: the
# copy VENDORED under this plugin's own vendor/ballast/ FIRST, then a
# separately-installed ballast (CLAUDE_PLUGIN_ROOT sibling, then the
# installed-plugins registry) as a fallback - so this plugin's own degrade
# checks agree with what the shim finds at hook time, and a self-contained
# install needs no separately-installed ballast at all.
# --------------------------------------------------------------------------

def _semver_dirs(home_dir):
    try:
        children = os.listdir(home_dir)
    except OSError:
        return []
    out = []
    for name in children:
        parts = name.split(".")
        if len(parts) == 3 and all(p.isdigit() for p in parts):
            full = os.path.join(home_dir, name)
            out.append((tuple(int(p) for p in parts), full))
    out.sort(reverse=True)
    return [full for _, full in out]


def _plugin_roots():
    """Every plausible plugins-root directory to look a sibling plugin up
    in: CLAUDE_PLUGIN_ROOT's own parent(s) (flat + versioned-cache), then
    the installed-plugins registry's conventional home. Order-independent
    for our purposes (existence check only)."""
    roots = []
    own = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if own:
        own = os.path.normpath(own)
        roots.append(os.path.dirname(own))
        roots.append(os.path.dirname(os.path.dirname(own)))
    home = os.environ.get("USERPROFILE") or os.environ.get("HOME")
    if home:
        roots.append(os.path.join(home, ".claude", "plugins", "cache"))
        roots.append(os.path.join(home, ".claude", "plugins"))
    return [r for r in roots if r and os.path.isdir(r)]


def _plugin_present(name, marker_rel):
    """True if a sibling plugin `name` is installed anywhere this process
    can see, flat or versioned-cache layout, checked by the presence of
    marker_rel (a path relative to that plugin's own root)."""
    for root in _plugin_roots():
        home = os.path.join(root, name)
        if os.path.isfile(os.path.join(home, marker_rel)):
            return True
        for versioned in _semver_dirs(home):
            if os.path.isfile(os.path.join(versioned, marker_rel)):
                return True
    return False


REGISTRY_CAP = 1024 * 256   # byte cap on installed_plugins.json we will read


def _script_under(plugin_home, script_name):
    """`<plugin_home>/scripts/<script_name>` in either layout - flat dev
    clone first, then the highest versioned-cache dir - or None."""
    flat = os.path.join(plugin_home, "scripts", script_name)
    if os.path.isfile(flat):
        return flat
    for versioned in _semver_dirs(plugin_home):
        candidate = os.path.join(versioned, "scripts", script_name)
        if os.path.isfile(candidate):
            return candidate
    return None


def _ballast_marketplace_script(home, script_name):
    """Step 2 of ballast_available()'s fallback chain: scan every
    marketplace dir under `<home>/.claude/plugins/cache/*/ballast/*/
    scripts/<script_name>` (any marketplace, any version - first hit
    wins). Returns the path, or None.

    _plugin_roots()'s own HOME fallback (used when CLAUDE_PLUGIN_ROOT is
    unset) checks `<home>/.claude/plugins/cache/ballast/...` directly,
    which is right for a flat dev clone but misses the real installed-
    cache shape: the harness nests each plugin one level under its
    marketplace name, e.g. `cache/staff-plugins/ballast/0.1.0/scripts/
    ballast.py` (confirmed 2026-09-07 against the live cache). A shell
    invocation - a skill running outside a hook - has no sibling to walk
    from and never sets CLAUDE_PLUGIN_ROOT, so step 1 alone found
    nothing there even with ballast genuinely installed and running."""
    cache = os.path.join(home, ".claude", "plugins", "cache")
    try:
        marketplaces = os.listdir(cache)
    except OSError:
        return None
    for mkt in marketplaces:
        found = _script_under(os.path.join(cache, mkt, "ballast"), script_name)
        if found:
            return found
    return None


def _ballast_marketplace_glob_hit(home):
    return _ballast_marketplace_script(home, "ballast.py") is not None


def _vendored_ballast_script(script_name):
    """The copy this plugin vendors under its own `vendor/ballast/`, checked
    FIRST so a self-contained install runs its own version-pinned engine and
    never depends on a separately-installed `ballast`. This is the same
    vendored-first resolution shim/ballast-shim.py applies at hook time."""
    candidate = os.path.join(plugin_root(), "vendor", "ballast", script_name)
    return candidate if os.path.isfile(candidate) else None


def ballast_script(script_name):
    """Absolute path to a runnable ballast `<script_name>` - the copy
    VENDORED under this plugin's own `vendor/ballast/` first, else a
    separately-installed ballast's `scripts/<script_name>` - or None when
    ballast can be found nowhere. The same resolution ballast_available()
    uses, in the same order, so a caller that needs to actually RUN one of
    ballast's scripts (manifest.py's approval gate runs `approve.py`)
    resolves it exactly where the shim would.

    Deliberately does NOT consult the installed-plugins registry as a path:
    that step is a belief, not a path, and there is nothing to execute at
    the end of it."""
    vendored = _vendored_ballast_script(script_name)
    if vendored:
        return vendored
    for root in _plugin_roots():
        found = _script_under(os.path.join(root, "ballast"), script_name)
        if found:
            return found
    home = os.environ.get("USERPROFILE") or os.environ.get("HOME")
    if not home:
        return None
    return _ballast_marketplace_script(home, script_name)


def _ballast_in_installed_registry(home):
    """Step 3 (optional, weakest signal): `installed_plugins.json` carries
    a `plugins` map keyed `<name>@<marketplace>` - a `ballast@...` key
    means the harness itself believes ballast is installed, independent
    of this process being able to see its on-disk shape."""
    registry = os.path.join(home, ".claude", "plugins", "installed_plugins.json")
    data, _err = read_json(registry, cap=REGISTRY_CAP)
    if not isinstance(data, dict):
        return False
    plugins = data.get("plugins")
    if not isinstance(plugins, dict):
        plugins = data   # tolerate a flat {key: [...]} shape too
    return any(isinstance(k, str) and k.startswith("ballast@") for k in plugins)


def ballast_available():
    """True if a `ballast` engine is available to THIS process - the copy
    VENDORED under this plugin's own `vendor/ballast/` (checked FIRST), else
    a separately-installed `ballast` - via a vendored-first, then three-step
    fallback chain (2026-09-07 fork incident: the first post-restart
    `/workstream:fork` had its `adopt_precheck()` refuse the mint even
    though ballast 0.1.0 was installed and its hooks were running - the
    precheck ran from a skill's shell rather than a hook, so
    `CLAUDE_PLUGIN_ROOT` was unset and the old single-path check saw
    nothing):

      0. `_vendored_ballast_script('ballast.py')` - this plugin's own
         vendored engine. A self-contained install stops here and needs no
         separately-installed ballast at all; the steps below are the
         fallback for a consumer that does not vendor.
      1. `_plugin_present()`'s existing CLAUDE_PLUGIN_ROOT-sibling lookup
         - unchanged, and already correct whenever a hook set the env var.
      2. `_ballast_marketplace_glob_hit()` - scan every marketplace dir
         under `~/.claude/plugins/cache/*/ballast/*/scripts/ballast.py`,
         which step 1's own HOME fallback does not reach (see its
         docstring). Tried whether step 1 failed OR the env var was
         simply unset, since a shell-run check has no env var to begin
         with.
      3. `_ballast_in_installed_registry()` - `installed_plugins.json`
         carrying a `ballast@...` key, as a last, weakest signal.

    True if ANY step succeeds; no env var currently overrides this
    result (none exists in this plugin yet - honor one here if that
    changes)."""
    if _vendored_ballast_script("ballast.py"):
        return True
    if _plugin_present("ballast", os.path.join("scripts", "ballast.py")):
        return True
    home = os.environ.get("USERPROFILE") or os.environ.get("HOME")
    if not home:
        return False
    if _ballast_marketplace_glob_hit(home):
        return True
    return _ballast_in_installed_registry(home)


def vault_lock_available(vault):
    """vault-lock's degrade signal is per-VAULT (the materialized CLI),
    not per-install - a vault-lock plugin can be installed but not yet
    have run its own SessionStart in this vault (fresh clone). Checking
    the materialized path is what actually matters to a write this
    session might make."""
    return os.path.isfile(os.path.join(vault, ".vault-meta", "bin", "vault-lock.sh"))


def grill_available():
    return _plugin_present("grill", os.path.join("skills", "grilling", "SKILL.md"))


def adopt_precheck():
    """(ok, message) - the HARD ballast gate adopt must check before
    minting a manifest (E5, Dependencies page: "no Ballast -> the plugin
    refuses to adopt: identity without continuity is today's defect, not
    a feature"). ok=True, message=None when clear to proceed."""
    if not ballast_available():
        return False, ("workstream:adopt refuses - no `ballast` engine is "
                       "available: none is vendored under this plugin's "
                       "vendor/ballast/, and none is separately installed. "
                       "Identity without continuity is the defect this rebuild "
                       "exists to fix, not a feature to ship anyway. Re-vendor "
                       "the engine (sync-templates.py from the ballast repo), "
                       "or install `ballast`, then retry.")
    return True, None
