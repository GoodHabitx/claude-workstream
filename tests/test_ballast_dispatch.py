#!/usr/bin/env python3
"""Tests for scripts/ballast-dispatch.py - the wrapper hook that resolves a
bound session's OWN workstream dir as the ballast scope and forwards to
shim/ballast-shim.py (the byte-identical copy from claude-ballast).

A FAKE shim (not the real one) stands in for shim/ballast-shim.py in most
of these tests, so they exercise ballast-dispatch's OWN resolution logic
(unbound no-op, ballast.json auto-create, argv/stdin forwarding) without
depending on the real ballast engine's behavior - that engine has its own
test suite in the ballast repo. One test point at the REAL copied shim to
confirm the fail-open contract end to end when ballast itself is absent.
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts")
PLUGIN_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, SCRIPTS)
import workstream_lib as wslib
import manifest as mprim
import sidecar

DISPATCH = os.path.join(SCRIPTS, "ballast-dispatch.py")

FAKE_SHIM = """#!/usr/bin/env python3
import sys
sys.stdout.write("FAKE-SHIM-CALLED event=%s argv=%r\\n" % (sys.argv[1], sys.argv))
sys.stdout.write("stdin-bytes=%d\\n" % len(sys.stdin.buffer.read()))
"""


def make_vault():
    d = tempfile.mkdtemp(prefix="ws_ballast_dispatch_test_")
    os.makedirs(os.path.join(d, "staff"), exist_ok=True)
    return d


def run_dispatch(vault, event, plugin_root, payload_text):
    return subprocess.run([sys.executable, DISPATCH, event, plugin_root],
                          cwd=vault, input=payload_text, capture_output=True, text=True)


class BallastDispatchFakeShimTests(unittest.TestCase):
    def setUp(self):
        self.vault = make_vault()
        self.fake_root = tempfile.mkdtemp(prefix="ws_fake_plugin_root_")
        shim_dir = os.path.join(self.fake_root, "shim")
        os.makedirs(shim_dir)
        with open(os.path.join(shim_dir, "ballast-shim.py"), "w") as f:
            f.write(FAKE_SHIM)

    def tearDown(self):
        shutil.rmtree(self.vault, ignore_errors=True)
        shutil.rmtree(self.fake_root, ignore_errors=True)

    # 0. sanity: ensure_scope's own ballast.json write goes through
    # workstream_lib's dry_run-capable primitive.
    def test_ensure_scope_write_helper_respects_dry_run(self):
        path = os.path.join(self.vault, "would-not-exist-ballast.json")
        wslib.atomic_write_lf(path, "{}", dry_run=True)
        self.assertFalse(os.path.isfile(path))

    def test_unbound_session_is_a_noop(self):
        proc = run_dispatch(self.vault, "SessionStart", self.fake_root,
                            json.dumps({"session_id": "sess-unbound"}))
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, "")   # fake shim never invoked

    def test_bound_session_forwards_event_and_scope(self):
        mprim.create_manifest(self.vault, "born-1", "ws1", "f")
        sidecar.write(self.vault, "sess-1", "born-1")
        proc = run_dispatch(self.vault, "PreCompact", self.fake_root,
                            json.dumps({"session_id": "sess-1"}))
        self.assertIn("FAKE-SHIM-CALLED event=PreCompact", proc.stdout)
        self.assertIn("--scope", proc.stdout)
        ws_dir = os.path.join(wslib.state_root(self.vault), "born-1")
        self.assertIn(ws_dir.replace("\\", "\\\\") if os.name == "nt" else ws_dir, proc.stdout)

    def test_bound_session_forwards_stdin_bytes(self):
        mprim.create_manifest(self.vault, "born-2", "ws2", "f")
        sidecar.write(self.vault, "sess-2", "born-2")
        payload = json.dumps({"session_id": "sess-2", "extra": "x" * 50})
        proc = run_dispatch(self.vault, "Stop", self.fake_root, payload)
        self.assertIn("stdin-bytes=%d" % len(payload.encode("utf-8")), proc.stdout)

    def test_ballast_json_auto_created_once(self):
        mprim.create_manifest(self.vault, "born-3", "ws3", "f")
        sidecar.write(self.vault, "sess-3", "born-3")
        scope_path = os.path.join(wslib.state_root(self.vault), "born-3", "ballast.json")
        self.assertFalse(os.path.isfile(scope_path))
        run_dispatch(self.vault, "SessionStart", self.fake_root, json.dumps({"session_id": "sess-3"}))
        self.assertTrue(os.path.isfile(scope_path))
        with open(scope_path) as f:
            scope = json.load(f)
        self.assertEqual(scope["hot"], "hot.md")
        self.assertIn("focus", scope["required_slots"])

        # customize it, then confirm a second dispatch never overwrites it
        scope["hot_cap_bytes"] = 999
        wslib.atomic_write_json(scope_path, scope)
        run_dispatch(self.vault, "UserPromptSubmit", self.fake_root, json.dumps({"session_id": "sess-3"}))
        with open(scope_path) as f:
            scope_after = json.load(f)
        self.assertEqual(scope_after["hot_cap_bytes"], 999)


class BallastDispatchRealShimFailOpenTests(unittest.TestCase):
    """Points ballast-dispatch at THIS plugin's real, copied shim, with no
    ballast plugin installed anywhere findable - the fail-open contract
    (ballast HARD-gates adopt, but a hook must never crash a turn)."""

    def setUp(self):
        self.vault = make_vault()
        self._old_env = dict(os.environ)
        os.environ.pop("CLAUDE_PLUGIN_ROOT", None)
        os.environ["HOME"] = tempfile.mkdtemp(prefix="ws_home_")
        os.environ.pop("USERPROFILE", None)

    def tearDown(self):
        shutil.rmtree(self.vault, ignore_errors=True)
        os.environ.clear()
        os.environ.update(self._old_env)

    def test_real_shim_fails_open_when_ballast_absent(self):
        mprim.create_manifest(self.vault, "born-1", "ws1", "f")
        sidecar.write(self.vault, "sess-1", "born-1")
        proc = run_dispatch(self.vault, "SessionStart", PLUGIN_ROOT,
                            json.dumps({"session_id": "sess-1"}))
        self.assertEqual(proc.returncode, 0)
        self.assertIn("no installed `ballast` plugin found", proc.stderr)

    def test_real_shim_finds_and_invokes_a_sibling_ballast_install(self):
        """End-to-end: ballast-dispatch -> the REAL copied shim -> a stub
        ballast.py installed as a sibling plugin under CLAUDE_PLUGIN_ROOT's
        own parent - confirms the whole chain resolves and runs when
        ballast IS present, not just the absent-path."""
        plugins_root = tempfile.mkdtemp(prefix="ws_plugins_root_")
        try:
            own_root = os.path.join(plugins_root, "workstream")
            shutil.copytree(PLUGIN_ROOT, own_root)
            ballast_scripts = os.path.join(plugins_root, "ballast", "scripts")
            os.makedirs(ballast_scripts)
            with open(os.path.join(ballast_scripts, "ballast.py"), "w") as f:
                f.write("#!/usr/bin/env python3\n"
                       "import sys\n"
                       "sys.stdout.write('STUB-BALLAST-RAN argv=%r\\n' % (sys.argv[1:],))\n")

            mprim.create_manifest(self.vault, "born-2", "ws2", "f")
            sidecar.write(self.vault, "sess-2", "born-2")
            os.environ["CLAUDE_PLUGIN_ROOT"] = own_root
            proc = subprocess.run(
                [sys.executable, os.path.join(own_root, "scripts", "ballast-dispatch.py"),
                 "SessionStart", own_root],
                cwd=self.vault, input=json.dumps({"session_id": "sess-2"}),
                capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0)
            self.assertIn("STUB-BALLAST-RAN", proc.stdout)
            self.assertIn("SessionStart", proc.stdout)
        finally:
            shutil.rmtree(plugins_root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
