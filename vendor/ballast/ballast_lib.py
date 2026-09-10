#!/usr/bin/env python3
# claude-ballast — the continuity engine's shared library.
"""Import-only module shared by scripts/ballast.py. Never itself a hook,
never invoked directly.

Owns: scope loading + validation (ballast.json), the hot/log/policy file
formats, the freshness gate, the completion check, and the tiny per-session
bookkeeping ballast needs for re-ground cadence + post-compaction dedup.

Design choices carried from the model pages, restated here so the "why" is
next to the code that implements it:

  * The freshness gate compares CONTENT, never file mtimes (04g Build notes:
    "gate = whole-file mtime ... -> gate = hot block's `updated` field vs
    log's newest entry ... a compliance append anywhere clears the mtime
    gate even if the labeled block is stale"). hot.md's own `updated` slot
    IS the stamp; there is no separate machine-maintained pointer file for
    it, because log.md only ever receives SIGNIFICANT-write entries (see
    `is_significant` below) — so "any log line newer than hot's `updated`"
    already excludes reads by construction, which is exactly the "reads
    never trigger it" rule (02, X6) with no extra bookkeeping.
  * log.md is pure append, opened in 'a' mode, UTF-8, newline='\\n' — no
    tmp-file + os.replace rotation step, ever. This scope has no log cap
    (only hot and policy are capped — 02's five-class table gives log
    gate {ok:none} and no size field appears in ballast.json's declared
    field list), and rotation-via-tmp-rename is exactly the mechanism
    behind the measured 14-file, ~900 KB tracked-orphan defect (D4, 02's
    "log build note") on win32 — so the fix is to never do that step at
    all, not to do it more carefully.
  * The five classes' own gates (02's table): log {ok:none}, hot
    {w:Stop + compaction}, index {ok:none}, policy {bad:approval to
    edit}. Ballast enforces the ones a hook CAN enforce (hot's Stop gate);
    a human-approval gate on policy.md's own edits is not a hook's job to
    enforce — it is enforced by whoever wires the approval step outside
    this engine (B4 says ballast lints + caps the injected policy set; it
    does not gate who is allowed to write policy.md).
  * required_slots / hot's fixed template: "delta + a fixed slot template
    to fill (not a free rewrite)" (02, Resolved 2026-09-06). Ballast
    supplies the template shape (key: value lines) and reads it back the
    same way; it never freely rewrites hot.md itself (B2 — ballast
    detects, the session reconciles).
"""
import datetime
import json
import os
import re
import sys

ENGINE_VERSION = "0.2.0"

# --- small cross-platform helpers -----------------------------------------

_SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def semver_tuple(s):
    m = _SEMVER_RE.match((s or "").strip())
    if not m:
        return None
    return tuple(int(x) for x in m.groups())


def semver_at_least(have, need):
    """True if `have` >= `need` as dotted-triple semvers. Any parse failure
    on either side is treated as "cannot tell" -> True (fail OPEN — a
    malformed min_engine string must never itself block a session)."""
    h, n = semver_tuple(have), semver_tuple(need)
    if h is None or n is None:
        return True
    return h >= n


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


_TS_RE = re.compile(
    r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)")


def parse_ts(text):
    """Best-effort ISO-8601 timestamp extraction from a free-text value
    (hot's `updated` slot, or a log line). Returns a timezone-aware
    datetime, or None if nothing timestamp-shaped is found — callers treat
    None as "cannot compare," never as "definitely stale" or "definitely
    fresh" (an unparsable stamp must not itself trap a session)."""
    if not text:
        return None
    m = _TS_RE.search(text)
    if not m:
        return None
    raw = m.group(1)
    raw = raw.replace("Z", "+00:00")
    if re.search(r"[+-]\d{4}$", raw):
        raw = raw[:-2] + ":" + raw[-2:]
    try:
        dt = datetime.datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt


SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def safe_session_id(session_id):
    return isinstance(session_id, str) and bool(SAFE_ID_RE.match(session_id))


def read_stdin_json():
    """Read + parse the hook's stdin JSON payload. Any failure at all (no
    stdin, a tty, empty body, malformed JSON, a non-object) degrades to
    `{}` — never raises. Every ballast.py handler must keep working, in
    degraded form, on an empty payload."""
    try:
        if sys.stdin is None or sys.stdin.isatty():
            return {}
        raw = sys.stdin.read()
    except Exception:
        return {}
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def read_text(path, cap_bytes=None):
    """Read a text file; None if missing/unreadable. `cap_bytes` reads at
    most that many bytes (a runaway file must never be slurped whole into
    a hook's memory) — the read is loud about truncation via the returned
    `truncated` flag, never silent.

    A falsy `path` — a scope that set the file field to an explicit `null`
    — reads as "this class is DISABLED for this scope", the same as a
    missing file. It must never raise: `open(None)` throws TypeError, not
    OSError, so without this line one `"hot": null` would take the whole
    event down."""
    if not path:
        return None, False
    try:
        with open(path, "rb") as f:
            if cap_bytes is not None:
                raw = f.read(cap_bytes + 1)
                truncated = len(raw) > cap_bytes
                raw = raw[:cap_bytes]
            else:
                raw = f.read()
                truncated = False
    except OSError:
        return None, False
    return raw.decode("utf-8", errors="replace"), truncated


# Every durable write in this module goes through this flag first
# (ballast.py's own `--dry-run` CLI flag sets it before dispatching to a
# handler). dry-run suppresses the actual filesystem mutation but still
# runs every read/decision path, so `--dry-run` is a real smoke test of
# a scope's wiring, not just a no-op. See scripts/validate-scope.py for a
# read-only *linter*, a different tool from this runtime dry-run switch.
DRY_RUN = False


def append_text(path, line):
    """Atomic-enough append for a single-writer-at-a-time file: open in 'a'
    mode (OS-level O_APPEND on both POSIX and Windows) and write once. No
    tmp-file + rename step — see the module docstring's log-rotation note.
    Creates the parent directory if missing; creates the file if absent.
    Honors module-level `DRY_RUN` (skips the actual write; the line is
    still validated/formatted the same way)."""
    if not path:
        return   # the log class is disabled for this scope ("log": null)
    text = line if line.endswith("\n") else line + "\n"
    if DRY_RUN:
        return
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        f.write(text)


def write_text_atomic(path, text):
    """Whole-file replace via tmp+os.replace — used ONLY for ballast's own
    small bookkeeping files (session state JSON), never for log.md (see the
    module docstring). os.replace is atomic on both POSIX and win32 for a
    same-volume rename, and these files are always small and short-lived,
    so the win32 tmp-orphan failure mode measured against log.md's
    rotation path (D4) does not apply the same way — but a crash mid-write
    or mid-replace still leaves a `<path>.tmp.<pid>` litter file, and
    "cleaned up on next successful write to the same path" (this
    docstring's own prior claim) does NOT hold for a path that is never
    written again — this is exactly how the 16 tracked `.tmp.*` orphans
    (B3, 2026-09-07 audit) accumulated and got swept into the consuming
    vault's auto-commit. The try/except unlinks the tmp on ANY exception,
    then re-raises, so a failed write never litters. Honors module-level `DRY_RUN` (skips the
    actual write)."""
    if DRY_RUN:
        return
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
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


# --- scope (ballast.json) --------------------------------------------------

# Caps are CEILINGS, not targets (context-management.md, 2026-09-08): the
# quality ceiling sits far below the mechanical one, so a scope should
# usually sit well under its cap. hot's rose 1024 -> 4096 once hot.md and
# policy.md stopped sharing one hook command's inline envelope (--part).
DEFAULT_HOT_CAP = 4096          # 4 KB ceiling — hot.md schema v2
DEFAULT_POLICY_CAP = 7168       # <=7 KB whole — B4, 04g "Policy ... <=7 KB"
DEFAULT_GLOSSARY_CAP = 4096     # glossary.md — tens of lines, not a lexicon
DEFAULT_DELTA_CAP = 2048        # the log.md delta that rides hot.md
# The heading the policy block injects under. Configurable so a scope
# holding rules shared across a consumer's other scopes can say e.g.
# `## Global policy` rather than a second, indistinguishable `## Policy`.
DEFAULT_POLICY_TITLE = "Policy"

# --- the approval gate's own constants (A9) --------------------------------
# The filenames the PreToolUse gate is armed for. Filename-based on
# purpose: the gate has to decide from a hook payload's `file_path` alone,
# before any scope owning that file is known.
GATED_FILENAMES = ("policy.md", "glossary.md")
GLOSSARY_FILENAME = "glossary.md"
APPROVALS_DIRNAME = ".ballast-approvals"
GATE_DISABLED_FILENAME = "ballast-gate.disabled"
GLOSSARY_GATE_DISABLED_FILENAME = "ballast-gate-glossary.disabled"
# A token is a one-time, short-lived permission slip, not a session-long
# one: the point is that the operator saw THIS text just now.
DEFAULT_APPROVAL_TTL = 600
# The hook protocol's refusal code: exit 2 with the reason on stderr.
REFUSAL_EXIT = 2
# A runaway file must never be slurped whole into a hook's memory, cap or
# no cap — this is the read bound, not a budget.
FILE_READ_LIMIT = 1024 * 1024
DEFAULT_REGROUND_INTERVAL = 25  # X4 — measured p90 turn-gap census
INLINE_CEILING = 9687           # X1 — the harness's own measured inline line

# A4 — the guard. Every stdout emission, on every event and every part,
# self-checks its own final byte size against INLINE_CEILING less this
# margin. The margin exists because INLINE_CEILING is a MEASURED figure,
# not a documented one: landing exactly on it would bet the whole delivery
# (the harness persists an over-cap payload to a file and inlines a ~2 KB
# preview instead — all-or-nothing) on that measurement being exact.
GUARD_MARGIN_BYTES = 256

DELIVERY_TRIM_MARKER = ("## Ballast — TRIMMED %s: %d B over the %d B guard "
                        "(the %d B inline cap less a %d B margin)")
DELIVERY_TRIM_PREFIX = "## Ballast — TRIMMED "

# Two DIFFERENT budgets can trim a delivery, and both write a marker
# starting with DELIVERY_TRIM_PREFIX — so a bare prefix test (what
# /ballast:check used to do) reports one of them as the other, and tells
# the operator to shrink something that is already small. These are the
# pieces unique to each marker; `trim_causes` reads them, and a test
# formats both real markers to pin them against drift.
DELIVERY_TRIM_TAIL = " B guard ("      # only DELIVERY_TRIM_MARKER has it
TRIM_CAUSE_DELIVERY = ("the delivery guard cut this whole part to fit its "
                       "inline budget; shrink what this part injects")
TRIM_CAUSE_SLOT = ("a per-slot cap cut one hot.md slot; shorten the slot "
                   "(the part itself is within budget)")
TRIM_CAUSE_UNKNOWN = "a trim marker of an unrecognized shape"


def trim_causes(text):
    """Every distinct trim cause present in a rendered delivery, in the
    order its markers appear; `[]` when nothing was trimmed. The two
    causes come from different budgets and call for different fixes, so
    they are reported apart."""
    causes = []
    slot_prefix = HOT_TRIM_MARKER.split("%s")[0]
    for line in (text or "").splitlines():
        if not line.startswith(DELIVERY_TRIM_PREFIX):
            continue
        if line.startswith(slot_prefix):
            cause = TRIM_CAUSE_SLOT
        elif DELIVERY_TRIM_TAIL in line:
            cause = TRIM_CAUSE_DELIVERY
        else:
            cause = TRIM_CAUSE_UNKNOWN
        if cause not in causes:
            causes.append(cause)
    return causes

# The completion check's default required-slot set is EMPTY: ballast holds
# no opinion about which hot.md slots a consumer must fill. A scope declares
# its own `required_slots` in ballast.json; a scope that declares none has
# no completion gate. Keeps the engine consumer-agnostic.
DEFAULT_REQUIRED_SLOTS = []


def delivery_limit():
    """The byte budget ONE hook command's stdout may use: the measured
    inline ceiling less the safety margin."""
    return INLINE_CEILING - GUARD_MARGIN_BYTES


def guard_delivery(text, label, budget=None):
    """A4's guard, applied to one whole delivery just before it is written.

    Under budget: returned unchanged. Over: ONE loud marker line naming the
    delivery and by how much, then the text trimmed to fit — the model is
    told what it is missing rather than losing the whole payload to the
    harness's file-persist-and-preview fallback. `budget` overrides the
    default limit (Stop's decision JSON budgets its `reason` field, since
    trimming the serialized line would leave unparseable JSON). Never
    raises, and never stacks a second marker on already-marked text."""
    limit = delivery_limit() if budget is None else budget
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


class ScopeError(Exception):
    """Raised only by validate-scope.py's strict path. ballast.py itself
    never raises this outward — a broken scope degrades to a stderr notice
    and a silent no-op (fail OPEN, per every event handler's contract)."""


def load_scope(scope_path):
    """Load + normalize a ballast.json into a plain dict with every field
    defaulted and every path resolved to an absolute filesystem path.
    Raises ScopeError on anything wrong — callers in ballast.py catch this
    and fail open; validate-scope.py lets it surface as a real error.

    Path resolution (docs/ballast.json.md is the authoritative spec):
      * `root` — resolved relative to the ballast.json file's OWN
        directory if relative; used as-is if absolute. Defaults to "."
        (ballast.json lives inside the scope root — the common case).
      * `log` / `hot` / `index` / `policy` / `glossary` — resolved
        relative to `root` if relative; used as-is if absolute. Default to
        `log.md`, `hot.md`, `index.md`, `policy.md`, `glossary.md`
        respectively. `policy` and `glossary` are the classes allowed to
        be genuinely absent (most scopes carry no standing policy and no
        glossary) — a missing declared path there is fine and silent, and
        a missing declared log/hot/index path just means "not written
        yet," also fine.
      * Any file field set to an explicit `null` means "this scope has no
        such file": the class is disabled, never crashed on.
    """
    scope_path = os.path.abspath(scope_path)
    scope_dir = os.path.dirname(scope_path)
    raw, truncated = read_text(scope_path, cap_bytes=1024 * 1024)
    if raw is None:
        raise ScopeError("cannot read scope file: %s" % scope_path)
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise ScopeError("scope file is not valid JSON (%s): %s"
                         % (exc, scope_path))
    if not isinstance(data, dict):
        raise ScopeError("scope file must be a JSON object: %s" % scope_path)

    def _resolve(base, rel_or_abs):
        if os.path.isabs(rel_or_abs):
            return os.path.normpath(rel_or_abs)
        return os.path.normpath(os.path.join(base, rel_or_abs))

    root_field = data.get("root", ".")
    if not isinstance(root_field, str) or not root_field.strip():
        raise ScopeError("`root` must be a non-empty string: %s" % scope_path)
    root = _resolve(scope_dir, root_field)

    def _file_field(name, default):
        val = data.get(name, default)
        if val is None:
            return None
        if not isinstance(val, str) or not val.strip():
            raise ScopeError("`%s` must be a non-empty string or null: %s"
                             % (name, scope_path))
        return _resolve(root, val)

    log_path = _file_field("log", "log.md")
    hot_path = _file_field("hot", "hot.md")
    index_path = _file_field("index", "index.md")
    policy_path = _file_field("policy", "policy.md")
    glossary_path = _file_field("glossary", "glossary.md")

    def _cap_field(name, default):
        val = data.get(name, default)
        if not isinstance(val, int) or isinstance(val, bool) or val <= 0:
            raise ScopeError("`%s` must be a positive integer: %s"
                             % (name, scope_path))
        return val

    hot_cap = _cap_field("hot_cap_bytes", DEFAULT_HOT_CAP)
    policy_cap = _cap_field("policy_cap_bytes", DEFAULT_POLICY_CAP)
    glossary_cap = _cap_field("glossary_cap_bytes", DEFAULT_GLOSSARY_CAP)

    required_slots = data.get("required_slots", DEFAULT_REQUIRED_SLOTS)
    if not (isinstance(required_slots, list)
            and all(isinstance(s, str) and s.strip() for s in required_slots)):
        raise ScopeError("`required_slots` must be a list of non-empty "
                         "strings: %s" % scope_path)

    policy_title = data.get("policy_title", DEFAULT_POLICY_TITLE)
    if not isinstance(policy_title, str) or not policy_title.strip():
        raise ScopeError("`policy_title` must be a non-empty string: %s"
                         % scope_path)
    policy_title = policy_title.strip()

    sig_rule = data.get("significant_write_rule", "path-under-root")
    if sig_rule not in ("path-under-root", "always", "never"):
        raise ScopeError("`significant_write_rule` must be one of "
                         "path-under-root|always|never: %s" % scope_path)

    reground_interval = data.get("reground_interval_turns",
                                 DEFAULT_REGROUND_INTERVAL)
    if not isinstance(reground_interval, int) or reground_interval <= 0:
        raise ScopeError("`reground_interval_turns` must be a positive "
                         "integer: %s" % scope_path)

    approval_ttl = _cap_field("approval_ttl_seconds", DEFAULT_APPROVAL_TTL)

    min_engine = data.get("min_engine", "0.1.0")
    if not isinstance(min_engine, str) or semver_tuple(min_engine) is None:
        raise ScopeError("`min_engine` must be an X.Y.Z semver string: %s"
                         % scope_path)

    # RETIRED as an execution path (0.1.2): the field is still accepted and
    # still validated, so no consumer's ballast.json breaks, but ballast no
    # longer runs it — ballast.json is a plain writable file no gate covers,
    # and running an argv declared in it made one edit to that file worth
    # arbitrary command execution on every later significant write. See
    # ballast.py's regen_index docstring.
    regen = data.get("regen")
    if regen is not None and not isinstance(regen, list):
        raise ScopeError("`regen`, if present, must be a list of argv "
                         "strings (accepted but no longer executed): %s"
                         % scope_path)

    state_dir = os.path.join(scope_dir, ".ballast")
    # The STATE ROOT is the parent of the scope directory: for a scope at
    # `<state-root>/<scope-id>/` it is `<state-root>/`. The approval
    # tokens and the two gate switch files live there, shared by every
    # scope under it — including a global scope's own directory.
    state_root = os.path.dirname(scope_dir)

    return {
        "scope_path": scope_path,
        "scope_dir": scope_dir,
        "state_root": state_root,
        "root": root,
        "log_path": log_path,
        "hot_path": hot_path,
        "index_path": index_path,
        "policy_path": policy_path,
        "glossary_path": glossary_path,
        "hot_cap_bytes": hot_cap,
        "policy_cap_bytes": policy_cap,
        "policy_title": policy_title,
        "glossary_cap_bytes": glossary_cap,
        "required_slots": required_slots,
        "significant_write_rule": sig_rule,
        "reground_interval_turns": reground_interval,
        "approval_ttl_seconds": approval_ttl,
        "min_engine": min_engine,
        "regen": regen,
        "state_dir": state_dir,
        "truncated_on_read": truncated,
    }


# --- hot.md: the fixed slot template (schema v2) ---------------------------

# Schema v2's full slot table (the design's `hot.md` shape section): slot
# name, its own cap in CHARACTERS, and the value that renders when the slot
# has nothing to report. Per-slot caps exist so no one slot can crowd the
# others out of hot.md's 4 KB ceiling (Letta's per-block cap, same reason).
#
# The first nine names below are v1's and stay REQUIRED
# (DEFAULT_REQUIRED_SLOTS); the seven v2 adds — goal, waiting-on, decision,
# deadline, risk, mode, last-artifact — are OPTIONAL: an absent one is
# never a Stop-gate completion gap.
#
# "" as the empty state means "always set": the template renders the slot
# blank and the session fills it in.
HOT_SLOT_SPECS = (
    ("focus", 300, ""),
    ("goal", 300, "none"),
    ("next", 400, ""),
    ("blocked", 300, "none"),
    ("waiting-on", 300, "none"),
    ("decision", 300, "none"),
    ("done", 300, "none"),
    ("deadline", 60, "none"),
    ("risk", 200, "none"),
    ("mode", 60, "none"),
    ("last-artifact", 150, "none"),
    ("updated", 40, ""),
    ("who-acts-next", 40, ""),
    ("looping", 40, "none"),
    ("progressing", 20, "no"),
    ("stall", 10, "0"),
)
HOT_SLOT_CAPS = {name: cap for name, cap, _ in HOT_SLOT_SPECS}

# Everything after the slots (the master-node link + frozen-models pointer)
# shares ONE budget, counted across those lines together.
HOT_POINTER_CAP = 200

HOT_MARKER_V2 = "<!-- ballast:hot v2 -->"

# One loud line per trimmed slot — ballast never silently drops content.
HOT_TRIM_MARKER = "## Ballast — TRIMMED slot %s: %d chars over its %d cap"

_SLOT_LINE_RE = re.compile(r"^([A-Za-z][A-Za-z0-9 _-]*)\s*:\s?(.*)$")


def parse_hot_slots(text):
    """Parse hot.md's key:value lines into a dict, slot names normalized to
    lowercase-hyphenated (so `Who Acts Next`, `who_acts_next` and
    `who-acts-next` all address the same slot). Lines that don't match the
    `key: value` shape are ignored (hot.md may carry a leading comment or
    heading; the template is deliberately forgiving to read, strict only in
    what ballast itself ever writes)."""
    slots = {}
    if not text:
        return slots
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith("<!--"):
            continue
        m = _SLOT_LINE_RE.match(line)
        if not m:
            continue
        key = re.sub(r"[\s_]+", "-", m.group(1).strip().lower())
        slots[key] = m.group(2).strip()
    return slots


def normalize_slot_name(name):
    return re.sub(r"[\s_]+", "-", name.strip().lower())


def _declared_slot_line(raw_line):
    """True when this line is one of the slots HOT_SLOT_SPECS declares —
    i.e. evidence that the file it came from actually parses as this
    schema. A `key: value` line naming a slot v2 does not declare is NOT
    such evidence (any prose line can be shaped that way)."""
    stripped = raw_line.strip()
    if not stripped or stripped.startswith("#") or stripped.startswith("<!--"):
        return False
    m = _SLOT_LINE_RE.match(stripped)
    if m is None:
        return False
    return normalize_slot_name(m.group(1)) in HOT_SLOT_CAPS


def trim_hot_text(text):
    """Apply schema v2's PER-SLOT caps at read. Returns
    `(text, markers)` — the text with every over-cap slot value trimmed to
    its own cap, and one loud HOT_TRIM_MARKER line per trim (ballast never
    silently drops content). A slot name this schema does not declare has
    no cap and passes through untouched; the non-slot lines AFTER the last
    declared slot (the pointer line(s)) share one HOT_POINTER_CAP budget.

    The pointer budget is bounded twice over, because it is a budget for
    ONE artifact shape and hot.md on disk is not always that shape:

      * it applies only to the lines that FOLLOW the last declared slot
        line, so a prose preamble above the slots is not counted against
        it; and
      * it applies only to a file that carries at least one declared slot
        at all. A free-form hot.md — markdown prose, headings, bullets,
        no `key: value` slots (20 of the 43 files on disk when 0.1.2 was
        built) — has every one of its lines fall outside the slot shape,
        so budgeting them together would drop all but the first ~200
        characters and hand the model a stack of marker lines instead of
        its own state. Such a file is returned VERBATIM, exactly as 0.1.1
        injected it: capped at read by `hot_cap_bytes`, with that read's
        own loud "truncated at N B" header. Ballast never rewrites hot.md
        (the session reconciles), so nothing migrates those files — the
        reader has to keep working on them as they are.

    Read-side only: trimming here changes what is INJECTED, never the
    file."""
    if not text:
        return text, []
    lines = text.splitlines()
    last_slot = -1
    for index, raw_line in enumerate(lines):
        if _declared_slot_line(raw_line):
            last_slot = index
    if last_slot < 0:
        return text, []          # free-form hot.md — 0.1.1 behaviour
    markers = []
    out = []
    pointer_used = 0
    for index, raw_line in enumerate(lines):
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("<!--"):
            out.append(raw_line)
            continue
        m = _SLOT_LINE_RE.match(stripped)
        if m is not None:
            name = m.group(1).strip()
            cap = HOT_SLOT_CAPS.get(normalize_slot_name(name))
            if cap is None:
                out.append(raw_line)     # a slot v2 doesn't declare: no cap
                continue
            value = m.group(2).strip()
            if len(value) > cap:
                markers.append(HOT_TRIM_MARKER
                               % (normalize_slot_name(name),
                                  len(value) - cap, cap))
                value = value[:cap]
            out.append("%s: %s" % (name, value))
            continue
        if index < last_slot:
            out.append(raw_line)         # above the slots: not a pointer
            continue
        room = HOT_POINTER_CAP - pointer_used
        if room <= 0:
            markers.append(HOT_TRIM_MARKER
                           % ("pointer", len(stripped), HOT_POINTER_CAP))
            continue
        if len(stripped) > room:
            markers.append(HOT_TRIM_MARKER
                           % ("pointer", len(stripped) - room,
                              HOT_POINTER_CAP))
            out.append(stripped[:room])
            pointer_used = HOT_POINTER_CAP
            continue
        out.append(raw_line)
        pointer_used += len(stripped)
    return "\n".join(out), markers


def render_hot_template(slots, required_slots):
    """The fixed slot template ballast hands the session to fill in on a
    refresh (02, Resolved: "delta + a fixed slot template to fill, not a
    free rewrite").

    Schema v2 renders ALL 16 slots of HOT_SLOT_SPECS, in table order, so
    the full set stays visible even though only nine of them are required
    — an optional slot with nothing to report renders its empty state
    (`none`, `0`), not a blank the session has to guess about. `updated` is
    PRE-STAMPED with the current UTC time: the session is filling this
    template in now, so making it retype the timestamp is what left hot.md
    perpetually one write behind log.md.

    `slots` seeds any already-known values (a partial hot.md keeps what it
    has). Any extra slot the scope's hot.md or its `required_slots` carries
    beyond the table is preserved after them (additive; ballast never
    deletes a slot it doesn't own)."""
    slots = dict(slots or {})
    lines = [HOT_MARKER_V2]
    seen = set()
    for name, _cap, empty in HOT_SLOT_SPECS:
        seen.add(name)
        if name == "updated":
            lines.append("updated: %s" % now_iso())
            continue
        value = (slots.get(name) or "").strip() or empty
        lines.append("%s: %s" % (name, value))
    for name in required_slots or ():
        key = normalize_slot_name(name)
        if key in seen:
            continue
        seen.add(key)
        lines.append("%s: %s" % (name, slots.get(key, "")))
    for key, value in slots.items():
        if key not in seen:
            lines.append("%s: %s" % (key, value))
    return "\n".join(lines) + "\n"


def missing_required_slots(slots, required_slots):
    missing = []
    for name in required_slots:
        key = normalize_slot_name(name)
        if not slots.get(key, "").strip():
            missing.append(name)
    return missing


# --- log.md: last entry + delta --------------------------------------------

def last_log_timestamp(log_path):
    """The newest timestamp found in log.md, scanning from the end (a log
    can be long; this never reads more than a small tail). None if the file
    is absent/empty/unparsable — treated by callers as "nothing to compare
    against yet," never as "definitely stale."""
    if not log_path:
        return None
    try:
        with open(log_path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            read_from = max(0, size - 8192)
            f.seek(read_from)
            tail = f.read().decode("utf-8", errors="replace")
    except OSError:
        return None
    for line in reversed(tail.splitlines()):
        ts = parse_ts(line)
        if ts is not None:
            return ts
    return None


def log_delta(log_path, since_ts, cap_bytes=8192):
    """Lines in log.md whose own timestamp is strictly after `since_ts`
    (or every line in the window, if `since_ts` is None — nothing to
    compare against yet). A delta larger than the window is a scope that
    has gone very stale, and the tail is still the most relevant part to
    hand over.

    Reads at most the tail `cap_bytes` of the file, and never more: log.md
    is append-only with no cap and no rotation BY DESIGN (the live
    consumer's are already hundreds of KB), while this runs inside a hook
    under a few seconds' timeout — so seeking to the tail is the read, not
    a slice applied after slurping the whole file in. The slice is taken
    in BYTES, and a window that starts mid-line drops that partial first
    line rather than handing the model a fragment with no timestamp."""
    if not log_path:
        return []
    try:
        with open(log_path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            read_from = max(0, size - cap_bytes)
            handle.seek(read_from)
            raw = handle.read()
    except OSError:
        return []
    text = raw.decode("utf-8", errors="replace")
    if read_from > 0:
        newline = text.find("\n")
        text = text[newline + 1:] if newline >= 0 else ""
    out = []
    for line in text.splitlines():
        if not line.strip():
            continue
        ts = parse_ts(line)
        if since_ts is None or ts is None or ts > since_ts:
            out.append(line)
    return out


# --- freshness gate ---------------------------------------------------------

def hot_is_stale(scope):
    """The corrected freshness gate (04g Build notes): compare hot's own
    `updated` slot against log.md's newest entry — never a file mtime.

    * No log.md at all -> not stale (nothing has happened to fall behind).
    * log.md exists, no hot.md (or hot.md has no parseable `updated`) ->
      stale (there is nothing to be caught up, trivially behind).
    * Both parse -> stale iff the log's newest timestamp is strictly after
      hot's `updated` timestamp.
    Log.md, by construction, only ever receives significant-write entries
    (see `is_significant`), so this already implements "reads never
    trigger it" (02, Resolved) with no separate read/write bookkeeping.

    A scope that disables hot ("hot": null) has nothing that can fall
    behind the log, so it is never stale.
    """
    if not scope["hot_path"]:
        return False
    log_ts = last_log_timestamp(scope["log_path"])
    if log_ts is None:
        return False
    hot_text, _ = read_text(scope["hot_path"])
    # A4/R1-1 — a PRESENT free-form hot.md (no declared slots, the escape
    # hatch trim_hot_text and completion_gaps already recognise) has no
    # `updated` slot to fall behind, so it can never be "stale". Without
    # this, its missing `updated` reads as stale below and the Stop gate
    # would BLOCK a scope that legitimately keeps hot.md as prose (A2 flags
    # it for conversion at SessionStart; it must never Stop-nag). An ABSENT
    # hot.md is a different case and stays stale via the `hot_ts is None`
    # branch below.
    if hot_text is not None and not any(
            _declared_slot_line(line) for line in hot_text.splitlines()):
        return False
    slots = parse_hot_slots(hot_text)
    hot_ts = parse_ts(slots.get("updated", ""))
    if hot_ts is None:
        return True
    return log_ts > hot_ts


def completion_gaps(scope):
    """Required hot slots that are missing or empty right now — the
    completion check (X6), independent of the freshness gate: a session
    can be perfectly caught up to the log and still have left a required
    slot blank (e.g. never set `next` at all).

    A scope that disables hot ("hot": null) has no slots to complete.
    """
    if not scope["hot_path"]:
        return []
    hot_text, _ = read_text(scope["hot_path"])
    # A4 — a PRESENT free-form hot.md (no declared slots, the escape hatch
    # trim_hot_text already recognises) has no slots to complete, so it can
    # never be a completion gap. Without this, required_slots would report
    # every slot missing and Stop would block a scope that legitimately
    # keeps hot.md as prose. (A2 flags such a file for conversion at
    # SessionStart; it must never BLOCK here.) An ABSENT hot.md is a
    # different case and keeps its prior behavior.
    if hot_text is not None and not any(
            _declared_slot_line(line) for line in hot_text.splitlines()):
        return []
    slots = parse_hot_slots(hot_text)
    return missing_required_slots(slots, scope["required_slots"])


# --- A2: the broken-artifact catch (detect only; the session repairs) ------
# A PRESENT-but-malformed strict-shape artifact is flagged ONCE at
# SessionStart with a loud stdout NOTE (fail-open: never stderr, never a
# Stop-block, never a completion gap). An ABSENT file is never flagged.
# Ballast only DETECTS here; the session performs the repair when the user
# says yes, using the existing writers (hot.md via render_hot_template,
# glossary.md via glossary.py). Read-only, never raises.

# glossary.md's one legal entry shape — the same `- **term** — gloss` line
# scripts/glossary.py writes and addresses entries by.
GLOSSARY_ENTRY_RE = re.compile(r"^-\s+\*\*[^*]+\*\*\s*(?:—|--|-)\s*.*$")


def hot_is_malformed(scope):
    """True when hot.md is PRESENT but not in the current v2 slot shape:
    missing the `<!-- ballast:hot v2 -->` marker, OR parsing to zero
    declared slots (a free-form / pre-v2 file). An ABSENT or unreadable
    hot.md is NOT malformed (missing is fine; unreadable is an availability
    concern, not a shape one)."""
    path = scope.get("hot_path")
    if not path or not os.path.isfile(path):
        return False
    text, _ = read_text(path, cap_bytes=scope["hot_cap_bytes"])
    if text is None:
        return False
    if HOT_MARKER_V2 not in text:
        return True
    return not any(_declared_slot_line(line) for line in text.splitlines())


def glossary_malformed_lines(scope):
    """The 1-indexed line numbers of glossary.md lines that break its entry
    shape — a present-but-malformed glossary is flagged with them. A blank
    line or a markdown heading (`#`) is allowed; every other line must be
    `- **term** — gloss`. An ABSENT or unreadable glossary returns []."""
    path = scope.get("glossary_path")
    if not path or not os.path.isfile(path):
        return []
    text, _ = read_text(path, cap_bytes=scope["glossary_cap_bytes"])
    if text is None:
        return []
    bad = []
    for index, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if not GLOSSARY_ENTRY_RE.match(line):
            bad.append(index)
    return bad


def policy_unreadable(scope):
    """True when the scope's policy file is PRESENT but cannot be read (an
    OS-level fault), so its standing rules would silently not load. An
    absent or readable policy is never flagged. Availability-only — there
    is no shape to check on free-text policy."""
    path = scope.get("policy_path")
    if not path or not os.path.isfile(path):
        return False
    text, _ = read_text(path, cap_bytes=scope["policy_cap_bytes"])
    return text is None


# --- significant-write rule -------------------------------------------------

_PATH_KEYS = ("file_path", "path", "notebook_path")


def _extract_paths(tool_input):
    if not isinstance(tool_input, dict):
        return []
    out = []
    for key in _PATH_KEYS:
        val = tool_input.get(key)
        if isinstance(val, str) and val.strip():
            out.append(val)
    return out


# The only tools whose effect on files ballast can know from the payload:
# each names its target in file_path/path/notebook_path. A Bash or
# PowerShell command does NOT qualify — its command text cannot be parsed
# for a target generically, and guessing is what produced the every-turn
# Stop nag (A5): a read-only `git status` appended a log.md line, which put
# log.md ahead of hot.md's `updated`, which blocked the next stop.
WRITE_TOOL_NAMES = ("write", "edit", "notebookedit")


def is_significant(scope, payload):
    """Whether this PostToolUse invocation counts as a significant write
    under this scope's `significant_write_rule` (02, Resolved: "a
    file-changing write under a scope root counts; reads don't.
    Overridable per scope.").

    `always`   — every invocation this handler is called for counts. Used
                 when the consumer's own hooks.json matcher already scopes
                 PostToolUse to write-shaped tools only, and the consumer
                 accepts that a command's effect is assumed rather than
                 known.
    `never`    — nothing is ever significant (log-append + index-regen
                 both skip). A scope that wants pure manual/log-free
                 tracking, and what the global scope declares.
    `path-under-root` (default) — significant only when BOTH hold: the
                 tool is one of WRITE_TOOL_NAMES, and its own input names a
                 path (file_path/path/notebook_path) resolving under this
                 scope's root — by `is_under`, the same canonicalizing
                 containment check the approval gate uses, so a case- or
                 symlink-differing spelling of the same file cannot read
                 as "outside" and silently skip the log line the Stop gate
                 later depends on. Everything else — a Bash or PowerShell
                 command, a read, a tool that named no path, an invocation
                 carrying no tool_name at all — is NOT significant. Only
                 actual saves may move log.md ahead of hot.md, because
                 that gap is exactly what the Stop gate blocks on.
    """
    rule = scope["significant_write_rule"]
    if rule == "never":
        return False
    if rule == "always":
        return True
    tool_name = payload.get("tool_name")
    if not isinstance(tool_name, str) or \
            tool_name.strip().lower() not in WRITE_TOOL_NAMES:
        return False
    tool_input = payload.get("tool_input")
    paths = _extract_paths(tool_input)
    if not paths:
        return False   # a write tool that named no target: nothing to log
    cwd = payload.get("cwd") or os.getcwd()
    for p in paths:
        candidate = p if os.path.isabs(p) else os.path.join(cwd, p)
        if is_under(candidate, scope["root"]):
            return True
    return False


# --- the approval gate (A9) -------------------------------------------------

def canonical_target(path):
    """The one spelling of a target path both the minter and the gate
    agree on: symlinks resolved, case/separator normalized. The consuming
    vault is reachable under two paths (a symlink), so comparing raw
    strings would let a token minted through one spelling miss the write
    that arrives through the other."""
    return os.path.normcase(os.path.realpath(os.path.abspath(path)))


def token_name(path):
    """The token filename for a target: sha1 of its canonical path. A
    hash, not the path itself, so the token directory never becomes a
    listing of what someone is about to edit."""
    import hashlib
    digest = hashlib.sha1(canonical_target(path).encode("utf-8")).hexdigest()
    return digest + ".token"


def approvals_dir(state_root):
    return os.path.join(state_root, APPROVALS_DIRNAME)


def find_approvals_dir(target_path):
    """The nearest existing approvals directory at or above a target's own
    directory, or None. Lets `approve.py check` work from a bare `--file`,
    with no scope to resolve the state root from; the deepest match wins,
    so a stray directory further up cannot shadow the real one."""
    current = os.path.dirname(canonical_target(target_path))
    while True:
        candidate = os.path.join(current, APPROVALS_DIRNAME)
        if os.path.isdir(candidate):
            return candidate
        parent = os.path.dirname(current)
        if parent == current:
            return None
        current = parent


def gate_disabled(state_root):
    return os.path.isfile(os.path.join(state_root, GATE_DISABLED_FILENAME))


def glossary_gate_disabled(state_root):
    return os.path.isfile(os.path.join(state_root,
                                       GLOSSARY_GATE_DISABLED_FILENAME))


def mint_approval(state_root, target_path, field=None):
    """Write the one-time approval token for `target_path`. Returns its
    path. Honors DRY_RUN.

    Clears any claim marker left by the PREVIOUS approval for this target
    first (see `consume_approval`): minting is the act that says a new
    approval exists, so the old claim must not still be standing in front
    of it. A cleared-then-not-yet-written moment can only ever cost a
    refusal, never an unapproved write."""
    directory = approvals_dir(state_root)
    path = os.path.join(directory, token_name(target_path))
    if not DRY_RUN:
        try:
            os.unlink(claim_path(path))
        except OSError:
            pass
    write_text_atomic(path, json.dumps({
        "minted": now_iso(),
        "target": canonical_target(target_path),
        "field": field,
    }, ensure_ascii=False))
    return path


def read_approval(target_path, state_root=None):
    """The token record for a target, or None. `state_root` is
    authoritative when given; otherwise the nearest approvals directory
    above the target is used."""
    directory = approvals_dir(state_root) if state_root \
        else find_approvals_dir(target_path)
    if not directory:
        return None
    path = os.path.join(directory, token_name(target_path))
    text, _ = read_text(path, cap_bytes=8192)
    if text is None:
        return None
    try:
        data = json.loads(text)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    data["_path"] = path
    return data


def approval_is_fresh(record, ttl_seconds):
    """True when a token exists and was minted within the TTL. An
    unparsable or future-dated stamp is NOT fresh — a permission slip that
    cannot be read is not a permission slip."""
    if not record:
        return False
    minted = parse_ts(record.get("minted", ""))
    if minted is None:
        return False
    age = (datetime.datetime.now(datetime.timezone.utc) - minted)\
        .total_seconds()
    return 0 <= age <= ttl_seconds


CLAIM_SUFFIX = ".claimed"


def claim_path(token_path):
    """The claim marker beside a token. Not a `.token` file, so it never
    reads as an approval itself (`read_approval` looks a token up by its
    exact name, and /ballast:check counts only `*.token`)."""
    return token_path + CLAIM_SUFFIX


def consume_approval(record):
    """Claim a token, then delete it. One approval, one write — never a
    standing pass, and never TWO writes on one token.

    The claim is an EXCLUSIVE CREATE of a marker file beside the token,
    not the delete, because on win32 the delete is not a claim: measured
    on the supported host, 20 races of two processes deleting one file
    reported success to BOTH in 5 of them (renaming to a unique name was
    worse, 8 of 20). `os.open(O_CREAT|O_EXCL)` was the one operation that
    picked exactly one winner in all 20 — it is the classic atomic
    primitive on both platforms, and the loser gets FileExistsError.

    The marker outlives the token deliberately: a racer that already read
    the same fresh record must still lose after the winner has cleaned
    up. `mint_approval` clears it, so the NEXT approval for the same
    target starts unclaimed — at most one small marker per gated target
    is ever left on disk."""
    path = (record or {}).get("_path")
    if not path or DRY_RUN:
        return False
    try:
        handle = os.open(claim_path(path),
                         os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except OSError:
        return False          # already claimed (or unclaimable) — no approval
    try:
        os.write(handle, now_iso().encode("utf-8"))
    except OSError:
        pass
    finally:
        os.close(handle)
    try:
        os.unlink(path)
    except OSError:
        pass
    return True


def gated_filename(path):
    """The gated filename this path is, or None. `glossary.md` is returned
    like the others; whether its gate is ARMED is the caller's call (it has
    its own switch)."""
    if not path:
        return None
    name = os.path.basename(str(path)).strip().lower()
    return name if name in GATED_FILENAMES else None


def is_under(path, root):
    """True when `path` resolves inside `root`. Both canonicalized, so a
    symlinked vault does not read as "outside"."""
    if not path or not root:
        return False
    candidate = canonical_target(path)
    base = canonical_target(root)
    try:
        return os.path.commonpath([candidate, base]) == base
    except ValueError:
        return False   # different drives on Windows


NO_APPROVAL = (
    "refused: no fresh approval for %s.\n"
    "This file is gated. Show the operator the exact text that will be written, "
    "wait for their explicit yes, then:\n"
    "  approve.py mint --scope %s --file %s\n"
    "and run this command again. An approval is one-time and expires after "
    "%d seconds."
)


def authorize_write(scope, target_path):
    """The gate check every write primitive runs before touching a gated
    file. Returns `(allowed, message)` and CONSUMES the token on success —
    one approval, one write.

    A path that is not one of the gated filenames is allowed without a
    token: this function guards the three, and is not a general
    permission system."""
    state_root = scope["state_root"]
    name = gated_filename(target_path)
    if name is None:
        return True, "not a gated file"
    if gate_disabled(state_root):
        return True, ("the approval gate is OFF (%s exists under %s)"
                      % (GATE_DISABLED_FILENAME, state_root))
    if name == GLOSSARY_FILENAME and glossary_gate_disabled(state_root):
        return True, ("the glossary gate is OFF (%s exists under %s)"
                      % (GLOSSARY_GATE_DISABLED_FILENAME, state_root))
    record = read_approval(target_path, state_root)
    # The delete is the claim (see cmd_pre_tool_use): a consume that comes
    # back False means another writer already took this one-time token, so
    # there is no approval left for THIS write.
    if approval_is_fresh(record, scope["approval_ttl_seconds"]) \
            and (DRY_RUN or consume_approval(record)):
        return True, "approval consumed"
    return False, (NO_APPROVAL % (target_path, scope["scope_path"],
                                  target_path,
                                  scope["approval_ttl_seconds"]))


def log_primitive(scope, message):
    """One log.md line per primitive write. The log is where a change's
    REASON lives — an entry itself carries only the entry."""
    append_text(scope["log_path"], "- %s %s" % (now_iso(), message))


# --- per-session bookkeeping (re-ground cadence, post-compact dedup) -------

def _session_state_path(scope, session_id):
    return os.path.join(scope["state_dir"], "session", session_id + ".json")


def read_session_state(scope, session_id):
    if not safe_session_id(session_id):
        return {"turns": 0, "pending_compact_reground": False}
    path = _session_state_path(scope, session_id)
    text, _ = read_text(path, cap_bytes=8192)
    if text is None:
        return {"turns": 0, "pending_compact_reground": False}
    try:
        data = json.loads(text)
    except ValueError:
        return {"turns": 0, "pending_compact_reground": False}
    if not isinstance(data, dict):
        return {"turns": 0, "pending_compact_reground": False}
    return {
        "turns": data.get("turns", 0) if isinstance(data.get("turns"), int) else 0,
        "pending_compact_reground": bool(data.get("pending_compact_reground", False)),
    }


def write_session_state(scope, session_id, state):
    if not safe_session_id(session_id):
        return
    path = _session_state_path(scope, session_id)
    write_text_atomic(path, json.dumps(state, ensure_ascii=False))


def reset_session_state(scope, session_id):
    """SessionStart's own dedup step (mirrors 04f's "boot clears the turn
    counter on every fire" for the consumer's remind — ballast applies the
    same trick to its own re-ground cadence): a fresh boot means the first
    ordinary prompt should not immediately re-ground just because a stale
    counter says so."""
    write_session_state(scope, session_id,
                        {"turns": 0, "pending_compact_reground": False})


def mark_pending_compact_reground(scope, session_id):
    state = read_session_state(scope, session_id)
    state["pending_compact_reground"] = True
    write_session_state(scope, session_id, state)
