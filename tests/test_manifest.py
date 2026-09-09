#!/usr/bin/env python3
"""Unit tests for scripts/manifest.py - the single-writer primitive (I3-I5)
plus the one sanctioned cross-manifest write, absorb_close (AB1-AB7)."""
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts")
SKILL_MD = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                        "skills", "manifest", "SKILL.md")
sys.path.insert(0, SCRIPTS)
import workstream_lib as wslib
import manifest as mprim


def make_vault():
    d = tempfile.mkdtemp(prefix="ws_manifest_test_")
    os.makedirs(os.path.join(d, "staff"), exist_ok=True)
    return d


# --- the skill's own documented commands (see DocumentedSkillCommandTests) --
DOCUMENTED_SET_RE = re.compile(
    r"manifest\.py set <born_session> ([A-Za-z_]+) "
    r"(?:'(?P<quoted>[^']*)'|(?P<bare>null))")
PLACEHOLDER_RE = re.compile(r"<([a-zA-Z0-9_-]+)>")
# The one placeholder whose own text is not itself a legal value.
PLACEHOLDER_VALUES = {"state": "active"}


def documented_set_commands():
    """[(field, value-as-written), ...] for every `manifest.py set` command
    in skills/manifest/SKILL.md - read from the shipped file, never restated
    here, so the test exercises the text a person would copy."""
    with open(SKILL_MD, encoding="utf-8") as handle:
        text = handle.read()
    out = []
    for match in DOCUMENTED_SET_RE.finditer(text):
        value = match.group("quoted")
        out.append((match.group(1), match.group("bare") if value is None else value))
    return out


def concretize(value):
    """The documented value with each angle-bracket placeholder replaced by
    a literal of the same shape - the substitution a person makes when they
    run the command."""
    return PLACEHOLDER_RE.sub(
        lambda m: PLACEHOLDER_VALUES.get(m.group(1), m.group(1)), value)


# A stand-in for ballast's scripts/approve.py, implementing exactly the
# convention docs/approval-gate.md documents: a token named for the sha1
# of the target's CANONICAL path, under `<state-root>/.ballast-approvals/`
# (the state root being the parent of the scope directory, or the nearest
# existing approvals dir above the target when no scope is given), fresh
# for a TTL, deleted on consume. Exit 0 succeeded / 1 refused / 2 usage.
#
# A stub rather than the real script because this repo's tests must not
# depend on a sibling repo being checked out beside it; what is under test
# here is manifest.py's own use of the seam - that it checks before
# writing, consumes on success, refuses with the mint command, and never
# gates an ungated field.
STUB_APPROVE = r'''#!/usr/bin/env python3
import hashlib, json, os, sys, time

TTL = 600

def canon(p):
    return os.path.normcase(os.path.realpath(os.path.abspath(p)))

def token_name(p):
    return hashlib.sha1(canon(p).encode("utf-8")).hexdigest() + ".token"

def approvals_dir(scope, target):
    if scope:
        return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(scope))),
                            ".ballast-approvals")
    cur = os.path.dirname(canon(target))
    while True:
        cand = os.path.join(cur, ".ballast-approvals")
        if os.path.isdir(cand):
            return cand
        parent = os.path.dirname(cur)
        if parent == cur:
            return None
        cur = parent

def main(argv):
    action = argv[0] if argv else ""
    opts = {}
    i = 1
    while i < len(argv) - 1:
        if argv[i] in ("--file", "--scope", "--field"):
            opts[argv[i][2:]] = argv[i + 1]
            i += 2
        else:
            return 2
    target = opts.get("file")
    if action not in ("mint", "check", "consume") or not target:
        return 2
    if opts.get("scope") and not os.path.isfile(opts["scope"]):
        return 2
    directory = approvals_dir(opts.get("scope"), target)
    if action == "mint":
        if not directory:
            return 2
        os.makedirs(directory, exist_ok=True)
        try:
            os.unlink(os.path.join(directory, token_name(target)) + ".claimed")
        except OSError:
            pass
        with open(os.path.join(directory, token_name(target)), "w") as handle:
            json.dump({"minted": time.time(), "field": opts.get("field")}, handle)
        return 0
    if not directory:
        return 1
    path = os.path.join(directory, token_name(target))
    if not os.path.isfile(path):
        return 1
    if action == "check":
        with open(path) as handle:
            age = time.time() - json.load(handle)["minted"]
        return 0 if 0 <= age <= TTL else 1
    # consume: the CLAIM is an exclusive create beside the token, exactly as
    # ballast_lib.consume_approval does it - a delete is not a claim (two
    # racers can both succeed at deleting one file), so the loser of the
    # claim is refused here with exit 1 and mint clears the marker.
    try:
        claim = os.open(path + ".claimed", os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except OSError:
        return 1
    os.close(claim)
    os.unlink(path)
    return 0

sys.exit(main(sys.argv[1:]))
'''


class GatedTestCase(unittest.TestCase):
    """Base for every test that touches an approval-gated field: installs
    the stub ballast as a sibling plugin under CLAUDE_PLUGIN_ROOT and
    points HOME at an empty directory, so resolution is deterministic and
    a real ballast on the host can never leak in."""

    def setUp(self):
        self.vault = make_vault()
        self.root = tempfile.mkdtemp(prefix="ws_plugin_root_")
        self._old_env = dict(os.environ)
        self.plugins_root = tempfile.mkdtemp(prefix="ws_plugins_root_")
        self.own_root = os.path.join(self.plugins_root, "workstream")
        os.makedirs(self.own_root)
        ballast_scripts = os.path.join(self.plugins_root, "ballast", "scripts")
        os.makedirs(ballast_scripts)
        self.approve_py = os.path.join(ballast_scripts, "approve.py")
        with open(self.approve_py, "w", newline="\n") as handle:
            handle.write(STUB_APPROVE)
        os.environ["CLAUDE_PLUGIN_ROOT"] = self.own_root
        os.environ["HOME"] = tempfile.mkdtemp(prefix="ws_home_")
        os.environ.pop("USERPROFILE", None)
        self.extra_setup()

    def extra_setup(self):
        pass

    def tearDown(self):
        shutil.rmtree(self.vault, ignore_errors=True)
        shutil.rmtree(self.root, ignore_errors=True)
        shutil.rmtree(self.plugins_root, ignore_errors=True)
        os.environ.clear()
        os.environ.update(self._old_env)

    def state_root(self):
        return wslib.state_root(self.vault, self.root)

    def scope_for(self, born_session):
        """A ballast.json beside the manifest, as ballast-dispatch writes on
        first use - `approve.py mint` needs one to find the state root."""
        path = os.path.join(self.state_root(), born_session, "ballast.json")
        if not os.path.isfile(path):
            wslib.atomic_write_lf(path, '{"root": "."}\n')
        return path

    def mint(self, born_session, field):
        proc = subprocess.run(
            [sys.executable, self.approve_py, "mint",
             "--scope", self.scope_for(born_session),
             "--file", wslib.manifest_path_for(self.vault, born_session, self.root),
             "--field", field], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def token_count(self):
        """Live tokens only - a spent token leaves its claim marker behind
        (see STUB_APPROVE's consume), and ballast counts `*.token` too."""
        directory = os.path.join(self.state_root(), ".ballast-approvals")
        if not os.path.isdir(directory):
            return 0
        return len([n for n in os.listdir(directory) if n.endswith(".token")])

    def token_path(self):
        directory = os.path.join(self.state_root(), ".ballast-approvals")
        return os.path.join(directory, sorted(
            n for n in os.listdir(directory) if n.endswith(".token"))[0])

    def disarm_gate(self):
        open(os.path.join(self.state_root(), "ballast-gate.disabled"), "w").close()


class CreateManifestTests(unittest.TestCase):
    def setUp(self):
        self.vault = make_vault()
        self.root = tempfile.mkdtemp(prefix="ws_plugin_root_")

    def tearDown(self):
        shutil.rmtree(self.vault, ignore_errors=True)
        shutil.rmtree(self.root, ignore_errors=True)

    def test_create_writes_schema_defaults(self):
        mprim.create_manifest(self.vault, "born-1", "my-ws", "do the thing", root=self.root)
        data, err = wslib.read_manifest(self.vault, "born-1", self.root)
        self.assertIsNone(err)
        self.assertEqual(data["name"], "my-ws")
        self.assertEqual(data["state"], "active")
        self.assertEqual(data["born_session"], "born-1")
        self.assertIsNone(data["direct_report"])
        self.assertEqual(data["collaborate"], [])
        self.assertEqual(data["absorbed"], [])
        self.assertEqual(data["maintains"], [])
        self.assertNotIn("parents", data)
        self.assertNotIn("rebound", data)

    def test_create_refuses_when_exists(self):
        mprim.create_manifest(self.vault, "born-1", "a", "f", root=self.root)
        with self.assertRaises(FileExistsError):
            mprim.create_manifest(self.vault, "born-1", "a-again", "f", root=self.root)

    def test_create_refuses_unsafe_born_session(self):
        with self.assertRaises(ValueError):
            mprim.create_manifest(self.vault, "../escape", "a", "f", root=self.root)

    def test_create_dry_run_touches_nothing(self):
        path = mprim.create_manifest(self.vault, "born-dry", "a", "f", root=self.root, dry_run=True)
        self.assertFalse(os.path.isfile(path))
        data, err = wslib.read_manifest(self.vault, "born-dry", self.root)
        self.assertIsNone(data)


class RepairManifestTests(unittest.TestCase):
    """R0-3: a workstream.json that no longer parses has no other write path
    (`set` refuses it, the direct-Write guard refuses a hand edit). repair is
    the in-schema route: it moves the unusable file aside and rewrites the
    canonical shape, and refuses to touch a well-formed manifest."""

    def setUp(self):
        self.vault = make_vault()
        self.root = tempfile.mkdtemp(prefix="ws_plugin_root_")

    def tearDown(self):
        shutil.rmtree(self.vault, ignore_errors=True)
        shutil.rmtree(self.root, ignore_errors=True)

    def _malform(self, born_session, text="not json{"):
        path = wslib.manifest_path_for(self.vault, born_session, self.root)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        return path

    def test_set_cannot_touch_a_malformed_manifest(self):
        self._malform("born-r")
        with self.assertRaises(FileNotFoundError):
            mprim.set_field(self.vault, "born-r", "focus", "x", root=self.root)

    def test_repair_rewrites_a_malformed_manifest_and_backs_it_up(self):
        path = self._malform("born-r")
        p, backup, err = mprim.repair_manifest(self.vault, "born-r", "ws-r",
                                               "do it", root=self.root)
        self.assertEqual(p, path)
        self.assertTrue(os.path.isfile(backup))
        self.assertEqual(err, "malformed JSON")
        data, e = wslib.read_manifest(self.vault, "born-r", self.root)
        self.assertIsNone(e)
        self.assertEqual(data["name"], "ws-r")
        self.assertEqual(data["state"], "active")
        self.assertEqual(data["born_session"], "born-r")
        self.assertEqual(data["direct_report"], None)
        self.assertEqual(data["maintains"], [])

    def test_repair_refuses_a_well_formed_manifest(self):
        mprim.create_manifest(self.vault, "born-ok", "ws-ok", "f", root=self.root)
        with self.assertRaises(ValueError):
            mprim.repair_manifest(self.vault, "born-ok", "ws-ok", root=self.root)

    def test_repair_on_a_missing_manifest_raises(self):
        with self.assertRaises(FileNotFoundError):
            mprim.repair_manifest(self.vault, "born-none", "ws", root=self.root)

    def test_repair_dry_run_touches_nothing(self):
        path = self._malform("born-d")
        with open(path, encoding="utf-8") as fh:
            before = fh.read()
        _p, backup, _err = mprim.repair_manifest(self.vault, "born-d", "ws-d",
                                                 root=self.root, dry_run=True)
        self.assertFalse(os.path.isfile(backup))
        with open(path, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), before)


class SetFieldTests(GatedTestCase):
    def extra_setup(self):
        mprim.create_manifest(self.vault, "born-1", "a", "f", root=self.root)

    def test_set_focus(self):
        mprim.set_field(self.vault, "born-1", "focus", "a new focus", root=self.root)
        data, _ = wslib.read_manifest(self.vault, "born-1", self.root)
        self.assertEqual(data["focus"], "a new focus")

    def test_set_state_valid(self):
        mprim.set_field(self.vault, "born-1", "state", "closed", root=self.root)
        data, _ = wslib.read_manifest(self.vault, "born-1", self.root)
        self.assertEqual(data["state"], "closed")

    def test_set_state_invalid_rejected(self):
        with self.assertRaises(ValueError):
            mprim.set_field(self.vault, "born-1", "state", "bogus", root=self.root)

    def test_set_direct_report_rejects_bare_string(self):
        with self.assertRaises(ValueError):
            mprim.set_field(self.vault, "born-1", "direct_report", "comms-desk", root=self.root)

    def test_set_direct_report_accepts_object(self):
        self.mint("born-1", "direct_report")
        mprim.set_field(self.vault, "born-1", "direct_report", {"name": "up", "session": "s"}, root=self.root)
        data, _ = wslib.read_manifest(self.vault, "born-1", self.root)
        self.assertEqual(data["direct_report"]["name"], "up")

    def test_set_refuses_legacy_field(self):
        with self.assertRaises(ValueError):
            mprim.set_field(self.vault, "born-1", "parents", ["x"], root=self.root)
        with self.assertRaises(ValueError):
            mprim.set_field(self.vault, "born-1", "rebound", ["x"], root=self.root)

    def test_set_refuses_an_immutable_field(self):
        """R0-5: identity/lineage is set once at birth and never rewritten -
        the very fields three docs call immutable."""
        for field in mprim.IMMUTABLE_FIELDS:
            with self.assertRaises(ValueError, msg=field):
                mprim.set_field(self.vault, "born-1", field, "HACKED", root=self.root)
        data, _ = wslib.read_manifest(self.vault, "born-1", self.root)
        self.assertEqual(data["born_session"], "born-1")   # untouched

    def test_set_refuses_an_unknown_field(self):
        """R0-5: a field name in neither SCHEMA_FIELDS nor the writable
        allowlist is refused, not written through as a dead key."""
        with self.assertRaises(ValueError):
            mprim.set_field(self.vault, "born-1", "bogus_unknown_field", "x",
                            root=self.root)
        data, _ = wslib.read_manifest(self.vault, "born-1", self.root)
        self.assertNotIn("bogus_unknown_field", data)

    def test_set_on_missing_manifest_raises(self):
        with self.assertRaises(FileNotFoundError):
            mprim.set_field(self.vault, "no-such-born", "focus", "x", root=self.root)


class CollaborateTests(GatedTestCase):
    def extra_setup(self):
        mprim.create_manifest(self.vault, "a", "a", "f", root=self.root)

    def test_add_is_additive(self):
        self.mint("a", "collaborate")
        mprim.collaborate_add(self.vault, "a", "b", "peer-b", scope="shared thing", root=self.root)
        data, _ = wslib.read_manifest(self.vault, "a", self.root)
        self.assertEqual(len(data["collaborate"]), 1)
        self.assertEqual(data["collaborate"][0]["session"], "b")
        self.assertEqual(data["collaborate"][0]["scope"], "shared thing")

    def test_add_duplicate_is_idempotent_never_a_second_entry(self):
        self.mint("a", "collaborate")
        mprim.collaborate_add(self.vault, "a", "b", "peer-b", root=self.root)
        # No second mint: the duplicate is an idempotent no-op, so it must
        # not demand (or spend) an approval of its own.
        mprim.collaborate_add(self.vault, "a", "b", "peer-b-renamed", root=self.root)
        data, _ = wslib.read_manifest(self.vault, "a", self.root)
        self.assertEqual(len(data["collaborate"]), 1)

    def test_add_never_touches_unrelated_entries(self):
        self.mint("a", "collaborate")
        mprim.collaborate_add(self.vault, "a", "b", "peer-b", root=self.root)
        self.mint("a", "collaborate")
        mprim.collaborate_add(self.vault, "a", "c", "peer-c", root=self.root)
        data, _ = wslib.read_manifest(self.vault, "a", self.root)
        self.assertEqual({e["session"] for e in data["collaborate"]}, {"b", "c"})

    def test_remove_drops_only_the_named_peer(self):
        for peer, name in (("b", "peer-b"), ("c", "peer-c")):
            self.mint("a", "collaborate")
            mprim.collaborate_add(self.vault, "a", peer, name, root=self.root)
        self.mint("a", "collaborate")
        mprim.collaborate_remove(self.vault, "a", "b", root=self.root)
        data, _ = wslib.read_manifest(self.vault, "a", self.root)
        self.assertEqual([e["session"] for e in data["collaborate"]], ["c"])

    def test_remove_of_an_absent_peer_needs_no_approval(self):
        mprim.collaborate_remove(self.vault, "a", "nobody", root=self.root)
        data, _ = wslib.read_manifest(self.vault, "a", self.root)
        self.assertEqual(data["collaborate"], [])


class AppendAbsorbedTests(GatedTestCase):
    def extra_setup(self):
        mprim.create_manifest(self.vault, "overtaker", "overtaker", "f", root=self.root)

    def test_append_absorbed_is_additive(self):
        self.mint("overtaker", "absorbed")
        mprim.append_absorbed(self.vault, "overtaker", "stale-ws", "stale-born",
                              ".vault-meta/workstreams/stale-born", "path/to.jsonl", root=self.root)
        data, _ = wslib.read_manifest(self.vault, "overtaker", self.root)
        self.assertEqual(len(data["absorbed"]), 1)
        self.assertEqual(data["absorbed"][0]["born_session"], "stale-born")
        self.assertEqual(data["absorbed"][0]["transcript"], "path/to.jsonl")

    def test_append_absorbed_duplicate_idempotent(self):
        self.mint("overtaker", "absorbed")
        mprim.append_absorbed(self.vault, "overtaker", "stale-ws", "stale-born", "dir", root=self.root)
        mprim.append_absorbed(self.vault, "overtaker", "stale-ws", "stale-born", "dir", root=self.root)
        data, _ = wslib.read_manifest(self.vault, "overtaker", self.root)
        self.assertEqual(len(data["absorbed"]), 1)


FAKE_LOCK_SH = """#!/bin/bash
# Minimal fake vault-lock.sh for tests: acquire always succeeds unless the
# path contains "DENY", release always succeeds.
if [ "$1" = "acquire" ]; then
  case "$2" in *DENY*) exit 75;; esac
  exit 0
fi
if [ "$1" = "release" ]; then
  exit 0
fi
exit 2
"""


class AbsorbCloseTests(unittest.TestCase):
    def setUp(self):
        self.vault = make_vault()
        self.root = tempfile.mkdtemp(prefix="ws_plugin_root_")
        mprim.create_manifest(self.vault, "stale", "stale-name", "old focus", root=self.root)
        self.lock_sh = os.path.join(self.vault, "fake-vault-lock.sh")
        with open(self.lock_sh, "w", newline="\n") as f:
            f.write(FAKE_LOCK_SH)
        os.chmod(self.lock_sh, os.stat(self.lock_sh).st_mode | stat.S_IEXEC)

    def tearDown(self):
        shutil.rmtree(self.vault, ignore_errors=True)
        shutil.rmtree(self.root, ignore_errors=True)

    def test_absorb_close_sets_state_and_absorbed_by(self):
        data, note = mprim.absorb_close(self.vault, "stale", "overtaker-name", "overtaker-born",
                                        vault_lock_sh=self.lock_sh, lock_session="overtaker-born",
                                        root=self.root)
        self.assertEqual(data["state"], "absorbed")
        self.assertEqual(data["absorbed_by"], "overtaker-name")
        self.assertEqual(data["absorbed_by_session"], "overtaker-born")
        self.assertIsNone(note)   # lock acquired cleanly - no warning

    def test_absorb_close_without_vault_lock_still_writes_with_warning(self):
        data, note = mprim.absorb_close(self.vault, "stale", "overtaker-name", "overtaker-born",
                                        vault_lock_sh=None, root=self.root)
        self.assertEqual(data["state"], "absorbed")
        self.assertIn("UNGUARDED", note)

    def test_absorb_close_on_missing_manifest_raises(self):
        with self.assertRaises(FileNotFoundError):
            mprim.absorb_close(self.vault, "no-such", "n", "s", root=self.root)

    def test_absorb_close_dry_run_never_writes_or_locks(self):
        data, note = mprim.absorb_close(self.vault, "stale", "overtaker-name", "overtaker-born",
                                        vault_lock_sh=self.lock_sh, root=self.root, dry_run=True)
        self.assertEqual(data["state"], "absorbed")   # returned preview reflects the would-be write
        self.assertIn("DRY-RUN", note)
        on_disk, _ = wslib.read_manifest(self.vault, "stale", self.root)
        self.assertEqual(on_disk["state"], "active")   # nothing actually landed


class ApprovalGateTests(GatedTestCase):
    """B2: the gate covers four fields, and the approval is FILE-scoped (a
    token authorizes the next gated write to the manifest, whichever field -
    Ballast keys it by target path). The four fleet-reshaping fields need a
    fresh approval; everything else - above all the fields hooks write on
    a cadence, such as last_touched - is never gated, because gating one
    of those would fail every hook-driven manifest write in the vault."""

    def extra_setup(self):
        mprim.create_manifest(self.vault, "born-1", "a", "f", root=self.root)

    # --- the ungated majority ------------------------------------------
    def test_last_touched_is_never_gated(self):
        mprim.set_field(self.vault, "born-1", "last_touched",
                        "2026-09-08T00:00:00Z", root=self.root)
        data, _ = wslib.read_manifest(self.vault, "born-1", self.root)
        self.assertEqual(data["last_touched"], "2026-09-08T00:00:00Z")
        self.assertEqual(self.token_count(), 0)

    def test_every_ungated_field_writes_without_a_token(self):
        for field, value in (("focus", "a new focus"), ("state", "closed"),
                             ("name", "renamed"), ("projects", ["p"]),
                             ("refocused", [{"date": "d"}]),
                             ("previous_names", [{"name": "old"}]),
                             ("absorbed_by", "successor"),
                             ("absorbed_by_session", "s")):
            mprim.set_field(self.vault, "born-1", field, value, root=self.root)
        data, _ = wslib.read_manifest(self.vault, "born-1", self.root)
        self.assertEqual(data["focus"], "a new focus")
        self.assertEqual(data["absorbed_by"], "successor")
        self.assertEqual(self.token_count(), 0)

    # --- the four gated fields -----------------------------------------
    def test_each_gated_field_is_refused_without_a_token(self):
        for field, value in (("maintains", ["[[a-node]]"]),
                             ("direct_report", {"name": "up", "session": "s"}),
                             ("collaborate", [{"name": "peer", "session": "p"}]),
                             ("absorbed", [{"name": "x", "born_session": "y"}])):
            with self.assertRaises(PermissionError, msg=field) as caught:
                mprim.set_field(self.vault, "born-1", field, value, root=self.root)
            self.assertIn("approve.py mint", str(caught.exception))
            self.assertIn(field, str(caught.exception))
        data, _ = wslib.read_manifest(self.vault, "born-1", self.root)
        for field in mprim.GATED_FIELDS:
            self.assertIn(data[field], ([], None), field)   # nothing landed

    def test_each_gated_field_is_written_with_a_fresh_token(self):
        for field, value in (("maintains", ["[[a-node]]"]),
                             ("direct_report", {"name": "up", "session": "s"}),
                             ("collaborate", [{"name": "peer", "session": "p"}]),
                             ("absorbed", [{"name": "x", "born_session": "y"}])):
            self.mint("born-1", field)
            mprim.set_field(self.vault, "born-1", field, value, root=self.root)
            data, _ = wslib.read_manifest(self.vault, "born-1", self.root)
            self.assertEqual(data[field], value, field)

    def test_an_approval_is_one_time(self):
        self.mint("born-1", "maintains")
        mprim.set_field(self.vault, "born-1", "maintains", ["one"], root=self.root)
        self.assertEqual(self.token_count(), 0)   # consumed by the write
        with self.assertRaises(PermissionError):
            mprim.set_field(self.vault, "born-1", "maintains", ["two"], root=self.root)
        data, _ = wslib.read_manifest(self.vault, "born-1", self.root)
        self.assertEqual(data["maintains"], ["one"])

    def test_the_refusal_gives_a_file_scoped_mint_command_with_no_field_flag(self):
        """R0-2: Ballast keys a token by target path and never reads the
        field, so the approval is FILE-scoped. The refusal prints a mint
        command WITHOUT --field (a per-field scope the mechanism cannot
        deliver) and says the approval covers the file."""
        self.scope_for("born-1")   # born-1 has its own scope -> the mint-command refusal
        with self.assertRaises(PermissionError) as caught:
            mprim.set_field(self.vault, "born-1", "maintains", ["x"], root=self.root)
        msg = str(caught.exception)
        self.assertIn("approve.py mint --scope", msg)
        self.assertNotIn("--field", msg)
        self.assertIn("scoped to this FILE", msg)

    def test_a_token_authorizes_a_write_to_any_gated_field_then_is_spent(self):
        """R0-2: the token is file-scoped, so one approval authorizes the
        next gated write whatever field it touches - and only that one."""
        self.mint("born-1", "maintains")            # minted for one field...
        mprim.set_field(self.vault, "born-1", "direct_report",
                        {"name": "up", "session": "s"}, root=self.root)  # ...spent by another
        data, _ = wslib.read_manifest(self.vault, "born-1", self.root)
        self.assertEqual(data["direct_report"], {"name": "up", "session": "s"})
        self.assertEqual(self.token_count(), 0)     # one write, one token
        with self.assertRaises(PermissionError):    # a second gated write needs a second yes
            mprim.set_field(self.vault, "born-1", "maintains", ["x"], root=self.root)

    def test_a_scope_less_dir_falls_back_to_a_sibling_scope_in_the_refusal(self):
        """R0-4: born-1's own dir has no ballast.json (20 of the 43 live dirs
        are like this), but a scope exists under the same state root. The
        refusal must print a RUNNABLE mint command - a real --scope path and
        the resolved approve.py path - never an angle-bracket placeholder,
        so the gated write is actually authorizable."""
        sibling = os.path.join(self.state_root(), "born-sibling", "ballast.json")
        os.makedirs(os.path.dirname(sibling), exist_ok=True)
        wslib.atomic_write_lf(sibling, '{"root": "."}\n')
        own = os.path.join(self.state_root(), "born-1", "ballast.json")
        self.assertFalse(os.path.isfile(own))   # born-1 is scope-less
        with self.assertRaises(PermissionError) as caught:
            mprim.set_field(self.vault, "born-1", "maintains", ["x"], root=self.root)
        msg = str(caught.exception)
        self.assertNotIn("<the scope", msg)
        mint_line = [ln for ln in msg.splitlines() if "mint --scope" in ln][0]
        self.assertNotIn("<", mint_line)              # no placeholder anywhere in the command
        self.assertIn(sibling, mint_line)             # a real fallback scope
        self.assertIn(self.approve_py, mint_line)     # the resolved approve.py, not a bare name

    def test_a_scope_less_dir_with_a_sibling_scope_is_authorizable_end_to_end(self):
        """R0-4: the fallback is not cosmetic - a token minted against the
        sibling scope actually authorizes the write to the scope-less dir's
        manifest (both resolve the same state root)."""
        wslib.atomic_write_lf(
            os.path.join(self.state_root(), "born-sibling", "ballast.json"),
            '{"root": "."}\n')
        # mint against the sibling scope, targeting born-1's manifest
        proc = subprocess.run(
            [sys.executable, self.approve_py, "mint",
             "--scope", os.path.join(self.state_root(), "born-sibling", "ballast.json"),
             "--file", wslib.manifest_path_for(self.vault, "born-1", self.root)],
            capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        mprim.set_field(self.vault, "born-1", "maintains", ["ok"], root=self.root)
        data, _ = wslib.read_manifest(self.vault, "born-1", self.root)
        self.assertEqual(data["maintains"], ["ok"])

    def test_a_no_op_assignment_needs_no_approval(self):
        """Nothing changed, so there is nothing anyone could have reviewed -
        and a hook re-writing an identical value must not start failing."""
        mprim.set_field(self.vault, "born-1", "maintains", [], root=self.root)
        mprim.set_field(self.vault, "born-1", "direct_report", None, root=self.root)
        self.assertEqual(self.token_count(), 0)

    def test_shape_is_checked_before_the_gate(self):
        """A value that could never be written is refused for being wrong,
        and never spends an approval on the way."""
        self.mint("born-1", "direct_report")
        with self.assertRaises(ValueError):
            mprim.set_field(self.vault, "born-1", "direct_report", "bare-string",
                            root=self.root)
        self.assertEqual(self.token_count(), 1)   # still unspent

    def test_a_legacy_field_is_refused_before_the_gate(self):
        with self.assertRaises(ValueError):
            mprim.set_field(self.vault, "born-1", "parents", ["x"], root=self.root)

    def test_dry_run_checks_the_approval_but_never_spends_it(self):
        self.mint("born-1", "maintains")
        mprim.set_field(self.vault, "born-1", "maintains", ["one"],
                        root=self.root, dry_run=True)
        self.assertEqual(self.token_count(), 1)   # a preview writes nothing
        data, _ = wslib.read_manifest(self.vault, "born-1", self.root)
        self.assertEqual(data["maintains"], [])
        mprim.set_field(self.vault, "born-1", "maintains", ["one"], root=self.root)
        self.assertEqual(self.token_count(), 0)

    def test_dry_run_is_refused_without_an_approval(self):
        with self.assertRaises(PermissionError):
            mprim.set_field(self.vault, "born-1", "maintains", ["one"],
                            root=self.root, dry_run=True)

    def test_the_collaborate_primitives_are_gated(self):
        with self.assertRaises(PermissionError):
            mprim.collaborate_add(self.vault, "born-1", "peer", "peer-name",
                                  root=self.root)
        self.mint("born-1", "collaborate")
        mprim.collaborate_add(self.vault, "born-1", "peer", "peer-name", root=self.root)
        with self.assertRaises(PermissionError):
            mprim.collaborate_remove(self.vault, "born-1", "peer", root=self.root)

    def test_append_absorbed_is_gated(self):
        with self.assertRaises(PermissionError):
            mprim.append_absorbed(self.vault, "born-1", "n", "stale-born", "dir",
                                  root=self.root)
        self.mint("born-1", "absorbed")
        mprim.append_absorbed(self.vault, "born-1", "n", "stale-born", "dir",
                              root=self.root)
        data, _ = wslib.read_manifest(self.vault, "born-1", self.root)
        self.assertEqual(len(data["absorbed"]), 1)

    def test_create_is_never_gated(self):
        """Minting a manifest writes the gated fields EMPTY. There is no
        change to review, and gating birth would make adopt/fork
        unreachable - the plugin's whole entry point."""
        mprim.create_manifest(self.vault, "born-new", "n", "f", root=self.root)
        data, _ = wslib.read_manifest(self.vault, "born-new", self.root)
        self.assertEqual(data["collaborate"], [])
        self.assertEqual(self.token_count(), 0)

    def test_absorb_close_is_never_gated(self):
        """It writes state + absorbed_by/absorbed_by_session on the STALE
        manifest - none of the four gated fields."""
        mprim.create_manifest(self.vault, "stale", "s", "f", root=self.root)
        data, _note = mprim.absorb_close(self.vault, "stale", "over", "over-born",
                                         root=self.root)
        self.assertEqual(data["state"], "absorbed")
        self.assertEqual(self.token_count(), 0)

    def test_the_off_switch_disarms_the_gate(self):
        self.disarm_gate()
        mprim.set_field(self.vault, "born-1", "maintains", ["one"], root=self.root)
        data, _ = wslib.read_manifest(self.vault, "born-1", self.root)
        self.assertEqual(data["maintains"], ["one"])

    def test_missing_ballast_refuses_with_a_clear_message(self):
        os.environ["CLAUDE_PLUGIN_ROOT"] = tempfile.mkdtemp(prefix="ws_no_ballast_")
        with self.assertRaises(PermissionError) as caught:
            mprim.set_field(self.vault, "born-1", "maintains", ["one"], root=self.root)
        self.assertIn("ballast", str(caught.exception))
        self.assertIn("ballast-gate.disabled", str(caught.exception))

    def test_missing_ballast_still_writes_an_ungated_field(self):
        os.environ["CLAUDE_PLUGIN_ROOT"] = tempfile.mkdtemp(prefix="ws_no_ballast_")
        mprim.set_field(self.vault, "born-1", "focus", "still fine", root=self.root)
        data, _ = wslib.read_manifest(self.vault, "born-1", self.root)
        self.assertEqual(data["focus"], "still fine")

    def test_a_malformed_scope_does_not_brick_the_gate(self):
        """approve.py rejects an unloadable --scope with a usage error; the
        gate retries the bare --file form rather than treating a broken
        ballast.json as a standing refusal."""
        self.mint("born-1", "maintains")
        wslib.atomic_write_lf(self.scope_for("born-1"), "{ not json\n")
        mprim.set_field(self.vault, "born-1", "maintains", ["one"], root=self.root)
        data, _ = wslib.read_manifest(self.vault, "born-1", self.root)
        self.assertEqual(data["maintains"], ["one"])


class ApprovalClaimRaceTests(GatedTestCase):
    """One approval, one write - including when two writers race for it.
    The gate used to authorize on `check` and discard `consume`'s exit code,
    so the writer that LOST the claim wrote anyway: one yes for a
    `maintains` change also let a concurrent `collaborate` rewrite through,
    which is exactly the org-chart rewiring the gate exists to prevent."""

    def extra_setup(self):
        mprim.create_manifest(self.vault, "born-r", "n", "f", root=self.root)

    def test_a_lost_claim_refuses_the_write(self):
        self.mint("born-r", "maintains")
        open(self.token_path() + ".claimed", "w").close()   # a racer got there first
        with self.assertRaises(PermissionError):
            mprim.set_field(self.vault, "born-r", "maintains", ["[[a]]"], root=self.root)
        data, _ = wslib.read_manifest(self.vault, "born-r", self.root)
        self.assertEqual(data["maintains"], [])

    def test_one_token_authorizes_exactly_one_of_two_gated_writes(self):
        self.mint("born-r", "maintains")
        mprim.set_field(self.vault, "born-r", "maintains", ["[[a]]"], root=self.root)
        with self.assertRaises(PermissionError):
            mprim.set_field(self.vault, "born-r", "collaborate",
                            [{"name": "p", "session": "s"}], root=self.root)
        data, _ = wslib.read_manifest(self.vault, "born-r", self.root)
        self.assertEqual(data["maintains"], ["[[a]]"])
        self.assertEqual(data["collaborate"], [])

    def test_a_fresh_mint_clears_a_stale_claim(self):
        """A new yes is a new approval: the previous claim must not stand in
        front of it (ballast_lib.mint_approval clears it for the same
        reason)."""
        self.mint("born-r", "maintains")
        open(self.token_path() + ".claimed", "w").close()
        self.mint("born-r", "maintains")
        mprim.set_field(self.vault, "born-r", "maintains", ["[[a]]"], root=self.root)
        data, _ = wslib.read_manifest(self.vault, "born-r", self.root)
        self.assertEqual(data["maintains"], ["[[a]]"])

    def test_a_dry_run_neither_claims_nor_refuses_on_a_claim(self):
        """A preview writes nothing, so it consumes nothing - and must not
        start refusing just because a claim marker exists."""
        self.mint("born-r", "maintains")
        mprim.set_field(self.vault, "born-r", "maintains", ["[[a]]"],
                        root=self.root, dry_run=True)
        self.assertEqual(self.token_count(), 1)
        mprim.set_field(self.vault, "born-r", "maintains", ["[[a]]"], root=self.root)
        data, _ = wslib.read_manifest(self.vault, "born-r", self.root)
        self.assertEqual(data["maintains"], ["[[a]]"])


class ManifestCLITests(unittest.TestCase):
    def setUp(self):
        self.vault = make_vault()

    def tearDown(self):
        shutil.rmtree(self.vault, ignore_errors=True)

    def _run(self, *args):
        return subprocess.run([sys.executable, os.path.join(SCRIPTS, "manifest.py")] + list(args),
                              cwd=self.vault, capture_output=True, text=True)

    def test_cli_create_and_read(self):
        proc = self._run("create", "born-cli", "--name", "cli-ws", "--focus", "test")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        proc = self._run("read", "born-cli")
        self.assertEqual(proc.returncode, 0)
        data = json.loads(proc.stdout)
        self.assertEqual(data["name"], "cli-ws")

    def test_cli_set_rejects_bad_shape(self):
        self._run("create", "born-cli2", "--name", "n", "--focus", "f")
        proc = self._run("set", "born-cli2", "direct_report", json.dumps("bare-string"))
        self.assertEqual(proc.returncode, 2)
        self.assertIn("rejected", proc.stderr)

    def test_cli_set_refuses_immutable_and_unknown_fields(self):
        """R0-5: the reproduced path - `set <bs> born_session '"HACKED"'`,
        `set created ...` and `set bogus_unknown_field ...` all landed on
        disk with exit 0. Each must now exit 2 and leave nothing behind."""
        self._run("create", "born-cli3", "--name", "n", "--focus", "f")
        for field, value in (("born_session", '"HACKED"'), ("created", '"1999"'),
                             ("spawned_from", '"x"'), ("bogus_unknown_field", '"x"')):
            proc = self._run("set", "born-cli3", field, value)
            self.assertEqual(proc.returncode, 2, "%s: %s" % (field, proc.stderr))
        proc = self._run("read", "born-cli3")
        data = json.loads(proc.stdout)
        self.assertEqual(data["born_session"], "born-cli3")
        self.assertNotEqual(data["created"], "1999")
        self.assertNotIn("bogus_unknown_field", data)


class ManifestCLIGateTests(GatedTestCase):
    """The gate surfaces through the CLI the skills actually call: exit 2
    with the mint command on stderr, not a traceback."""

    def extra_setup(self):
        mprim.create_manifest(self.vault, "born-cli", "n", "f", root=self.root)

    def _run(self, *args):
        env = dict(os.environ)
        return subprocess.run([sys.executable, os.path.join(SCRIPTS, "manifest.py")]
                              + list(args), cwd=self.vault, env=env,
                              capture_output=True, text=True)

    def test_cli_refuses_a_gated_field_without_a_token(self):
        proc = self._run("set", "born-cli", "maintains", json.dumps(["[[a]]"]))
        self.assertEqual(proc.returncode, 2)
        self.assertIn("approve.py mint", proc.stderr)
        self.assertEqual(proc.stdout, "")

    def test_cli_writes_a_gated_field_with_a_token(self):
        self.mint("born-cli", "maintains")
        proc = self._run("set", "born-cli", "maintains", json.dumps(["[[a]]"]))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data, _ = wslib.read_manifest(self.vault, "born-cli", self.root)
        self.assertEqual(data["maintains"], ["[[a]]"])

    def test_cli_writes_an_ungated_field_with_no_token(self):
        proc = self._run("set", "born-cli", "last_touched", json.dumps("2026-09-08T00:00:00Z"))
        self.assertEqual(proc.returncode, 0, proc.stderr)


class DocumentedSkillCommandTests(unittest.TestCase):
    """The `manifest.py set` command strings /workstream:manifest documents,
    run EXACTLY as written against a fixture manifest with the gate
    disarmed - so what is under test is the shape check, i.e. whether the
    sanctioned path in the skill actually works when someone follows it.

    Prose review alone missed that five of them wrapped a non-string JSON
    value in an extra literal quote pair (`'"[...]"'`), which json.loads
    turns into a STRING and the shape check rejects every time."""

    EXPECTED_FIELDS = {"focus", "direct_report", "refocused", "name",
                       "previous_names", "state", "absorbed_by",
                       "absorbed_by_session", "maintains", "projects"}

    def setUp(self):
        self.vault = make_vault()
        mprim.create_manifest(self.vault, "ws1", "ws1", "f")
        open(os.path.join(wslib.state_root(self.vault),
                          "ballast-gate.disabled"), "w").close()

    def tearDown(self):
        shutil.rmtree(self.vault, ignore_errors=True)

    def test_every_field_the_skill_documents_is_exercised(self):
        """Guards the extraction itself: a command that stops matching would
        otherwise silently drop out of the run below."""
        self.assertEqual({field for field, _ in documented_set_commands()},
                         self.EXPECTED_FIELDS)

    def test_each_documented_value_is_valid_json(self):
        for field, value in documented_set_commands():
            with self.subTest(field=field):
                json.loads(concretize(value))

    def test_each_documented_command_is_accepted_by_the_primitive(self):
        for field, value in documented_set_commands():
            concrete = concretize(value)
            with self.subTest(field=field, value=concrete):
                proc = subprocess.run(
                    [sys.executable, os.path.join(SCRIPTS, "manifest.py"),
                     "set", "ws1", field, concrete],
                    cwd=self.vault, capture_output=True, text=True)
                self.assertNotIn("fails shape check", proc.stderr)
                self.assertEqual(proc.returncode, 0, proc.stderr)


if __name__ == "__main__":
    unittest.main()
