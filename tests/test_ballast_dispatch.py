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


def _load_dispatch_module():
    """`ballast-dispatch` is not an importable module name (the hyphen is
    load-bearing in the hook line), so the scope-default constants are
    loaded by path rather than restated in this file - a copy here would
    pass while the shipped constant drifted."""
    spec = importlib.util.spec_from_file_location("ballast_dispatch", DISPATCH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


dispatch_mod = _load_dispatch_module()

FAKE_SHIM = """#!/usr/bin/env python3
import sys
sys.stdout.write("FAKE-SHIM-CALLED event=%s argv=%r\\n" % (sys.argv[1], sys.argv))
sys.stdout.write("stdin-bytes=%d\\n" % len(sys.stdin.buffer.read()))
"""

# Stands in for ballast's PreToolUse approval refusal: the hook protocol's
# exit 2 with the reason on stderr. The wrapper must return this code
# VERBATIM - a wrapper that copies the output and then returns 0 turns
# every refusal into an allow.
FAKE_SHIM_REFUSES = """#!/usr/bin/env python3
import sys
sys.stdin.buffer.read()
sys.stderr.write("FAKE-BALLAST-REFUSAL: no fresh approval\\n")
sys.exit(2)
"""

EVERY_EVENT = ("SessionStart", "UserPromptSubmit", "PreToolUse",
               "PostToolUse", "PreCompact", "Stop")


def make_vault():
    d = tempfile.mkdtemp(prefix="ws_ballast_dispatch_test_")
    os.makedirs(os.path.join(d, "staff"), exist_ok=True)
    return d


def run_dispatch(vault, event, plugin_root, payload_text, extra=()):
    return subprocess.run([sys.executable, DISPATCH, event, plugin_root] + list(extra),
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


class BallastDispatchScopeMigrationTests(unittest.TestCase):
    """B3: the default a first dispatch writes is ballast 0.1.3's
    fixtures/scope-example/ballast.json verbatim, and a scope still holding
    the 0.1.1 default byte-for-byte is replaced with it. Anything else is a
    decision somebody made and is never overwritten."""

    def setUp(self):
        self.vault = make_vault()
        self.fake_root = tempfile.mkdtemp(prefix="ws_fake_plugin_root_")
        os.makedirs(os.path.join(self.fake_root, "shim"))
        with open(os.path.join(self.fake_root, "shim", "ballast-shim.py"), "w") as f:
            f.write(FAKE_SHIM)
        mprim.create_manifest(self.vault, "born-mig", "wsmig", "f")
        sidecar.write(self.vault, "sess-mig", "born-mig")
        self.scope_path = os.path.join(wslib.state_root(self.vault), "born-mig",
                                       "ballast.json")

    def tearDown(self):
        shutil.rmtree(self.vault, ignore_errors=True)
        shutil.rmtree(self.fake_root, ignore_errors=True)

    def _dispatch(self):
        return run_dispatch(self.vault, "SessionStart", self.fake_root,
                            json.dumps({"session_id": "sess-mig"}))

    def _read(self):
        with open(self.scope_path, encoding="utf-8", newline="") as handle:
            return handle.read()

    def test_the_old_default_text_is_what_0_1_1_actually_wrote(self):
        """The migration only fires on a byte-exact match, so the constant
        has to be exactly `json.dumps(old_dict, indent=2) + newline` - the
        text the wrapper itself used to produce."""
        old_dict = json.loads(dispatch_mod.OLD_DEFAULT_SCOPE_TEXT)
        self.assertEqual(json.dumps(old_dict, indent=2) + "\n",
                         dispatch_mod.OLD_DEFAULT_SCOPE_TEXT)
        self.assertEqual(old_dict["hot_cap_bytes"], 1024)

    def test_first_write_is_the_new_default_verbatim(self):
        self._dispatch()
        # Byte-exact against the constant, so the retired-recipe keys are
        # gone by construction: the written scope must equal a constant that
        # no longer carries them, not merely parse.
        self.assertEqual(self._read(), dispatch_mod.DEFAULT_SCOPE_TEXT)
        scope = json.loads(self._read())
        self.assertEqual(scope["hot_cap_bytes"], 4096)
        self.assertEqual(scope["glossary"], "glossary.md")
        self.assertEqual(sorted(scope.keys()), [
            "glossary", "glossary_cap_bytes", "hot", "hot_cap_bytes", "index",
            "log", "min_engine", "policy", "policy_cap_bytes", "regen",
            "reground_interval_turns", "required_slots", "root",
            "significant_write_rule"])

    def test_an_untouched_old_default_is_migrated(self):
        wslib.atomic_write_lf(self.scope_path, dispatch_mod.OLD_DEFAULT_SCOPE_TEXT)
        self._dispatch()
        self.assertEqual(self._read(), dispatch_mod.DEFAULT_SCOPE_TEXT)

    def test_a_customized_scope_is_never_migrated(self):
        customized = dispatch_mod.OLD_DEFAULT_SCOPE_TEXT.replace(
            '"hot_cap_bytes": 1024', '"hot_cap_bytes": 8192')
        wslib.atomic_write_lf(self.scope_path, customized)
        self._dispatch()
        self.assertEqual(self._read(), customized)

    def test_a_scope_already_on_the_new_default_is_left_alone(self):
        wslib.atomic_write_lf(self.scope_path, dispatch_mod.DEFAULT_SCOPE_TEXT)
        before = os.path.getmtime(self.scope_path)
        self._dispatch()
        self.assertEqual(self._read(), dispatch_mod.DEFAULT_SCOPE_TEXT)
        self.assertEqual(os.path.getmtime(self.scope_path), before)

    def test_migration_is_idempotent(self):
        wslib.atomic_write_lf(self.scope_path, dispatch_mod.OLD_DEFAULT_SCOPE_TEXT)
        self._dispatch()
        self._dispatch()
        self.assertEqual(self._read(), dispatch_mod.DEFAULT_SCOPE_TEXT)


class BallastDispatchPartTests(unittest.TestCase):
    """B1: the SessionStart split. Each `--part` rides its own hook command
    so it claims its own inline envelope; the wrapper's only job is to
    forward the flag untouched."""

    def setUp(self):
        self.vault = make_vault()
        self.fake_root = tempfile.mkdtemp(prefix="ws_fake_plugin_root_")
        os.makedirs(os.path.join(self.fake_root, "shim"))
        with open(os.path.join(self.fake_root, "shim", "ballast-shim.py"), "w") as f:
            f.write(FAKE_SHIM)
        mprim.create_manifest(self.vault, "born-p", "wsp", "f")
        sidecar.write(self.vault, "sess-p", "born-p")

    def tearDown(self):
        shutil.rmtree(self.vault, ignore_errors=True)
        shutil.rmtree(self.fake_root, ignore_errors=True)

    def test_part_is_forwarded_verbatim(self):
        for part in ("hot", "policy", "glossary"):
            proc = run_dispatch(self.vault, "SessionStart", self.fake_root,
                                json.dumps({"session_id": "sess-p"}),
                                extra=("--part", part))
            self.assertIn("'--part'", proc.stdout, part)
            self.assertIn("'%s'" % part, proc.stdout, part)

    def test_no_part_flag_forwards_no_part(self):
        proc = run_dispatch(self.vault, "SessionStart", self.fake_root,
                            json.dumps({"session_id": "sess-p"}))
        self.assertIn("FAKE-SHIM-CALLED", proc.stdout)
        self.assertNotIn("--part", proc.stdout)

    def test_plugin_root_positional_still_works_with_flags(self):
        """The flags may follow the two positionals an un-updated hook line
        already passes; the plugin root must still resolve from argv."""
        proc = run_dispatch(self.vault, "SessionStart", self.fake_root,
                            json.dumps({"session_id": "sess-p"}),
                            extra=("--part", "hot"))
        self.assertIn("ballast-shim.py", proc.stdout)


class BallastDispatchGlobalScopeTests(unittest.TestCase):
    """B1: the global delivery. `<state-root>/_global/ballast.json` is
    seeded by an operator; until it is, the delivery is silent - not an
    error, and never auto-created here."""

    def setUp(self):
        self.vault = make_vault()
        self.fake_root = tempfile.mkdtemp(prefix="ws_fake_plugin_root_")
        os.makedirs(os.path.join(self.fake_root, "shim"))
        with open(os.path.join(self.fake_root, "shim", "ballast-shim.py"), "w") as f:
            f.write(FAKE_SHIM)
        mprim.create_manifest(self.vault, "born-g", "wsg", "f")
        sidecar.write(self.vault, "sess-g", "born-g")
        self.global_dir = os.path.join(wslib.state_root(self.vault), "_global")

    def tearDown(self):
        shutil.rmtree(self.vault, ignore_errors=True)
        shutil.rmtree(self.fake_root, ignore_errors=True)

    def _run(self, session_id="sess-g"):
        return run_dispatch(self.vault, "SessionStart", self.fake_root,
                            json.dumps({"session_id": session_id}),
                            extra=("--part", "policy", "--scope-kind", "global"))

    def test_skips_silently_when_global_dir_is_absent(self):
        self.assertFalse(os.path.isdir(self.global_dir))
        proc = self._run()
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, "")
        self.assertEqual(proc.stderr, "")

    def test_never_creates_the_global_scope(self):
        self._run()
        self.assertFalse(os.path.exists(self.global_dir))

    def test_skips_silently_for_an_unbound_session(self):
        os.makedirs(self.global_dir)
        wslib.atomic_write_lf(os.path.join(self.global_dir, "ballast.json"), "{}\n")
        proc = self._run(session_id="sess-not-bound")
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, "")

    def test_forwards_the_global_scope_when_it_exists(self):
        os.makedirs(self.global_dir)
        wslib.atomic_write_lf(os.path.join(self.global_dir, "ballast.json"), "{}\n")
        proc = self._run()
        self.assertIn("FAKE-SHIM-CALLED event=SessionStart", proc.stdout)
        self.assertIn("_global", proc.stdout)
        # ...and the session's OWN workstream scope is not what was passed.
        self.assertNotIn("born-g", proc.stdout)


class BallastDispatchExitCodeTests(unittest.TestCase):
    """The highest-risk surface in the wrapper: swallowing the shim's exit
    2 would leave a gate that looks wired and enforces nothing."""

    def setUp(self):
        self.vault = make_vault()
        self.fake_root = tempfile.mkdtemp(prefix="ws_fake_plugin_root_")
        os.makedirs(os.path.join(self.fake_root, "shim"))
        with open(os.path.join(self.fake_root, "shim", "ballast-shim.py"), "w") as f:
            f.write(FAKE_SHIM_REFUSES)
        mprim.create_manifest(self.vault, "born-x", "wsx", "f")
        sidecar.write(self.vault, "sess-x", "born-x")

    def tearDown(self):
        shutil.rmtree(self.vault, ignore_errors=True)
        shutil.rmtree(self.fake_root, ignore_errors=True)

    def test_refusal_exit_code_propagates_verbatim(self):
        proc = run_dispatch(self.vault, "PreToolUse", self.fake_root,
                            json.dumps({"session_id": "sess-x",
                                        "tool_name": "Write",
                                        "tool_input": {"file_path": "notes.md"}}))
        self.assertEqual(proc.returncode, 2)
        self.assertIn("FAKE-BALLAST-REFUSAL", proc.stderr)

    def test_the_vendored_shim_forwards_a_refusal_and_swallows_the_rest(self):
        """End to end through THIS plugin's own copied shim, against a stub
        VENDORED engine: exit 2 (and only 2) survives. The 0.1.1 shim mapped
        every non-zero code to 0, which would have left the whole PreToolUse
        gate wired and inert."""
        plugins_root = tempfile.mkdtemp(prefix="ws_plugins_root_")
        old_env = dict(os.environ)
        try:
            own_root = os.path.join(plugins_root, "workstream")
            shutil.copytree(PLUGIN_ROOT, own_root,
                            ignore=shutil.ignore_patterns(".git", "__pycache__"))
            # The shim resolves the vendored engine first, so the stub goes
            # where this plugin vendors ballast (copytree brought the dir).
            stub = os.path.join(own_root, "vendor", "ballast", "ballast.py")
            os.environ["CLAUDE_PLUGIN_ROOT"] = own_root

            for code, expected in ((2, 2), (1, 0), (7, 0), (0, 0)):
                wslib.atomic_write_lf(stub,
                                      "#!/usr/bin/env python3\n"
                                      "import sys\n"
                                      "sys.stderr.write('STUB-BALLAST code=%d\\n')\n"
                                      "sys.exit(%d)\n" % (code, code))
                proc = subprocess.run(
                    [sys.executable,
                     os.path.join(own_root, "scripts", "ballast-dispatch.py"),
                     "PreToolUse", own_root],
                    cwd=self.vault,
                    input=json.dumps({"session_id": "sess-x", "tool_name": "Write",
                                      "tool_input": {"file_path": "notes.md"}}),
                    capture_output=True, text=True)
                self.assertEqual(proc.returncode, expected,
                                 "ballast exit %d -> hook exit %d (wanted %d)"
                                 % (code, proc.returncode, expected))
        finally:
            shutil.rmtree(plugins_root, ignore_errors=True)
            os.environ.clear()
            os.environ.update(old_env)


class BallastDispatchManifestGuardTests(unittest.TestCase):
    """B1: the wrapper's own refusal - a direct Write/Edit/NotebookEdit of a
    workstream.json under the state root. Manifests go through
    scripts/manifest.py, which is not a Write tool call, so the primitive
    itself is never caught by this."""

    def setUp(self):
        self.vault = make_vault()
        self.fake_root = tempfile.mkdtemp(prefix="ws_fake_plugin_root_")
        os.makedirs(os.path.join(self.fake_root, "shim"))
        with open(os.path.join(self.fake_root, "shim", "ballast-shim.py"), "w") as f:
            f.write(FAKE_SHIM)
        mprim.create_manifest(self.vault, "born-m", "wsm", "f")
        sidecar.write(self.vault, "sess-m", "born-m")
        self.manifest = wslib.manifest_path_for(self.vault, "born-m")

    def tearDown(self):
        shutil.rmtree(self.vault, ignore_errors=True)
        shutil.rmtree(self.fake_root, ignore_errors=True)

    def _pre_tool_use(self, payload):
        return run_dispatch(self.vault, "PreToolUse", self.fake_root,
                            json.dumps(payload))

    def test_write_to_a_manifest_is_refused(self):
        proc = self._pre_tool_use({"session_id": "sess-m", "tool_name": "Write",
                                   "tool_input": {"file_path": self.manifest}})
        self.assertEqual(proc.returncode, 2)
        self.assertIn("may not be written", proc.stderr)
        self.assertIn("manifest.py", proc.stderr)
        self.assertEqual(proc.stdout, "")

    def test_the_disarm_switch_allows_the_write_with_a_loud_note(self):
        """R0-3: `<state-root>/ballast-gate.disabled` disarms this guard
        exactly as it disarms manifest.py's approval gate - the write is
        allowed (never exit 2) with a loud stderr note, so a malformed or
        hand-corrupted manifest always has a way back."""
        open(os.path.join(wslib.state_root(self.vault),
                          "ballast-gate.disabled"), "w").close()
        proc = self._pre_tool_use({"session_id": "sess-m", "tool_name": "Write",
                                   "tool_input": {"file_path": self.manifest}})
        self.assertNotEqual(proc.returncode, 2)
        self.assertIn("manifest gate is OFF", proc.stderr)
        self.assertNotIn("may not be written", proc.stderr)

    def test_every_write_tool_is_covered(self):
        for tool, key in (("Write", "file_path"), ("Edit", "file_path"),
                          ("NotebookEdit", "notebook_path")):
            proc = self._pre_tool_use({"session_id": "sess-m", "tool_name": tool,
                                       "tool_input": {key: self.manifest}})
            self.assertEqual(proc.returncode, 2, tool)

    def test_refused_even_when_the_session_is_unbound(self):
        """The guard protects the manifest tree, not this session's own
        identity - an unbound session writing someone else's manifest is
        exactly the case worth catching."""
        proc = self._pre_tool_use({"session_id": "sess-unbound",
                                   "tool_name": "Write",
                                   "tool_input": {"file_path": self.manifest}})
        self.assertEqual(proc.returncode, 2)

    def test_a_relative_path_resolves_against_the_payload_cwd(self):
        rel = os.path.relpath(self.manifest, self.vault)
        proc = self._pre_tool_use({"session_id": "sess-m", "tool_name": "Write",
                                   "cwd": self.vault,
                                   "tool_input": {"file_path": rel}})
        self.assertEqual(proc.returncode, 2)

    def test_refused_through_a_second_spelling_of_the_same_root(self):
        """The consuming vault is reachable under two roots (one a symlink
        to the other), so the guard canonicalizes both sides - a raw string
        comparison would let a write arriving through the other spelling
        walk straight past it."""
        link = os.path.join(tempfile.mkdtemp(prefix="ws_link_"), "vault-alias")
        try:
            os.symlink(self.vault, link, target_is_directory=True)
        except (OSError, NotImplementedError, AttributeError) as exc:
            self.skipTest("symlinks unavailable on this host: %r" % exc)
        aliased = os.path.join(link, os.path.relpath(self.manifest, self.vault))
        proc = self._pre_tool_use({"session_id": "sess-m", "tool_name": "Write",
                                   "tool_input": {"file_path": aliased}})
        self.assertEqual(proc.returncode, 2)

    def test_a_workstream_json_outside_the_state_root_passes(self):
        outside = os.path.join(self.vault, "somewhere", "workstream.json")
        os.makedirs(os.path.dirname(outside))
        proc = self._pre_tool_use({"session_id": "sess-m", "tool_name": "Write",
                                   "tool_input": {"file_path": outside}})
        self.assertEqual(proc.returncode, 0)
        self.assertIn("FAKE-SHIM-CALLED", proc.stdout)   # forwarded, not refused

    def test_a_bash_command_naming_the_manifest_is_not_a_write_tool(self):
        proc = self._pre_tool_use({"session_id": "sess-m", "tool_name": "Bash",
                                   "tool_input": {"command": "cat " + self.manifest}})
        self.assertEqual(proc.returncode, 0)

    def test_an_ordinary_write_is_forwarded_to_the_shim(self):
        proc = self._pre_tool_use({"session_id": "sess-m", "tool_name": "Write",
                                   "tool_input": {"file_path":
                                                  os.path.join(self.vault, "n.md")}})
        self.assertEqual(proc.returncode, 0)
        self.assertIn("FAKE-SHIM-CALLED event=PreToolUse", proc.stdout)


class BallastDispatchUnboundGateTests(unittest.TestCase):
    """The gate binds FILES under the state root, not sessions: an UNBOUND
    session's write of one still reaches Ballast's PreToolUse gate, with the
    scope resolved from the TARGET. Unbound is this plugin's own documented
    common case, and this plugin is the only consumer wiring the gate, so a
    gate that skipped unbound sessions would guard nothing in practice."""

    def setUp(self):
        self.vault = make_vault()
        self.fake_root = tempfile.mkdtemp(prefix="ws_fake_plugin_root_")
        os.makedirs(os.path.join(self.fake_root, "shim"))
        self.shim = os.path.join(self.fake_root, "shim", "ballast-shim.py")
        with open(self.shim, "w") as f:
            f.write(FAKE_SHIM)
        self.state_root = wslib.state_root(self.vault)
        self.ws_dir = os.path.join(self.state_root, "born-u")
        os.makedirs(self.ws_dir)
        wslib.atomic_write_lf(os.path.join(self.ws_dir, "ballast.json"), "{}\n")
        self.global_dir = os.path.join(self.state_root, "_global")

    def tearDown(self):
        shutil.rmtree(self.vault, ignore_errors=True)
        shutil.rmtree(self.fake_root, ignore_errors=True)

    def _write(self, path, event="PreToolUse", tool="Write", key="file_path",
               session_id="sess-unbound"):
        return run_dispatch(self.vault, event, self.fake_root,
                            json.dumps({"session_id": session_id,
                                        "tool_name": tool,
                                        "tool_input": {key: path}}))

    def test_an_unbound_write_under_the_state_root_reaches_the_gate(self):
        proc = self._write(os.path.join(self.ws_dir, "policy.md"))
        self.assertEqual(proc.returncode, 0)
        self.assertIn("FAKE-SHIM-CALLED event=PreToolUse", proc.stdout)
        self.assertIn(os.path.join(self.ws_dir, "ballast.json").replace("\\", "\\\\")
                      if os.name == "nt"
                      else os.path.join(self.ws_dir, "ballast.json"), proc.stdout)

    def test_an_unbound_write_of_the_global_file_reaches_the_gate(self):
        """`_global/` carries no ballast.json here, so the scope comes from
        the fallback scan - the gate only needs the state root, which every
        scope under it carries."""
        os.makedirs(self.global_dir)
        proc = self._write(os.path.join(self.global_dir, "shared-policy.md"))
        self.assertEqual(proc.returncode, 0)
        self.assertIn("FAKE-SHIM-CALLED event=PreToolUse", proc.stdout)
        self.assertIn("born-u", proc.stdout)

    def test_the_global_scopes_own_ballast_json_is_preferred_when_present(self):
        os.makedirs(self.global_dir)
        wslib.atomic_write_lf(os.path.join(self.global_dir, "ballast.json"), "{}\n")
        proc = self._write(os.path.join(self.global_dir, "shared-policy.md"))
        self.assertIn("_global", proc.stdout)
        self.assertNotIn("born-u", proc.stdout)

    def test_every_write_tool_reaches_the_gate(self):
        for tool, key in (("Write", "file_path"), ("Edit", "file_path"),
                          ("NotebookEdit", "notebook_path")):
            proc = self._write(os.path.join(self.ws_dir, "policy.md"),
                               tool=tool, key=key)
            self.assertIn("FAKE-SHIM-CALLED", proc.stdout, tool)

    def test_nothing_is_created_by_a_gate_check(self):
        os.makedirs(self.global_dir)
        self._write(os.path.join(self.global_dir, "shared-policy.md"))
        self._write(os.path.join(self.state_root, "born-new", "policy.md"))
        self.assertFalse(os.path.isfile(os.path.join(self.global_dir, "ballast.json")))
        self.assertFalse(os.path.exists(os.path.join(self.state_root, "born-new")))

    def test_a_write_outside_the_state_root_is_still_a_silent_no_op(self):
        proc = self._write(os.path.join(self.vault, "notes.md"))
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, "")
        self.assertEqual(proc.stderr, "")

    def test_a_non_write_tool_is_still_a_silent_no_op(self):
        proc = self._write(os.path.join(self.ws_dir, "policy.md"),
                           tool="Bash", key="command")
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, "")

    def test_only_pre_tool_use_forwards_for_an_unbound_session(self):
        for event in ("SessionStart", "UserPromptSubmit", "PostToolUse",
                      "PreCompact", "Stop"):
            proc = self._write(os.path.join(self.ws_dir, "policy.md"), event=event)
            self.assertEqual(proc.returncode, 0, event)
            self.assertEqual(proc.stdout, "", event)

    def test_the_global_scope_kind_still_needs_a_bound_session(self):
        proc = subprocess.run(
            [sys.executable, DISPATCH, "PreToolUse", self.fake_root,
             "--scope-kind", "global"],
            cwd=self.vault, capture_output=True, text=True,
            input=json.dumps({"session_id": "sess-unbound", "tool_name": "Write",
                              "tool_input": {"file_path":
                                             os.path.join(self.ws_dir, "policy.md")}}))
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, "")

    def test_no_scope_anywhere_under_the_root_is_a_silent_no_op(self):
        os.unlink(os.path.join(self.ws_dir, "ballast.json"))
        proc = self._write(os.path.join(self.ws_dir, "policy.md"))
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, "")

    def test_an_unbound_refusal_propagates_verbatim(self):
        """The acceptance case: with Ballast refusing, the UNBOUND session's
        write of a gated file exits 2 - not 0."""
        with open(self.shim, "w") as f:
            f.write(FAKE_SHIM_REFUSES)
        proc = self._write(os.path.join(self.ws_dir, "policy.md"))
        self.assertEqual(proc.returncode, 2)
        self.assertIn("FAKE-BALLAST-REFUSAL", proc.stderr)


class BallastDispatchUnboundTests(unittest.TestCase):
    def setUp(self):
        self.vault = make_vault()
        self.fake_root = tempfile.mkdtemp(prefix="ws_fake_plugin_root_")
        os.makedirs(os.path.join(self.fake_root, "shim"))
        with open(os.path.join(self.fake_root, "shim", "ballast-shim.py"), "w") as f:
            f.write(FAKE_SHIM)

    def tearDown(self):
        shutil.rmtree(self.vault, ignore_errors=True)
        shutil.rmtree(self.fake_root, ignore_errors=True)

    def test_no_output_and_exit_zero_on_every_event(self):
        for event in EVERY_EVENT:
            proc = run_dispatch(self.vault, event, self.fake_root,
                                json.dumps({"session_id": "sess-unbound"}))
            self.assertEqual(proc.returncode, 0, event)
            self.assertEqual(proc.stdout, "", event)
            self.assertEqual(proc.stderr, "", event)

    def test_no_output_when_stdin_carries_no_session_id(self):
        for event in EVERY_EVENT:
            proc = run_dispatch(self.vault, event, self.fake_root, "{}")
            self.assertEqual(proc.returncode, 0, event)
            self.assertEqual(proc.stdout, "", event)


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
        # Absent now means: no vendored engine AND no installed sibling. The
        # real plugin tree carries vendor/ballast/, so copy it and remove the
        # vendored copy to reach the fail-open path.
        plugins_root = tempfile.mkdtemp(prefix="ws_plugins_root_")
        try:
            own_root = os.path.join(plugins_root, "workstream")
            shutil.copytree(PLUGIN_ROOT, own_root,
                            ignore=shutil.ignore_patterns(".git", "__pycache__"))
            shutil.rmtree(os.path.join(own_root, "vendor"), ignore_errors=True)
            mprim.create_manifest(self.vault, "born-1", "ws1", "f")
            sidecar.write(self.vault, "sess-1", "born-1")
            proc = run_dispatch(self.vault, "SessionStart", own_root,
                                json.dumps({"session_id": "sess-1"}))
            self.assertEqual(proc.returncode, 0)
            self.assertIn("no ballast engine found", proc.stderr)
        finally:
            shutil.rmtree(plugins_root, ignore_errors=True)

    def test_real_shim_invokes_the_vendored_engine_first(self):
        """The production path: ballast-dispatch -> the REAL copied shim ->
        the engine THIS plugin vendors under vendor/ballast/, ahead of any
        installed sibling."""
        plugins_root = tempfile.mkdtemp(prefix="ws_plugins_root_")
        try:
            own_root = os.path.join(plugins_root, "workstream")
            shutil.copytree(PLUGIN_ROOT, own_root,
                            ignore=shutil.ignore_patterns(".git", "__pycache__"))
            # A sibling install exists too, but the vendored engine must win.
            ballast_scripts = os.path.join(plugins_root, "ballast", "scripts")
            os.makedirs(ballast_scripts)
            with open(os.path.join(ballast_scripts, "ballast.py"), "w") as f:
                f.write("#!/usr/bin/env python3\nimport sys\n"
                        "sys.stdout.write('SIBLING-RAN\\n')\n")
            wslib.atomic_write_lf(
                os.path.join(own_root, "vendor", "ballast", "ballast.py"),
                "#!/usr/bin/env python3\nimport sys\n"
                "sys.stdout.write('VENDORED-RAN argv=%r\\n' % (sys.argv[1:],))\n")
            mprim.create_manifest(self.vault, "born-2", "ws2", "f")
            sidecar.write(self.vault, "sess-2", "born-2")
            os.environ["CLAUDE_PLUGIN_ROOT"] = own_root
            proc = subprocess.run(
                [sys.executable, os.path.join(own_root, "scripts", "ballast-dispatch.py"),
                 "SessionStart", own_root],
                cwd=self.vault, input=json.dumps({"session_id": "sess-2"}),
                capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0)
            self.assertIn("VENDORED-RAN", proc.stdout)
            self.assertNotIn("SIBLING-RAN", proc.stdout)
            self.assertIn("SessionStart", proc.stdout)
        finally:
            shutil.rmtree(plugins_root, ignore_errors=True)

    def test_real_shim_falls_back_to_a_sibling_ballast_install(self):
        """When no engine is vendored, the shim falls back to a sibling
        ballast install - the restart-#1 safety net before de-plugin."""
        plugins_root = tempfile.mkdtemp(prefix="ws_plugins_root_")
        try:
            own_root = os.path.join(plugins_root, "workstream")
            shutil.copytree(PLUGIN_ROOT, own_root,
                            ignore=shutil.ignore_patterns(".git", "__pycache__"))
            shutil.rmtree(os.path.join(own_root, "vendor"), ignore_errors=True)
            ballast_scripts = os.path.join(plugins_root, "ballast", "scripts")
            os.makedirs(ballast_scripts)
            with open(os.path.join(ballast_scripts, "ballast.py"), "w") as f:
                f.write("#!/usr/bin/env python3\nimport sys\n"
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
