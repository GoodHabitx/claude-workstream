#!/usr/bin/env python3
"""Unit tests for scripts/views.py - the fleet-index + graph regenerator
(V2, V4)."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts")
sys.path.insert(0, SCRIPTS)
import workstream_lib as wslib
import manifest as mprim
import views


def make_vault():
    d = tempfile.mkdtemp(prefix="ws_views_test_")
    os.makedirs(os.path.join(d, "staff"), exist_ok=True)
    return d


class RegenerateTests(unittest.TestCase):
    def setUp(self):
        self.vault = make_vault()

    def tearDown(self):
        shutil.rmtree(self.vault, ignore_errors=True)

    def test_regen_empty_state(self):
        summary = views.regenerate(self.vault)
        self.assertEqual(summary["manifests"], 0)
        with open(summary["index_path"]) as f:
            content = f.read()
        self.assertIn("_none_", content)
        with open(summary["graph_path"]) as f:
            graph = f.read()
        self.assertIn("```mermaid", graph)

    def test_regen_reflects_manifests_and_edges(self):
        mprim.create_manifest(self.vault, "root", "root-ws", "root focus")
        mprim.create_manifest(self.vault, "child", "child-ws", "child focus",
                              spawned_from="root-ws", spawned_from_session="root")
        mprim.set_field(self.vault, "child", "direct_report", {"name": "root-ws", "session": "root"})
        summary = views.regenerate(self.vault)
        self.assertEqual(summary["manifests"], 2)
        with open(summary["index_path"]) as f:
            index = f.read()
        self.assertIn("root-ws", index)
        self.assertIn("child-ws", index)
        with open(summary["graph_path"]) as f:
            graph = f.read()
        self.assertIn("reports to", graph)

    def test_regen_flags_orphan_and_anomaly(self):
        os.makedirs(os.path.join(wslib.state_root(self.vault), "no-manifest"))
        mprim.create_manifest(self.vault, "bad", "bad-ws", "f")
        mprim.set_field(self.vault, "bad", "state", "active")
        # force an anomaly: malformed direct_report by writing raw json directly
        mpath = wslib.manifest_path_for(self.vault, "bad")
        data, _ = wslib.read_manifest(self.vault, "bad")
        data["direct_report"] = "bad-string"
        wslib.atomic_write_json(mpath, data)
        summary = views.regenerate(self.vault)
        self.assertEqual(summary["orphans"], 1)
        self.assertGreaterEqual(summary["problems"], 1)
        with open(summary["index_path"]) as f:
            index = f.read()
        self.assertIn("no-manifest", index)
        self.assertIn("Manifest anomalies", index)

    def test_regen_idempotent_no_tmp_litter(self):
        mprim.create_manifest(self.vault, "a", "a-ws", "f")
        views.regenerate(self.vault)
        views.regenerate(self.vault)
        ws_root = wslib.state_root(self.vault)
        for name in os.listdir(ws_root):
            self.assertFalse(name.endswith(".tmp") or ".tmp." in name)

    def test_cli_refuses_outside_vault(self):
        not_a_vault = tempfile.mkdtemp(prefix="ws_not_a_vault_")
        try:
            proc = subprocess.run([sys.executable, os.path.join(SCRIPTS, "views.py"),
                                  "regen", "--root", not_a_vault],
                                 capture_output=True, text=True)
            self.assertEqual(proc.returncode, 1)
            self.assertIn("refusing", proc.stderr)
        finally:
            shutil.rmtree(not_a_vault, ignore_errors=True)

    def test_regen_dry_run_touches_nothing(self):
        mprim.create_manifest(self.vault, "a", "a-ws", "f")
        summary = views.regenerate(self.vault, dry_run=True)
        self.assertEqual(summary["manifests"], 1)   # full scan still ran
        self.assertFalse(os.path.isfile(summary["index_path"]))
        self.assertFalse(os.path.isfile(summary["graph_path"]))

    def test_cli_regen_dry_run(self):
        mprim.create_manifest(self.vault, "a", "a-ws", "f")
        proc = subprocess.run([sys.executable, os.path.join(SCRIPTS, "views.py"),
                              "regen", "--dry-run"],
                             cwd=self.vault, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("would write", proc.stdout)
        self.assertFalse(os.path.isfile(os.path.join(wslib.state_root(self.vault), "index.md")))

    def test_cli_regen_success(self):
        mprim.create_manifest(self.vault, "a", "a-ws", "f")
        proc = subprocess.run([sys.executable, os.path.join(SCRIPTS, "views.py"), "regen"],
                             cwd=self.vault, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("1 manifest(s)", proc.stdout)


if __name__ == "__main__":
    unittest.main()
