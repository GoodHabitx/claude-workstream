#!/usr/bin/env python3
"""Unit tests for scripts/sidecar.py - the self-healing primitive (I5, L3)."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts")
sys.path.insert(0, SCRIPTS)
import workstream_lib as wslib
import sidecar


def make_vault():
    d = tempfile.mkdtemp(prefix="ws_sidecar_test_")
    os.makedirs(os.path.join(d, "staff"), exist_ok=True)
    return d


class SidecarFunctionTests(unittest.TestCase):
    def setUp(self):
        self.vault = make_vault()
        self.root = tempfile.mkdtemp(prefix="ws_plugin_root_")

    def tearDown(self):
        shutil.rmtree(self.vault, ignore_errors=True)
        shutil.rmtree(self.root, ignore_errors=True)

    def test_write_then_resolve(self):
        sidecar.write(self.vault, "sess-1", "born-a", root=self.root)
        os.makedirs(os.path.join(wslib.state_root(self.vault, self.root), "born-a"))
        result = sidecar.resolve(self.vault, "sess-1", self.root)
        self.assertEqual(result["born_session"], "born-a")

    def test_write_refuses_unsafe_ids(self):
        with self.assertRaises(ValueError):
            sidecar.write(self.vault, "../etc/passwd", "born-a", root=self.root)
        with self.assertRaises(ValueError):
            sidecar.write(self.vault, "sess-1", "../../x", root=self.root)

    def test_self_heal_from_missing(self):
        note = sidecar.self_heal(self.vault, "sess-2", "born-b", root=self.root)
        self.assertIn("had NO sidecar", note)
        result = sidecar.resolve(self.vault, "sess-2", self.root)
        self.assertIsNone(result)   # dir born-b doesn't exist yet - still unbound
        os.makedirs(os.path.join(wslib.state_root(self.vault, self.root), "born-b"))
        result = sidecar.resolve(self.vault, "sess-2", self.root)
        self.assertEqual(result["born_session"], "born-b")

    def test_self_heal_corrects_stale_pointer(self):
        sidecar.write(self.vault, "sess-3", "born-old", root=self.root)
        note = sidecar.self_heal(self.vault, "sess-3", "born-new", root=self.root)
        self.assertIn("STALE", note)
        self.assertIn("born-old", note)
        self.assertIn("born-new", note)

    def test_self_heal_same_binding_reports_no_change(self):
        sidecar.write(self.vault, "sess-4", "born-same", root=self.root)
        note = sidecar.self_heal(self.vault, "sess-4", "born-same", root=self.root)
        self.assertIn("already pointed at", note)

    def test_check_missing(self):
        self.assertEqual(sidecar.check(self.vault, "sess-5", "born-x", self.root), "missing")

    def test_check_match(self):
        sidecar.write(self.vault, "sess-6", "born-y", root=self.root)
        self.assertEqual(sidecar.check(self.vault, "sess-6", "born-y", self.root), "match")

    def test_write_dry_run_touches_nothing(self):
        path = sidecar.write(self.vault, "sess-dry", "born-dry", root=self.root, dry_run=True)
        self.assertFalse(os.path.isfile(path))
        self.assertIsNone(sidecar.resolve(self.vault, "sess-dry", self.root))

    def test_check_mismatch(self):
        sidecar.write(self.vault, "sess-7", "born-z", root=self.root)
        result = sidecar.check(self.vault, "sess-7", "born-different", self.root)
        self.assertTrue(result.startswith("mismatch"))


class SidecarCLITests(unittest.TestCase):
    def setUp(self):
        self.vault = make_vault()

    def tearDown(self):
        shutil.rmtree(self.vault, ignore_errors=True)

    def _run(self, *args):
        return subprocess.run([sys.executable, os.path.join(SCRIPTS, "sidecar.py")] + list(args),
                              cwd=self.vault, capture_output=True, text=True)

    def test_cli_resolve_unbound_exit1(self):
        proc = self._run("resolve", "sess-cli-1")
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(json.loads(proc.stdout), {})

    def test_cli_write_dry_run_touches_nothing(self):
        proc = self._run("write", "sess-cli-dry", "born-cli-dry", "--dry-run")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("DRY-RUN", proc.stdout)
        sidecar_path = os.path.join(self.vault, "staff", "cos", "workstream-sessions", "sess-cli-dry.json")
        self.assertFalse(os.path.isfile(sidecar_path))

    def test_cli_write_then_resolve(self):
        proc = self._run("write", "sess-cli-2", "born-cli")
        self.assertEqual(proc.returncode, 0)
        os.makedirs(os.path.join(self.vault, "staff", "cos", "workstreams", "born-cli"))
        proc = self._run("resolve", "sess-cli-2")
        self.assertEqual(proc.returncode, 0)
        data = json.loads(proc.stdout)
        self.assertEqual(data["born_session"], "born-cli")


if __name__ == "__main__":
    unittest.main()
