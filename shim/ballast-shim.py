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
"""ballast-shim.py EVENT --scope PATH [--vault-marker NAME] — resolves
ballast's engine (scripts/ballast.py) and forwards argv + stdin to it
verbatim.

Resolution, in order:
  0. A copy VENDORED inside the consumer itself —
     `$CLAUDE_PLUGIN_ROOT/vendor/ballast/ballast.py`, or `ballast.py` beside
     this shim, or `../vendor/ballast/ballast.py` relative to it — checked
     FIRST, so a self-contained consumer that vendors ballast never depends
     on a separately-installed one. The remaining steps are the fallback for
     a consumer that does NOT vendor (a separately-installed `ballast`
     plugin, found via the installed-plugins registry — not a relative-path
     guess, since that root's name/location is not fixed):
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
compaction) because ballast is absent. The single exception is ballast's
own PreToolUse approval refusal, exit 2, which is forwarded verbatim:
swallowing it would turn every refusal into an allow. Whether a missing
ballast should block a consumer operation is a CONSUMER-level policy
decision made elsewhere (e.g. a consumer's own `adopt` verb checking for
ballast explicitly); this shim's only job is per-event forwarding, and it
degrades silently by design.

Stdin/stdout: read once, forwarded byte-for-byte. This shim never parses
the hook payload itself — ballast.py does that.
"""
import os
import subprocess
import sys

INTERPRETER_CANDIDATES = ("python3", "python", "py -3", "py")

# The hook protocol's refusal code, duplicated here because this file is
# standalone by contract (it is copied into consumers, and cannot import
# ballast_lib). Kept in step with ballast_lib.REFUSAL_EXIT.
REFUSAL_EXIT = 2


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


def _vendored_ballast_script():
    """A copy VENDORED inside the consumer itself, checked FIRST so a
    self-contained consumer never depends on a separately-installed ballast.
    Tried three ways, most-reliable first:
      * `$CLAUDE_PLUGIN_ROOT/vendor/ballast/ballast.py` — the consumer's own
        plugin root (set by the harness) plus the vendor subdir;
      * `ballast.py` beside this shim (shim vendored INTO vendor/ballast/);
      * `../vendor/ballast/ballast.py` relative to this shim (shim kept in
        the consumer's own shim/ dir)."""
    candidates = []
    own_root = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if own_root:
        candidates.append(os.path.join(os.path.normpath(own_root),
                                       "vendor", "ballast", "ballast.py"))
    here = os.path.dirname(os.path.abspath(__file__))
    candidates.append(os.path.join(here, "ballast.py"))
    candidates.append(os.path.join(os.path.dirname(here),
                                   "vendor", "ballast", "ballast.py"))
    for cand in candidates:
        found = _try_path(cand)
        if found:
            return found
    return None


def resolve_ballast_script():
    vendored = _vendored_ballast_script()
    if vendored:
        return vendored
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
            "ballast-shim: no ballast engine found (checked the vendored "
            "copy under vendor/ballast/, CLAUDE_PLUGIN_ROOT-relative plugins "
            "roots, and the installed_plugins.json registry) — this "
            "consumer's ballast event is a no-op this session; vendor "
            "ballast into vendor/ballast/ (or install the ballast plugin) to "
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
        # Forward the ONE deliberate non-zero code ballast uses: 2, the
        # PreToolUse approval refusal (hook protocol: exit 2 + the reason
        # on stderr). Swallowing it would turn every refusal into an
        # allow, so the gate would look wired and enforce nothing.
        # EVERY other code maps to 0 — fail open is this shim's whole
        # contract, and a crash in ballast.py must never block a turn.
        if proc.returncode == REFUSAL_EXIT:
            return REFUSAL_EXIT
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
