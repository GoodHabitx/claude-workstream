#!/usr/bin/env python3
# claude-ballast — the consumer shim.
#
# COPY THIS FILE BYTE-IDENTICAL into every consumer plugin (e.g.
# <consumer>/shim/ballast-shim.py). It is the one file every consumer's
# hooks.json points at; only the ballast.json path each consumer's hook
# line passes via --scope differs (02, Resolved: "Shim finds ballast ->
# via the installed-plugins registry; the library ships zero hooks; fails
# open if absent."). Do not modify a consumer's copy — if the shim needs a
# fix, fix it here and re-copy everywhere it's installed; a per-consumer
# fork of this file defeats the point of having one canonical shim.
"""ballast-shim.py EVENT --scope PATH [--vault-marker NAME] — resolves the
installed `ballast` plugin's scripts/ballast.py and forwards argv + stdin
to it verbatim.

Resolution (installed-plugins registry, not a relative path guess — a
consumer plugin and the ballast plugin are siblings under the SAME
plugins root, but that root's name/location is not fixed, so the shim
reads the registry the harness itself writes):
  1. `CLAUDE_PLUGIN_ROOT` env var (set by the harness for every hook
     invocation) -> walk up to find the plugins root: the parent of
     CLAUDE_PLUGIN_ROOT's own plugin directory, i.e.
     `dirname(dirname(CLAUDE_PLUGIN_ROOT))` for a flat layout, or
     `dirname(dirname(dirname(CLAUDE_PLUGIN_ROOT)))` for the versioned-
     cache layout (`<root>/<plugin>/<semver>/...`) — both are tried.
  2. Within each candidate plugins root, read `installed_plugins.json` if
     present (Windows: the per-user profile's `.claude/plugins/
     installed_plugins.json`, resolved via `%USERPROFILE%` at runtime,
     never a hardcoded drive path; the WSL/Linux equivalent under
     `~/.claude/plugins/installed_plugins.json`) and also just try the
     constructed path `<root>/ballast/scripts/ballast.py` (flat) or the
     highest `<root>/ballast/<semver>/scripts/ballast.py` (versioned
     cache) — the registry is consulted for the ballast version string
     when present, but the constructed path is what is actually invoked
     either way. This mirrors the same flat-vs-versioned-cache duality
     hiring-manager's floor.py resolves trusted linters by (same
     ecosystem convention, independently arrived at here for the same
     reason: dev clones are flat, installs are versioned).
  3. `~/.claude/plugins/installed_plugins.json` directly (both `%USERPROFILE%`
     on Windows and `$HOME` on WSL/Linux are tried) as a final fallback,
     independent of CLAUDE_PLUGIN_ROOT, in case a caller invokes this shim
     outside a normal hook context (e.g. a test harness).

Fails OPEN, always: if ballast cannot be found ANYWHERE by the above, or
resolves but errors, this shim prints exactly ONE stderr notice and exits
0 — a consumer must never fail its own hook (startup, a turn, a
compaction) because ballast is not installed. `no Ballast -> the plugin
refuses to adopt` (per the decision register) is a CONSUMER-level policy
decision made elsewhere (e.g. a workstream's own `adopt` verb checking for
ballast explicitly); this shim's only job is per-event forwarding, and it
degrades silently by design.

Stdin/stdout: read once, forwarded byte-for-byte. This shim never parses
the hook payload itself — ballast.py does that.
"""
import os
import subprocess
import sys

INTERPRETER_CANDIDATES = ("python3", "python", "py -3", "py")


def _try_path(path):
    return path if path and os.path.isfile(path) else None


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
            if os.path.isdir(os.path.join(full, "scripts")):
                out.append((tuple(int(p) for p in parts), full))
    out.sort(reverse=True)
    return [full for _, full in out]


def _candidate_ballast_scripts(plugins_root):
    """Every plausible scripts/ballast.py under one plugins root, flat
    layout first (dev clones), then the highest versioned-cache dir."""
    if not plugins_root or not os.path.isdir(plugins_root):
        return []
    home = os.path.join(plugins_root, "ballast")
    out = []
    flat = os.path.join(home, "scripts", "ballast.py")
    if os.path.isfile(flat):
        out.append(flat)
    for versioned in _semver_dirs(home):
        candidate = os.path.join(versioned, "scripts", "ballast.py")
        if os.path.isfile(candidate):
            out.append(candidate)
    return out


def _plugins_roots_from_env():
    """Every plugins-root candidate implied by CLAUDE_PLUGIN_ROOT, trying
    both the flat (<root>/<plugin>/scripts/...) and versioned-cache
    (<root>/<plugin>/<semver>/scripts/...) layouts, since this shim's own
    consumer could be installed either way."""
    own_root = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if not own_root:
        return []
    own_root = os.path.normpath(own_root)
    candidates = [os.path.dirname(own_root)]              # flat: <root>/<plugin>
    parts_up_two = os.path.dirname(os.path.dirname(own_root))
    candidates.append(parts_up_two)                         # cache: <root>/<plugin>/<semver>
    return [c for c in candidates if c and os.path.isdir(c)]


def _plugins_roots_from_registry():
    """installed_plugins.json's own directory, read from either OS's
    conventional home — a final, CLAUDE_PLUGIN_ROOT-independent fallback."""
    roots = []
    home = os.environ.get("USERPROFILE") or os.environ.get("HOME")
    if home:
        registry = os.path.join(home, ".claude", "plugins",
                                "installed_plugins.json")
        plugins_dir = os.path.join(home, ".claude", "plugins")
        if os.path.isfile(registry):
            # The registry's own presence confirms the plugins dir; the
            # cache layout keeps plugin code under plugins/cache/<name>/.
            for sub in ("cache", ""):
                cand = os.path.join(plugins_dir, sub) if sub else plugins_dir
                if os.path.isdir(cand):
                    roots.append(cand)
    return roots


def resolve_ballast_script():
    for root in _plugins_roots_from_env():
        found = _candidate_ballast_scripts(root)
        if found:
            return found[0]
    for root in _plugins_roots_from_registry():
        found = _candidate_ballast_scripts(root)
        if found:
            return found[0]
    return None


def resolve_interpreter():
    for cmd in INTERPRETER_CANDIDATES:
        parts = cmd.split(" ")
        try:
            proc = subprocess.run(
                parts + ["-c", "import sys; sys.exit(0)"],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if proc.returncode == 0:
                return parts
        except (OSError, FileNotFoundError):
            continue
    return None


def main(argv):
    script = resolve_ballast_script()
    if script is None:
        sys.stderr.write(
            "ballast-shim: no installed `ballast` plugin found (checked "
            "CLAUDE_PLUGIN_ROOT-relative plugins roots and the "
            "installed_plugins.json registry) — this consumer's ballast "
            "event is a no-op this session; install the ballast plugin to "
            "restore continuity for this scope.\n")
        return 0

    interpreter = resolve_interpreter()
    if interpreter is None:
        sys.stderr.write(
            "ballast-shim: no Python interpreter resolved (tried %s) — "
            "ballast did NOT run this event; install a Python 3 "
            "interpreter to restore continuity for this scope.\n"
            % ", ".join(INTERPRETER_CANDIDATES))
        return 0

    try:
        stdin_data = sys.stdin.buffer.read() if sys.stdin and not sys.stdin.isatty() else b""
    except Exception:
        stdin_data = b""

    try:
        proc = subprocess.run(interpreter + [script] + list(argv),
                             input=stdin_data, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE)
        if proc.stdout:
            sys.stdout.buffer.write(proc.stdout)
        if proc.stderr:
            sys.stderr.buffer.write(proc.stderr)
    except Exception as exc:
        sys.stderr.write("ballast-shim: forwarding to ballast.py failed "
                         "(fail open): %r\n" % exc)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except SystemExit:
        raise
    except BaseException:
        sys.exit(0)
