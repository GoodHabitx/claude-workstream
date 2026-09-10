#!/usr/bin/env python3
# claude-ballast — the continuity engine.
"""ballast.py EVENT --scope PATH — the one entry point every consumer's shim
forwards to.

Usage
-----
    <interpreter> ballast.py EVENT --scope PATH/to/ballast.json
                  [--part NAME] [--dry-run]

EVENT uses Claude Code's own hook_event_name spelling (case-insensitive;
hyphen/underscore variants also accepted so a hand-written hooks.json line
is forgiving) — the five events the model page names, plus PreToolUse,
which the approval gate added in 0.1.2:
    SessionStart | UserPromptSubmit | PreToolUse | PostToolUse |
    PreCompact | Stop

`--part all|hot|policy|glossary` splits SessionStart's delivery
so a consumer can put each part on its own hook command with its own
~9,687 B inline envelope, instead of hot.md and policy.md contending for
one. It DEFAULTS to `all` — exactly the pre-split combined output — so an
un-updated consumer's hook line keeps working unchanged. The delta always
rides `hot` and appears in no other part (it is computed from hot.md's own
`updated` slot). Ordering between parts is not guaranteed and nothing
depends on it: every block opens with a stable self-identifying heading.
The flag is accepted and ignored on the other four events, which have one
delivery each.

`--dry-run` suppresses every durable write this run would otherwise make
(log.md append, index.md regen, session-state bookkeeping) while still
running the full read/decide path and printing exactly what a real
invocation would print — a smoke test for a newly-wired scope (see the
README's Verify section) with no side effects.

Stdin: the hook's own JSON payload (session_id, cwd, source, tool_name,
tool_input, tool_response, hook_event_name, ...) — read via
ballast_lib.read_stdin_json (empty/malformed/absent all degrade to `{}`,
never raise).

Contract with every caller (the shim, and — if a consumer ever calls this
directly — the consumer itself): FAIL OPEN. Any internal error, at any
event, prints ONE line to stderr, one loud NOT LOADED line on stdout, and
exits 0 — this engine must never be the reason a session's turn is
blocked, its compaction stalls, or its startup fails. There are exactly
two deliberate exceptions, both gates doing their documented job:

  * Stop's `{"decision": "block", ...}` JSON, which always allows on a
    second consecutive attempt (the harness's own `stop_hook_active`).
  * PreToolUse's approval refusal, the one path that exits NON-zero: the
    hook protocol's exit 2 with the reason on stderr. It is deliberately
    fail-CLOSED, for two filenames only, and two switch files disarm it.

Every other path exits 0 (bar total interpreter failure below main()).
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ballast_lib as bl

EVENT_ALIASES = {
    "sessionstart": "SessionStart",
    "session-start": "SessionStart",
    "session_start": "SessionStart",
    "userpromptsubmit": "UserPromptSubmit",
    "user-prompt-submit": "UserPromptSubmit",
    "user_prompt_submit": "UserPromptSubmit",
    "posttooluse": "PostToolUse",
    "post-tool-use": "PostToolUse",
    "post_tool_use": "PostToolUse",
    "pretooluse": "PreToolUse",
    "pre-tool-use": "PreToolUse",
    "pre_tool_use": "PreToolUse",
    "precompact": "PreCompact",
    "pre-compact": "PreCompact",
    "pre_compact": "PreCompact",
    "stop": "Stop",
}


def _note(msg):
    """The one stderr line an error path is allowed — never more than one
    per invocation, and never anything on stdout (stdout is reserved for
    real hook output: additionalContext text, or Stop's decision JSON)."""
    try:
        sys.stderr.write("ballast: %s\n" % msg)
    except Exception:
        pass


# The name this invocation's delivery goes by in a guard or NOT LOADED
# marker — the event, or "<event> --part <part>" once a delivery is split.
# main() sets it; it is only ever read for those two messages.
DELIVERY_LABEL = "delivery"


def _out(text, label=None):
    """Write ONE delivery to stdout, guarded (A4): over the byte budget it
    is trimmed with a loud marker rather than silently handed to the
    harness's all-or-nothing file-persist fallback."""
    if text:
        guarded = bl.guard_delivery(text, label or DELIVERY_LABEL)
        sys.stdout.write(guarded if guarded.endswith("\n") else guarded + "\n")


def _emit_decision(obj, label=None):
    """Stop's own decision JSON — the one delivery that must stay
    parseable, so the guard budgets the `reason` field instead of trimming
    the serialized line. Re-guards the ORIGINAL reason each round (never
    a marker stacked on a marker) against a budget that shrinks by the
    measured overflow until the whole line fits."""
    limit = bl.delivery_limit()
    original = obj.get("reason")
    if isinstance(original, str):
        reason = original
        for _ in range(6):
            candidate = dict(obj)
            candidate["reason"] = reason
            over = len(json.dumps(candidate, ensure_ascii=False)
                       .encode("utf-8")) - limit
            if over <= 0:
                break
            reason = bl.guard_delivery(
                original, label or DELIVERY_LABEL,
                budget=len(original.encode("utf-8")) - over)
        obj = dict(obj)
        obj["reason"] = reason
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _not_loaded(label, exc):
    """A4's fail-open-but-LOUD contract. An exception inside a handler
    still puts ONE line in front of the model naming what did not load and
    why — a stderr line alone is invisible to it, which is how a dropped
    artifact used to look exactly like an empty one."""
    try:
        sys.stdout.write("## Ballast — %s NOT LOADED: %s: %s\n"
                         % (label, type(exc).__name__, exc))
    except Exception:
        pass


# --- SessionStart ------------------------------------------------------

# A1 — the split. Each part is a SEPARATE hook command in the consumer's
# hooks.json, so each claims its own ~9,687 B inline envelope instead of
# hot.md and policy.md contending for one. `all` is the default and is
# exactly the pre-split combined output, so an un-updated 0.1.1 consumer
# keeps working unchanged.
SESSION_START_PARTS = ("all", "hot", "policy", "glossary")

# The part this invocation was asked for; main() sets it from --part.
PART = "all"

# A2 — the broken-artifact catch. A PRESENT-but-malformed strict-shape
# artifact gets ONE loud stdout NOTE at SessionStart (never stderr, never
# exit 2, never a Stop-block, never a completion gap) telling the session
# it can repair it — content preserved — on the user's yes. Each note
# rides the part that owns its artifact, so it fires once per session
# start, never per turn, and never on a UserPromptSubmit reground.
BROKEN_HOT_NOTE = (
    "## Ballast — hot.md needs conversion\n"
    "`%s` predates the current slot format — reply to convert it (your "
    "notes are preserved).")
BROKEN_GLOSSARY_NOTE = (
    "## Ballast — glossary.md malformed\n"
    "`%s` has %d line(s) that are not `- **term** — gloss` (line(s): %s) — "
    "reply to convert it (your entries are preserved).")
BROKEN_POLICY_NOTE = (
    "## Ballast — policy unreadable\n"
    "`%s` is present but could not be read, so its standing rules did not "
    "load this session — check its permissions and encoding.")


def cmd_session_start(scope, payload):
    """Inject this invocation's PART (02: "SessionStart -> inject hot +
    policy; on compact/resume, if stale the first action is a refresh").
    Also resets this session's re-ground counter (04f's
    boot-clears-the-counter trick, applied to ballast's own cadence) —
    per A3, exactly ONE part owns that reset (`hot` in the split, `all`
    in the un-split case), never every part: on win32, two parts racing
    to os.replace the same session-state file throws WinError 5 (the
    boot collision), so a single writer touches it and the counter still
    resets once at every real boot.

    Ordering between parts is NOT guaranteed: same-event hook commands run
    in parallel with no declared order, and nothing here depends on one.
    That is why every block opens with a stable self-identifying heading —
    the blocks read correctly in any order.

    The delta ALWAYS rides `hot` and appears in no other part: it is
    computed from hot.md's own `updated` slot, so splitting it away from
    hot.md would orphan it."""
    if PART not in SESSION_START_PARTS:
        raise ValueError("unknown --part %r (expected one of %s)"
                         % (PART, "|".join(SESSION_START_PARTS)))

    # A3 — exactly ONE part owns the session-state reset/write, so the
    # parallel SessionStart part-processes do not all race to os.replace
    # the same <scope>/.ballast/session/<id>.json (on win32, one process
    # replacing a file another holds open throws WinError 5 — the boot
    # collision). `hot` owns it in the split; `all` owns it in the
    # un-split single-command case. A session runs ONE of those, never
    # both, so exactly one writer touches the file — and the reground
    # counter is still reset once at every real boot.
    session_id = payload.get("session_id")
    if PART in ("all", "hot") and bl.safe_session_id(session_id):
        bl.reset_session_state(scope, session_id)

    def wanted(part):
        return PART in ("all", part)

    blocks = []

    if wanted("hot"):
        # A2 — flag a present-but-malformed hot.md once (rides the hot
        # part), then still inject whatever it holds verbatim.
        if bl.hot_is_malformed(scope):
            blocks.append(BROKEN_HOT_NOTE % _rel(scope, scope["hot_path"]))
        hot_text, hot_markers, hot_truncated = _read_hot(scope)
        if hot_text is not None:
            header = "## Hot state"
            if hot_truncated:
                header += " (truncated at %d B — consolidate on next refresh)" % scope["hot_cap_bytes"]
            blocks.append("\n".join([header] + hot_markers +
                                    [hot_text.strip()]))

    if wanted("policy"):
        # A2 — flag a present-but-unreadable policy once (rides the policy
        # part), then still inject it when it reads. Shares the shape of
        # hot_is_malformed / glossary_malformed_lines: a boolean helper
        # flags, a separate read injects.
        if bl.policy_unreadable(scope):
            blocks.append(BROKEN_POLICY_NOTE
                          % _rel(scope, scope["policy_path"]))
        if scope["policy_path"] and os.path.isfile(scope["policy_path"]):
            policy_text, policy_truncated = bl.read_text(
                scope["policy_path"], cap_bytes=scope["policy_cap_bytes"])
            # An empty (or whitespace-only) policy injects nothing — no bare
            # `## <title>` heading for a scope with no rules yet (matches the
            # glossary's own empty-skip below). An ABSENT policy is already
            # skipped by the isfile guard above.
            if policy_text is not None and policy_text.strip():
                header = "## " + scope["policy_title"]
                if policy_truncated:
                    header += (" (TRUNCATED at %d B — this policy.md is over "
                              "cap; edit it down, ballast never silently drops "
                              "a rule without saying so)" % scope["policy_cap_bytes"])
                lint_notes = lint_policy_contradictions(policy_text)
                if lint_notes:
                    header += "\n(lint: " + "; ".join(lint_notes) + ")"
                blocks.append(header + "\n" + policy_text.strip())

    source = payload.get("source")
    if wanted("hot") and source in ("compact", "resume") \
            and bl.hot_is_stale(scope):
        blocks.append(
            "## Ballast — refresh needed\n"
            "hot.md is behind this scope's log.md (source: %s). Before "
            "anything else this session: refresh `%s` from the log delta "
            "below, then continue.\n\n%s"
            % (source, _rel(scope, scope["hot_path"]),
               _refresh_template(scope))
        )
        delta = bl.log_delta(scope["log_path"], _hot_updated_ts(scope),
                             cap_bytes=bl.DEFAULT_DELTA_CAP)
        if delta:
            blocks.append("## Log delta since last refresh\n" +
                          "\n".join(delta[-40:]))

    if wanted("glossary"):
        # A2 — flag a present-but-malformed glossary once (rides the
        # glossary part), then still inject whatever it holds.
        bad_lines = bl.glossary_malformed_lines(scope)
        if bad_lines:
            blocks.append(BROKEN_GLOSSARY_NOTE
                          % (_rel(scope, scope["glossary_path"]),
                             len(bad_lines),
                             ", ".join(str(n) for n in bad_lines)))
        glossary = _glossary_block(scope)
        if glossary:
            blocks.append(glossary)

    _out("\n\n".join(blocks))
    return 0


GLOSSARY_HEADER = "## Glossary"


def _glossary_block(scope):
    """glossary.md, injected whole under its own cap — the terms, names
    and concepts this scope pins so they stay unambiguous across sessions
    and compactions. One line per entry.

    Cadence follows policy.md, not hot.md: every SessionStart source
    INCLUDING compact/resume, and NOT PreCompact (it re-injects at the
    compact-source SessionStart, so carrying it through the summarizer
    would only pay for it twice).

    A missing or empty glossary.md is not a warning — most scopes have
    none. Returns None when there is no block to emit."""
    path = scope["glossary_path"]
    if not path or not os.path.isfile(path):
        return None
    text, truncated = bl.read_text(path,
                                   cap_bytes=scope["glossary_cap_bytes"])
    if text is None or not text.strip():
        return None
    header = GLOSSARY_HEADER
    if truncated:
        header += (" (TRUNCATED at %d B — this glossary.md is over cap; "
                   "edit it down, ballast never silently drops an entry "
                   "without saying so)" % scope["glossary_cap_bytes"])
    return header + "\n" + text.strip()


def _read_hot(scope):
    """hot.md exactly as ballast injects it: read under the file cap, then
    per-slot trimmed (schema v2). Returns `(text, markers, truncated)`;
    `text` is None when hot.md is absent or unreadable, and `markers` are
    the loud per-slot trim lines to print above it."""
    text, truncated = bl.read_text(scope["hot_path"],
                                   cap_bytes=scope["hot_cap_bytes"])
    if text is None:
        return None, [], False
    trimmed, markers = bl.trim_hot_text(text)
    return trimmed, markers, truncated


def _hot_updated_ts(scope):
    hot_text, _ = bl.read_text(scope["hot_path"])
    slots = bl.parse_hot_slots(hot_text)
    return bl.parse_ts(slots.get("updated", ""))


def _refresh_template(scope):
    """The fixed slot template a refresh fills in — printed in the
    compact/resume refresh block and in the Stop-gate message, so the
    session never has to remember the slot set. `updated` arrives already
    stamped (schema v2), which is what stops hot.md sitting one write
    behind log.md forever."""
    hot_text, _ = bl.read_text(scope["hot_path"])
    slots = bl.parse_hot_slots(hot_text)
    return ("Refresh template for `%s` — every slot, `updated` already "
            "stamped; keep what is still true, and write `none` for a slot "
            "with nothing to report:\n%s"
            % (_rel(scope, scope["hot_path"]),
               bl.render_hot_template(slots, scope["required_slots"]).strip()))


def _rel(scope, path):
    if not path:
        return "(disabled in this scope)"
    try:
        return os.path.relpath(path, scope["root"]).replace(os.sep, "/")
    except ValueError:
        return path


# --- policy contradiction lint (B4) -------------------------------------

_NEGATORS = ("never", "no ", "not ", "don't", "do not")
_AFFIRMERS = ("always", "must ")


def lint_policy_contradictions(text):
    """Best-effort MECHANICAL lint over policy.md's rule lines (B4: "ballast
    lints the injected policy set for contradictions rather than assuming
    a precedence"). This is deliberately NOT semantic understanding — it
    flags two classes a hook can catch cheaply and honestly:
      1. exact duplicate rule lines (redundant, likely drift from an edit
         that appended instead of replacing);
      2. a pair of lines that are textually identical once one negator
         word/phrase is stripped from one of them and an affirmer from the
         other (e.g. "never commit secrets" / "always commit secrets") —
         a narrow, literal heuristic, not a general negation detector.
    Returns a short list of human-readable notes; empty list = nothing
    flagged. Never raises."""
    try:
        lines = [ln.strip("-* \t") for ln in text.splitlines()
                if ln.strip().startswith(("- ", "* "))]
        notes = []
        seen = {}
        for ln in lines:
            key = ln.lower()
            if key in seen:
                notes.append("duplicate rule: %r" % ln[:60])
            seen[key] = True

        def _strip_one(s, words):
            low = s.lower()
            for w in words:
                if w in low:
                    return low.replace(w, "", 1).strip()
            return None

        for a in lines:
            stripped_a = _strip_one(a, _NEGATORS)
            if stripped_a is None:
                continue
            for b in lines:
                if a is b:
                    continue
                stripped_b = _strip_one(b, _AFFIRMERS)
                if stripped_b is None:
                    continue
                if _squash(stripped_a) == _squash(stripped_b):
                    notes.append("possible contradiction: %r vs %r"
                                % (a[:50], b[:50]))
        return notes[:5]   # a lint note is a nudge, not a wall of text
    except Exception:
        return []


def _squash(s):
    return "".join(s.split())


# --- UserPromptSubmit ----------------------------------------------------

def cmd_user_prompt_submit(scope, payload):
    """re-ground every K turns (goal + ledger), plus the mandatory
    post-compaction re-ground; a stale-hot nudge (never a block — only
    Stop blocks) rides the same slot when it fires (02: "UserPromptSubmit
    -> every K turns re-ground; nudge if hot is stale"; X4: ballast's
    re-ground goes FIRST on the shared UserPromptSubmit slot, the
    consumer's own `remind` second)."""
    session_id = payload.get("session_id")
    if not bl.safe_session_id(session_id):
        return 0   # no session to key state on -> nothing to ground

    state = bl.read_session_state(scope, session_id)
    state["turns"] += 1
    should_reground = (state["pending_compact_reground"]
                       or state["turns"] >= scope["reground_interval_turns"])

    if not should_reground:
        bl.write_session_state(scope, session_id, state)
        return 0

    hot_text, hot_markers, _ = _read_hot(scope)
    blocks = ["## Ballast re-ground"]
    if hot_text:
        blocks.extend(hot_markers)
        blocks.append(hot_text.strip())
    else:
        blocks.append("(no hot.md yet at %s)" % _rel(scope, scope["hot_path"]))
    if bl.hot_is_stale(scope):
        blocks.append(
            "Nudge: hot.md looks behind log.md — consider refreshing it "
            "(this is a nudge, not a block; Stop will gate on it if it's "
            "still stale then)."
        )
    _out("\n".join(blocks))

    bl.write_session_state(scope, session_id,
                           {"turns": 0, "pending_compact_reground": False})
    return 0


# --- PostToolUse -----------------------------------------------------------

def cmd_post_tool_use(scope, payload):
    """Append one log line on a significant write; regenerate index.md
    silently (02: "PostToolUse -> append significant writes to log.md;
    regenerate index.md silently"). Never prints anything — PostToolUse
    stdout is not surfaced to the model the way SessionStart/
    UserPromptSubmit stdout is, so this handler's only job is the side
    effect, not text output."""
    if not bl.is_significant(scope, payload):
        return 0

    ts = bl.now_iso()
    tool_name = payload.get("tool_name") or "?"
    paths = bl._extract_paths(payload.get("tool_input"))
    detail = paths[0] if paths else (
        (payload.get("tool_input") or {}).get("command", "")[:120]
        if isinstance(payload.get("tool_input"), dict) else "")
    response = payload.get("tool_response")
    failed = isinstance(response, dict) and response.get("error")
    status = " (error)" if failed else ""
    line = "- %s [%s]%s %s" % (ts, tool_name, status, detail)
    bl.append_text(scope["log_path"], line)

    regen_index(scope)
    return 0


def regen_index(scope):
    """Regenerate index.md with ballast's own minimal built-in generator:
    a flat, sorted listing of every file under root (excluding ballast's
    own bookkeeping dir and the class files themselves) with size + mtime.
    Any failure here is swallowed to stderr — index regen is class-gate
    {ok:none}, it must never affect the log-append this handler already
    committed. Honors `--dry-run` (bl.DRY_RUN).

    A scope's own `regen` argv is accepted in ballast.json and NO LONGER
    RUN. ballast.json is a plain writable file that no gate covers, so
    honouring an argv declared there meant one edit to that single file
    bought arbitrary command execution — on every later significant write,
    with the scope root as cwd, and with no consent step anywhere. That is
    a strictly larger unreviewed-edit surface than the two files the
    approval gate exists to protect, bought for a generated listing.
    Existing scopes keep loading (the field is still accepted, so no
    consumer's ballast.json breaks); one declaring a command gets ONE
    stderr line saying it was ignored, never silence."""
    if not scope["index_path"]:
        return   # the index class is disabled for this scope ("index": null)
    if bl.DRY_RUN:
        return
    try:
        _builtin_regen_index(scope)
    except Exception as exc:
        _note("index regen failed: %r" % exc)
        return
    if scope["regen"]:
        _note("`regen` is declared in %s but is no longer executed — a "
             "custom regen command would make ballast.json an ungated "
             "command-execution surface; ballast's built-in index "
             "generator ran instead" % scope["scope_path"])


def _builtin_regen_index(scope):
    root = scope["root"]
    skip_abs = {os.path.normpath(p) for p in
               (scope["log_path"], scope["hot_path"], scope["index_path"],
                scope["policy_path"], scope["glossary_path"]) if p}
    skip_dirnames = {".ballast", ".git"}
    rows = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in skip_dirnames]
        for name in sorted(filenames):
            full = os.path.normpath(os.path.join(dirpath, name))
            if full in skip_abs:
                continue
            try:
                st = os.stat(full)
            except OSError:
                continue
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            rows.append((rel, st.st_size, st.st_mtime))
    rows.sort(key=lambda r: r[0])
    lines = ["<!-- ballast:index v1 (generated — do not hand-edit) -->",
            "| path | bytes | modified |", "|---|---|---|"]
    for rel, size, mtime in rows:
        stamp = bl.now_iso() if mtime <= 0 else \
            __import__("datetime").datetime.fromtimestamp(
                mtime, __import__("datetime").timezone.utc
            ).strftime("%Y-%m-%dT%H:%M:%SZ")
        lines.append("| %s | %d | %s |" % (rel, size, stamp))
    bl.write_text_atomic(scope["index_path"], "\n".join(lines) + "\n")


# --- PreCompact --------------------------------------------------------

def cmd_pre_compact(scope, payload):
    """Carry: hand the summarizer hot.md verbatim + the log delta, and
    instruct it not to re-summarize hot (02: "PreCompact -> carry: hand
    the summarizer hot verbatim + the delta, no re-summarize"). Also
    arms the next UserPromptSubmit's mandatory re-ground (X4)."""
    session_id = payload.get("session_id")
    if bl.safe_session_id(session_id):
        bl.mark_pending_compact_reground(scope, session_id)

    hot_text, hot_markers, _ = _read_hot(scope)
    blocks = [
        "## Ballast carry (PreCompact)",
        "Preserve the block below VERBATIM in the compaction summary — do "
        "NOT re-summarize or paraphrase it; it is already the compact "
        "form.",
    ]
    if hot_text:
        blocks.extend(hot_markers)
        blocks.append(hot_text.strip())
    else:
        blocks.append("(no hot.md yet at %s)" % _rel(scope, scope["hot_path"]))

    delta = bl.log_delta(scope["log_path"], _hot_updated_ts(scope),
                         cap_bytes=1024)
    if delta:
        blocks.append("Log delta since hot's last `updated`:\n" +
                      "\n".join(delta[-20:]))

    _out("\n\n".join(blocks))
    return 0


# --- Stop ----------------------------------------------------------------

STOP_FRESHNESS_REASON = (
    "Ballast: `%s` is behind `%s` — this session has significant writes "
    "logged since hot.md's `updated` slot was last set. Before stopping: "
    "refresh `%s`'s slots (at least `updated`, plus whatever else this "
    "session changed) using the Write or Edit tool, then stop again."
)

STOP_COMPLETION_REASON = (
    "Ballast: `%s` is missing a value for required slot(s) %s. Before "
    "stopping: fill in every required slot (Write or Edit tool), then "
    "stop again."
)


def cmd_stop(scope, payload):
    """Freshness gate + completion check (02: "Stop -> if hot is stale,
    block the turn end for one refresh; a read-only turn never triggers
    it, and it fails open after one attempt"; X6 adds the completion
    check as a second, independent reason to gate once).

    HARD ANTI-LOOP, checked first: when the hook payload's own
    `stop_hook_active` is true, this hook
    already blocked once this turn — always allow, unconditionally. This
    is what makes "fails open after one attempt" true without any extra
    bookkeeping: the harness itself is the one-shot counter.
    """
    if payload.get("stop_hook_active"):
        return 0

    reasons = []
    if bl.hot_is_stale(scope):
        reasons.append(STOP_FRESHNESS_REASON % (
            _rel(scope, scope["hot_path"]), _rel(scope, scope["log_path"]),
            _rel(scope, scope["hot_path"])))

    gaps = bl.completion_gaps(scope)
    if gaps:
        reasons.append(STOP_COMPLETION_REASON % (
            _rel(scope, scope["hot_path"]), ", ".join(gaps)))

    if reasons:
        reasons.append(_refresh_template(scope))
        _emit_decision({"decision": "block", "reason": "\n".join(reasons)})
    return 0


# --- PreToolUse: the approval gate --------------------------------------

GATE_REFUSAL = (
    "Ballast approval gate: `%s` may not be written directly.\n"
    "%s is gated — every edit must be shown to the person who owns these "
    "rules, in full, before it is written.\n"
    "The sanctioned path: show the operator the exact text that will be written, "
    "wait for their explicit yes, mint the approval with\n"
    "  approve.py mint --scope <the scope's ballast.json> --file %s\n"
    "then write through the primitive (%s), which consumes the approval.\n"
    "A minted approval is one-time and expires after %d seconds."
)

_PRIMITIVE_OF = {
    "policy.md": "policy.py set",
    "glossary.md": "glossary.py add|edit|delete",
}


def cmd_pre_tool_use(scope, payload):
    """A9b — refuse a direct Write/Edit/NotebookEdit to one of the two
    gated files unless a fresh approval token exists (which it then
    consumes). Everything else passes untouched: exit 0, no output.

    Fail-CLOSED for those two filenames only — that is the whole point of
    a gate — and the two switch files disarm it. Refusal follows the hook
    protocol: exit 2 with the reason on stderr, so the reason reaches the
    model rather than the transcript.

    The honest limit, stated in docs/approval-gate.md: this proves the
    SANCTIONED PATH was used. What actually asks the operator is the skill
    template's own procedure."""
    tool = payload.get("tool_name")
    if not isinstance(tool, str) or \
            tool.strip().lower() not in bl.WRITE_TOOL_NAMES:
        return 0

    state_root = scope["state_root"]
    for raw_path in bl._extract_paths(payload.get("tool_input")):
        cwd = payload.get("cwd") or os.getcwd()
        target = raw_path if os.path.isabs(raw_path) \
            else os.path.join(cwd, raw_path)
        name = bl.gated_filename(target)
        if name is None or not bl.is_under(target, state_root):
            continue
        if name == bl.GLOSSARY_FILENAME and \
                bl.glossary_gate_disabled(state_root):
            continue
        if bl.gate_disabled(state_root):
            _note("approval gate is OFF (%s exists) — allowing the write to "
                 "%s" % (bl.GATE_DISABLED_FILENAME, target))
            continue
        record = bl.read_approval(target, state_root)
        # Consumption IS the claim, not a bookkeeping step after one: two
        # gate checks racing on one token both read the same fresh record,
        # so only the DELETE can decide between them. A consume that
        # returns False means another check already took it — this write
        # has no approval left and is refused. (--dry-run consumes
        # nothing by design, so it never claims and never refuses on a
        # missing claim.)
        if bl.approval_is_fresh(record, scope["approval_ttl_seconds"]) \
                and (bl.DRY_RUN or bl.consume_approval(record)):
            continue
        _note(GATE_REFUSAL % (target, name, target,
                              _PRIMITIVE_OF.get(name, "the scope's own verb"),
                              scope["approval_ttl_seconds"]))
        return bl.REFUSAL_EXIT
    return 0


DISPATCH = {
    "SessionStart": cmd_session_start,
    "PreToolUse": cmd_pre_tool_use,
    "UserPromptSubmit": cmd_user_prompt_submit,
    "PostToolUse": cmd_post_tool_use,
    "PreCompact": cmd_pre_compact,
    "Stop": cmd_stop,
}


def parse_args(argv):
    """`(event, scope_path, dry_run, part)`. `part` is None when --part was
    not given at all, which is how an un-updated 0.1.1 hook line is told
    apart from an explicit `--part all` for labelling purposes; both behave
    identically."""
    if not argv:
        return None, None, False, None
    event_raw = argv[0]
    event = EVENT_ALIASES.get(event_raw.strip().lower())
    scope_path = None
    dry_run = False
    part = None
    i = 1
    while i < len(argv):
        if argv[i] == "--scope" and i + 1 < len(argv):
            scope_path = argv[i + 1]
            i += 2
        elif argv[i] == "--part" and i + 1 < len(argv):
            part = argv[i + 1].strip().lower()
            i += 2
        elif argv[i] == "--dry-run":
            dry_run = True
            i += 1
        else:
            i += 1
    return event, scope_path, dry_run, part


def main(argv):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace", newline="\n")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace", newline="\n")

    event, scope_path, dry_run, part = parse_args(argv)
    if event is None or scope_path is None:
        _note("usage: ballast.py EVENT --scope PATH [--part NAME] "
             "[--dry-run] (got argv=%r) — no-op, fail open" % (argv,))
        return 0
    bl.DRY_RUN = dry_run
    global DELIVERY_LABEL, PART
    PART = part or "all"
    DELIVERY_LABEL = event if part is None \
        else "%s --part %s" % (event, part)

    payload = bl.read_stdin_json()

    try:
        scope = bl.load_scope(scope_path)
    except bl.ScopeError as exc:
        _note("scope error, fail open: %s" % exc)
        return 0
    except Exception as exc:
        _note("unexpected error loading scope, fail open: %r" % exc)
        return 0

    if not bl.semver_at_least(bl.ENGINE_VERSION, scope["min_engine"]):
        _note("this ballast engine (%s) is older than the scope's "
             "min_engine (%s) — fail open, no-op"
             % (bl.ENGINE_VERSION, scope["min_engine"]))
        return 0

    handler = DISPATCH.get(event)
    if handler is None:
        _note("unknown event %r — fail open, no-op" % (event,))
        return 0

    try:
        return handler(scope, payload) or 0
    except Exception as exc:
        _note("%s handler failed, fail open: %r" % (event, exc))
        _not_loaded(DELIVERY_LABEL, exc)
        return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except SystemExit:
        raise
    except BaseException:
        # Even a crash this early must fail open — no partial stdout should
        # exist yet at this point in every realistic failure, so silence
        # here already means "allow."
        sys.exit(0)
