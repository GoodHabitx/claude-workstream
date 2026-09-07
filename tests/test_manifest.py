#!/usr/bin/env python3
"""Unit tests for scripts/manifest.py - the single-writer primitive (I3-I5)
plus the one sanctioned cross-manifest write, absorb_close (AB1-AB7)."""
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts")
sys.path.insert(0, SCRIPTS)
import workstream_lib as wslib
import manifest as mprim


def make_vault():
    d = tempfile.mkdtemp(prefix="ws_manifest_test_")
    os.makedirs(os.path.join(d, "staff"), exist_ok=True)
    return d


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


class SetFieldTests(unittest.TestCase):
    def setUp(self):
        self.vault = make_vault()
        self.root = tempfile.mkdtemp(prefix="ws_plugin_root_")
        mprim.create_manifest(self.vault, "born-1", "a", "f", root=self.root)

    def tearDown(self):
        shutil.rmtree(self.vault, ignore_errors=True)
        shutil.rmtree(self.root, ignore_errors=True)

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
        mprim.set_field(self.vault, "born-1", "direct_report", {"name": "up", "session": "s"}, root=self.root)
        data, _ = wslib.read_manifest(self.vault, "born-1", self.root)
        self.assertEqual(data["direct_report"]["name"], "up")

    def test_set_refuses_legacy_field(self):
        with self.assertRaises(ValueError):
            mprim.set_field(self.vault, "born-1", "parents", ["x"], root=self.root)
        with self.assertRaises(ValueError):
            mprim.set_field(self.vault, "born-1", "rebound", ["x"], root=self.root)

    def test_set_on_missing_manifest_raises(self):
        with self.assertRaises(FileNotFoundError):
            mprim.set_field(self.vault, "no-such-born", "focus", "x", root=self.root)


class CollaborateTests(unittest.TestCase):
    def setUp(self):
        self.vault = make_vault()
        self.root = tempfile.mkdtemp(prefix="ws_plugin_root_")
        mprim.create_manifest(self.vault, "a", "a", "f", root=self.root)

    def tearDown(self):
        shutil.rmtree(self.vault, ignore_errors=True)
        shutil.rmtree(self.root, ignore_errors=True)

    def test_add_is_additive(self):
        mprim.collaborate_add(self.vault, "a", "b", "peer-b", scope="shared thing", root=self.root)
        data, _ = wslib.read_manifest(self.vault, "a", self.root)
        self.assertEqual(len(data["collaborate"]), 1)
        self.assertEqual(data["collaborate"][0]["session"], "b")
        self.assertEqual(data["collaborate"][0]["scope"], "shared thing")

    def test_add_duplicate_is_idempotent_never_a_second_entry(self):
        mprim.collaborate_add(self.vault, "a", "b", "peer-b", root=self.root)
        mprim.collaborate_add(self.vault, "a", "b", "peer-b-renamed", root=self.root)
        data, _ = wslib.read_manifest(self.vault, "a", self.root)
        self.assertEqual(len(data["collaborate"]), 1)

    def test_add_never_touches_unrelated_entries(self):
        mprim.collaborate_add(self.vault, "a", "b", "peer-b", root=self.root)
        mprim.collaborate_add(self.vault, "a", "c", "peer-c", root=self.root)
        data, _ = wslib.read_manifest(self.vault, "a", self.root)
        self.assertEqual({e["session"] for e in data["collaborate"]}, {"b", "c"})

    def test_remove_drops_only_the_named_peer(self):
        mprim.collaborate_add(self.vault, "a", "b", "peer-b", root=self.root)
        mprim.collaborate_add(self.vault, "a", "c", "peer-c", root=self.root)
        mprim.collaborate_remove(self.vault, "a", "b", root=self.root)
        data, _ = wslib.read_manifest(self.vault, "a", self.root)
        self.assertEqual([e["session"] for e in data["collaborate"]], ["c"])


class AppendAbsorbedTests(unittest.TestCase):
    def setUp(self):
        self.vault = make_vault()
        self.root = tempfile.mkdtemp(prefix="ws_plugin_root_")
        mprim.create_manifest(self.vault, "overtaker", "overtaker", "f", root=self.root)

    def tearDown(self):
        shutil.rmtree(self.vault, ignore_errors=True)
        shutil.rmtree(self.root, ignore_errors=True)

    def test_append_absorbed_is_additive(self):
        mprim.append_absorbed(self.vault, "overtaker", "stale-ws", "stale-born",
                              "staff/cos/workstreams/stale-born", "path/to.jsonl", root=self.root)
        data, _ = wslib.read_manifest(self.vault, "overtaker", self.root)
        self.assertEqual(len(data["absorbed"]), 1)
        self.assertEqual(data["absorbed"][0]["born_session"], "stale-born")
        self.assertEqual(data["absorbed"][0]["transcript"], "path/to.jsonl")

    def test_append_absorbed_duplicate_idempotent(self):
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


if __name__ == "__main__":
    unittest.main()
