#!/usr/bin/env python3
"""Crafted-stdin tests for scripts/remind.py (the UserPromptSubmit hook,
X4: dedup via the boot-stamped counter, fires every N=25 turns)."""
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
import manifest as mprim
import sidecar


def make_vault():
    d = tempfile.mkdtemp(prefix="ws_remind_test_")
    os.makedirs(os.path.join(d, "staff"), exist_ok=True)
    return d


def run_remind(vault, payload):
    return subprocess.run([sys.executable, os.path.join(SCRIPTS, "remind.py")],
                          cwd=vault, input=json.dumps(payload), capture_output=True, text=True)


class RemindCraftedStdinTests(unittest.TestCase):
    def setUp(self):
        self.vault = make_vault()

    def tearDown(self):
        shutil.rmtree(self.vault, ignore_errors=True)

    # 0. sanity: bump_and_check's own counter write goes through
    # workstream_lib's dry_run-capable primitive.
    def test_counter_write_helper_respects_dry_run(self):
        counter_path = os.path.join(wslib.sessions_root(self.vault), "sess-dry.remind.json")
        wslib.atomic_write_json(counter_path, {"count": 1}, dry_run=True)
        self.assertFalse(os.path.isfile(counter_path))

    # 1. unbound session: no output, no counter file created
    def test_unbound_no_output_no_counter_file(self):
        proc = run_remind(self.vault, {"session_id": "sess-unbound"})
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, "")
        counter = os.path.join(wslib.sessions_root(self.vault), "sess-unbound.remind.json")
        self.assertFalse(os.path.isfile(counter))

    # 2. malformed stdin: no crash, no output
    def test_malformed_stdin_no_crash(self):
        proc = subprocess.run([sys.executable, os.path.join(SCRIPTS, "remind.py")],
                              cwd=self.vault, input="{bad json", capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, "")

    # 3. bound, single call: counter increments, no identity shown yet
    def test_bound_single_call_no_fire(self):
        mprim.create_manifest(self.vault, "born-1", "ws1", "f")
        sidecar.write(self.vault, "sess-1", "born-1")
        proc = run_remind(self.vault, {"session_id": "sess-1"})
        self.assertEqual(proc.stdout, "")
        data, _ = wslib.read_json(os.path.join(wslib.sessions_root(self.vault), "sess-1.remind.json"))
        self.assertEqual(data["count"], 1)

    # 4. bound, counter pre-set to 24 -> this call fires (reaches 25) and resets to 0
    def test_bound_fires_at_25_and_resets(self):
        mprim.create_manifest(self.vault, "born-2", "ws2", "focus-two")
        sidecar.write(self.vault, "sess-2", "born-2")
        counter_path = os.path.join(wslib.sessions_root(self.vault), "sess-2.remind.json")
        wslib.atomic_write_json(counter_path, {"count": 24})
        proc = run_remind(self.vault, {"session_id": "sess-2"})
        self.assertIn("Workstream identity", proc.stdout)
        self.assertIn("ws2", proc.stdout)
        data, _ = wslib.read_json(counter_path)
        self.assertEqual(data["count"], 0)

    # 5. malformed counter file recovers to count=1, no crash
    def test_malformed_counter_file_recovers(self):
        mprim.create_manifest(self.vault, "born-3", "ws3", "f")
        sidecar.write(self.vault, "sess-3", "born-3")
        counter_path = os.path.join(wslib.sessions_root(self.vault), "sess-3.remind.json")
        os.makedirs(os.path.dirname(counter_path), exist_ok=True)
        with open(counter_path, "w") as f:
            f.write("{not json")
        proc = run_remind(self.vault, {"session_id": "sess-3"})
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, "")   # count 0 -> 1, no fire
        data, _ = wslib.read_json(counter_path)
        self.assertEqual(data["count"], 1)

    # 6. counter one below threshold does not fire
    def test_bound_one_below_threshold_no_fire(self):
        mprim.create_manifest(self.vault, "born-4", "ws4", "f")
        sidecar.write(self.vault, "sess-4", "born-4")
        counter_path = os.path.join(wslib.sessions_root(self.vault), "sess-4.remind.json")
        wslib.atomic_write_json(counter_path, {"count": 23})
        proc = run_remind(self.vault, {"session_id": "sess-4"})
        self.assertEqual(proc.stdout, "")
        data, _ = wslib.read_json(counter_path)
        self.assertEqual(data["count"], 24)


if __name__ == "__main__":
    unittest.main()
