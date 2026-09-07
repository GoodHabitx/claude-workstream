#!/usr/bin/env python3
"""Crafted-stdin tests for scripts/precompact.py (the PreCompact hook,
04f step 7: a short ~0.3 KB note, read-only, never crashes)."""
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
import precompact as precompact_mod


def make_vault():
    d = tempfile.mkdtemp(prefix="ws_precompact_test_")
    os.makedirs(os.path.join(d, "staff"), exist_ok=True)
    return d


def run_precompact(vault, payload_text):
    return subprocess.run([sys.executable, os.path.join(SCRIPTS, "precompact.py")],
                          cwd=vault, input=payload_text, capture_output=True, text=True)


class PrecompactCraftedStdinTests(unittest.TestCase):
    def setUp(self):
        self.vault = make_vault()

    def tearDown(self):
        shutil.rmtree(self.vault, ignore_errors=True)

    # 0. sanity: precompact.py is read-only end to end (module docstring) -
    # confirm the one write primitive it could reach (atomic_write_lf, via
    # workstream_lib) still honors dry_run, even though precompact.py never
    # calls it - a boundary check on the shared primitive this hook's
    # neighbors DO call.
    def test_shared_write_primitive_respects_dry_run(self):
        path = os.path.join(self.vault, "would-not-exist.md")
        wslib.atomic_write_lf(path, "x", dry_run=True)
        self.assertFalse(os.path.isfile(path))

    # 1. no stdin at all
    def test_no_stdin(self):
        proc = run_precompact(self.vault, "")
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, "")

    # 2. malformed JSON
    def test_malformed_json(self):
        proc = run_precompact(self.vault, "{bad")
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, "")

    # 3. unbound session
    def test_unbound_session(self):
        proc = run_precompact(self.vault, json.dumps({"session_id": "sess-unbound"}))
        self.assertEqual(proc.stdout, "")

    # 4. bound, full manifest -> note includes name/focus/state
    def test_bound_note_content(self):
        mprim.create_manifest(self.vault, "born-1", "compact-ws", "finish the thing")
        mprim.set_field(self.vault, "born-1", "state", "active")
        sidecar.write(self.vault, "sess-1", "born-1")
        proc = run_precompact(self.vault, json.dumps({"session_id": "sess-1"}))
        self.assertIn("compact-ws", proc.stdout)
        self.assertIn("finish the thing", proc.stdout)
        self.assertIn("active", proc.stdout)
        self.assertIn("preserve", proc.stdout.lower())

    # 5. bound, malformed manifest -> falls back to born_session label, still prints
    def test_bound_malformed_manifest_falls_back(self):
        ws_dir = os.path.join(wslib.state_root(self.vault), "born-2")
        os.makedirs(ws_dir)
        with open(os.path.join(ws_dir, "workstream.json"), "w") as f:
            f.write("{not json")
        sidecar.write(self.vault, "sess-2", "born-2")
        proc = run_precompact(self.vault, json.dumps({"session_id": "sess-2"}))
        self.assertIn("born-2", proc.stdout)
        self.assertIn("preserve", proc.stdout.lower())

    # 6. note stays under the ~0.3 KB cap even with a very long focus
    def test_note_respects_cap_directly(self):
        mprim.create_manifest(self.vault, "born-3", "ws3", "x" * 5000)
        note = precompact_mod.build_note(self.vault, "born-3",
                                         os.path.join(wslib.state_root(self.vault), "born-3"))
        self.assertLessEqual(len(note.encode("utf-8")), precompact_mod.NOTE_CAP + 40)


if __name__ == "__main__":
    unittest.main()
