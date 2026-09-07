#!/usr/bin/env python3
"""Crafted-stdin tests for scripts/boot.py (the SessionStart hook, X2/X3/V1).

Each test feeds boot.py a specific SessionStart stdin payload (or none/
malformed) against a purpose-built temp vault and asserts on stdout - the
"tests-first, crafted-stdin" discipline 04i calls for. Run as a subprocess
via sys.executable so the hook's own `if __name__` / reconfigure / outer
seatbelt all run for real, exactly as the harness would invoke it.
"""
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
    d = tempfile.mkdtemp(prefix="ws_boot_test_")
    os.makedirs(os.path.join(d, "staff"), exist_ok=True)
    return d


def run_boot(vault, stdin_text=None):
    kwargs = dict(cwd=vault, capture_output=True, text=True)
    if stdin_text is not None:
        kwargs["input"] = stdin_text
    else:
        kwargs["input"] = ""
    return subprocess.run([sys.executable, os.path.join(SCRIPTS, "boot.py")], **kwargs)


class BootCraftedStdinTests(unittest.TestCase):
    def setUp(self):
        self.vault = make_vault()

    def tearDown(self):
        shutil.rmtree(self.vault, ignore_errors=True)

    # 0. sanity: boot.py's own writes (reset_remind_counter) go through
    # workstream_lib's dry_run-capable primitive - confirm dry_run really
    # skips the write, since every crafted-stdin case below runs boot.py
    # for real (never dry-run) and relies on that write actually landing.
    def test_reset_remind_counter_helper_respects_dry_run(self):
        counter_path = os.path.join(wslib.sessions_root(self.vault), "sess-dry.remind.json")
        wslib.atomic_write_json(counter_path, {"count": 5}, dry_run=True)
        self.assertFalse(os.path.isfile(counter_path))

    # 1. empty stdin
    def test_empty_stdin(self):
        proc = run_boot(self.vault, "")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("no valid session_id", proc.stdout)

    # 2. malformed JSON stdin
    def test_malformed_json_stdin(self):
        proc = run_boot(self.vault, "{not json at all")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("no valid session_id", proc.stdout)

    # 3. valid JSON, no session_id key
    def test_json_missing_session_id(self):
        proc = run_boot(self.vault, json.dumps({"cwd": self.vault, "source": "startup"}))
        self.assertEqual(proc.returncode, 0)
        self.assertIn("no valid session_id", proc.stdout)

    # 4. valid session_id, unsafe characters
    def test_unsafe_session_id(self):
        proc = run_boot(self.vault, json.dumps({"session_id": "../../etc/passwd"}))
        self.assertEqual(proc.returncode, 0)
        self.assertIn("no valid session_id", proc.stdout)

    # 5. valid session_id, no sidecar at all (unbound, common case)
    def test_unbound_session_only_echo_line(self):
        proc = run_boot(self.vault, json.dumps({"session_id": "sess-unbound-1"}))
        self.assertEqual(proc.returncode, 0)
        self.assertIn("<!-- workstream-session-id: sess-unbound-1 -->", proc.stdout)
        self.assertNotIn("Workstream identity", proc.stdout)

    # 6. sidecar exists but born_session dir missing (unbound)
    def test_sidecar_points_at_missing_dir_is_unbound(self):
        sidecar.write(self.vault, "sess-2", "born-missing")
        proc = run_boot(self.vault, json.dumps({"session_id": "sess-2"}))
        self.assertEqual(proc.returncode, 0)
        self.assertNotIn("Workstream identity", proc.stdout)

    # 7. bound, full manifest with direct_report + collaborate
    def test_bound_full_identity_block(self):
        mprim.create_manifest(self.vault, "peer-born", "peer-ws", "peer focus")
        mprim.create_manifest(self.vault, "born-3", "my-ws", "get it done")
        mprim.collaborate_add(self.vault, "born-3", "peer-born", "peer-ws", scope="shared work")
        sidecar.write(self.vault, "sess-3", "born-3")
        proc = run_boot(self.vault, json.dumps({"session_id": "sess-3"}))
        self.assertEqual(proc.returncode, 0)
        self.assertIn("<!-- workstream-session-id: sess-3 -->", proc.stdout)
        self.assertIn("my-ws", proc.stdout)
        self.assertIn("get it done", proc.stdout)
        self.assertIn("peer-ws", proc.stdout)
        self.assertIn("shared work", proc.stdout)

    # 8. bound, root workstream (no direct_report)
    def test_bound_root_reports_to_nobody(self):
        mprim.create_manifest(self.vault, "born-4", "root-ws", "own focus")
        sidecar.write(self.vault, "sess-4", "born-4")
        proc = run_boot(self.vault, json.dumps({"session_id": "sess-4"}))
        self.assertIn("nobody - a root", proc.stdout)

    # 9. bound, malformed manifest.json
    def test_bound_malformed_manifest_degrades_loudly(self):
        ws_dir = os.path.join(wslib.state_root(self.vault), "born-5")
        os.makedirs(ws_dir)
        with open(os.path.join(ws_dir, "workstream.json"), "w") as f:
            f.write("{not json")
        sidecar.write(self.vault, "sess-5", "born-5")
        proc = run_boot(self.vault, json.dumps({"session_id": "sess-5"}))
        self.assertEqual(proc.returncode, 0)
        self.assertIn("Workstream identity", proc.stdout)
        self.assertIn("malformed", proc.stdout)

    # 10. bound, vault-lock absent -> warning NOTE
    def test_bound_vault_lock_absent_warns(self):
        mprim.create_manifest(self.vault, "born-6", "ws6", "f")
        sidecar.write(self.vault, "sess-6", "born-6")
        proc = run_boot(self.vault, json.dumps({"session_id": "sess-6"}))
        self.assertIn("vault-lock is not installed", proc.stdout)

    # 11. bound, vault-lock materialized -> no warning
    def test_bound_vault_lock_present_no_warning(self):
        mprim.create_manifest(self.vault, "born-7", "ws7", "f")
        sidecar.write(self.vault, "sess-7", "born-7")
        binp = os.path.join(self.vault, ".vault-meta", "bin")
        os.makedirs(binp)
        with open(os.path.join(binp, "vault-lock.sh"), "w") as f:
            f.write("#!/bin/bash\n")
        proc = run_boot(self.vault, json.dumps({"session_id": "sess-7"}))
        self.assertNotIn("vault-lock is not installed", proc.stdout)

    # 12. bound, resets remind counter to 0
    def test_bound_resets_remind_counter(self):
        mprim.create_manifest(self.vault, "born-8", "ws8", "f")
        sidecar.write(self.vault, "sess-8", "born-8")
        counter_path = os.path.join(wslib.sessions_root(self.vault), "sess-8.remind.json")
        wslib.atomic_write_json(counter_path, {"count": 24})
        run_boot(self.vault, json.dumps({"session_id": "sess-8"}))
        data, _ = wslib.read_json(counter_path)
        self.assertEqual(data["count"], 0)

    # 13. bound with an oversized field forces the identity-cap truncation note
    def test_bound_identity_block_respects_cap(self):
        mprim.create_manifest(self.vault, "born-9", "ws9", "f")
        for i in range(30):
            mprim.collaborate_add(self.vault, "born-9", "peer-%03d" % i, "a very long peer name " * 5,
                                  scope="scope text " * 5)
        sidecar.write(self.vault, "sess-9", "born-9")
        proc = run_boot(self.vault, json.dumps({"session_id": "sess-9"}))
        self.assertEqual(proc.returncode, 0)
        self.assertIn("truncated", proc.stdout)


if __name__ == "__main__":
    unittest.main()
