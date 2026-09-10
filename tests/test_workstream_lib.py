#!/usr/bin/env python3
"""Unit tests for scripts/workstream_lib.py. Plain unittest (no pytest) -
runnable as `python3 tests/test_workstream_lib.py` (WSL/Linux) or
`py -3 tests\\test_workstream_lib.py` (Windows). Uses a temp directory as
the vault root throughout - never touches the real vault.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
import workstream_lib as wslib


def make_vault():
    d = tempfile.mkdtemp(prefix="ws_lib_test_")
    os.makedirs(os.path.join(d, "staff"), exist_ok=True)
    return d


class ConfigTests(unittest.TestCase):
    def test_default_config_when_missing(self):
        root = tempfile.mkdtemp(prefix="ws_plugin_root_")
        try:
            cfg = wslib.load_config(root)
            self.assertEqual(cfg["state_root"], ".vault-meta/workstreams")
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_config_override(self):
        root = tempfile.mkdtemp(prefix="ws_plugin_root_")
        try:
            with open(os.path.join(root, "config.json"), "w") as f:
                json.dump({"state_root": "custom/root"}, f)
            cfg = wslib.load_config(root)
            self.assertEqual(cfg["state_root"], "custom/root")
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_malformed_config_falls_back(self):
        root = tempfile.mkdtemp(prefix="ws_plugin_root_")
        try:
            with open(os.path.join(root, "config.json"), "w") as f:
                f.write("{not json")
            cfg = wslib.load_config(root)
            self.assertEqual(cfg["state_root"], ".vault-meta/workstreams")
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_state_root_and_sessions_root_siblings(self):
        vault = make_vault()
        try:
            root = tempfile.mkdtemp(prefix="ws_plugin_root_")
            sr = wslib.state_root(vault, root)
            sess = wslib.sessions_root(vault, root)
            self.assertEqual(sr, os.path.join(vault, ".vault-meta", "workstreams"))
            self.assertEqual(sess, os.path.join(vault, ".vault-meta", "workstream-sessions"))
            shutil.rmtree(root, ignore_errors=True)
        finally:
            shutil.rmtree(vault, ignore_errors=True)


class VaultMarkerTests(unittest.TestCase):
    def test_looks_like_vault_true(self):
        vault = make_vault()
        try:
            self.assertTrue(wslib.looks_like_vault(vault))
        finally:
            shutil.rmtree(vault, ignore_errors=True)

    def test_looks_like_vault_false(self):
        vault = tempfile.mkdtemp(prefix="ws_not_a_vault_")
        try:
            self.assertFalse(wslib.looks_like_vault(vault))
        finally:
            shutil.rmtree(vault, ignore_errors=True)


class ResolveBoundDirTests(unittest.TestCase):
    def setUp(self):
        self.vault = make_vault()
        self.root = tempfile.mkdtemp(prefix="ws_plugin_root_")

    def tearDown(self):
        shutil.rmtree(self.vault, ignore_errors=True)
        shutil.rmtree(self.root, ignore_errors=True)

    def _write_sidecar(self, session_id, born_session):
        path = os.path.join(wslib.sessions_root(self.vault, self.root), session_id + ".json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump({"session_id": session_id, "born_session": born_session}, f)

    def test_no_sidecar_is_unbound(self):
        born, ws_dir = wslib.resolve_bound_dir(self.vault, "sess-1", self.root)
        self.assertIsNone(born)
        self.assertIsNone(ws_dir)

    def test_unsafe_session_id_is_unbound(self):
        born, ws_dir = wslib.resolve_bound_dir(self.vault, "../etc/passwd", self.root)
        self.assertIsNone(born)

    def test_sidecar_but_missing_dir_is_unbound(self):
        self._write_sidecar("sess-2", "born-xyz")
        born, ws_dir = wslib.resolve_bound_dir(self.vault, "sess-2", self.root)
        self.assertIsNone(born)   # born-xyz dir does not exist on disk

    def test_bound_when_dir_exists(self):
        self._write_sidecar("sess-3", "born-abc")
        os.makedirs(os.path.join(wslib.state_root(self.vault, self.root), "born-abc"))
        born, ws_dir = wslib.resolve_bound_dir(self.vault, "sess-3", self.root)
        self.assertEqual(born, "born-abc")
        self.assertTrue(ws_dir.endswith("born-abc"))

    def test_oversized_sidecar_is_unbound(self):
        path = os.path.join(wslib.sessions_root(self.vault, self.root), "sess-4.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(json.dumps({"born_session": "x", "junk": "a" * (wslib.WS_BINDING_CAP + 10)}))
        born, ws_dir = wslib.resolve_bound_dir(self.vault, "sess-4", self.root)
        self.assertIsNone(born)


class ManifestScanTests(unittest.TestCase):
    def setUp(self):
        self.vault = make_vault()
        self.root = tempfile.mkdtemp(prefix="ws_plugin_root_")
        self.ws_root = wslib.state_root(self.vault, self.root)
        os.makedirs(self.ws_root, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.vault, ignore_errors=True)
        shutil.rmtree(self.root, ignore_errors=True)

    def _write_manifest(self, born, data):
        d = os.path.join(self.ws_root, born)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "workstream.json"), "w") as f:
            json.dump(data, f)

    def test_discover_orphan_dir(self):
        os.makedirs(os.path.join(self.ws_root, "no-manifest-here"))
        manifests, orphans = wslib.discover_manifests(self.ws_root)
        self.assertEqual(manifests, {})
        self.assertEqual(orphans, [("no-manifest-here", "no workstream.json")])

    def test_discover_malformed_manifest_is_orphan(self):
        d = os.path.join(self.ws_root, "broken")
        os.makedirs(d)
        with open(os.path.join(d, "workstream.json"), "w") as f:
            f.write("{not json")
        manifests, orphans = wslib.discover_manifests(self.ws_root)
        self.assertEqual(len(orphans), 1)
        self.assertIn("broken", orphans[0][0])

    def test_find_problems_invalid_state(self):
        self._write_manifest("a", {"name": "a", "state": "bogus"})
        manifests, _ = wslib.discover_manifests(self.ws_root)
        problems = wslib.find_problems(manifests)
        kinds = [k for k, _d in problems]
        self.assertIn("invalid state", kinds)

    def test_find_problems_malformed_direct_report(self):
        self._write_manifest("a", {"name": "a", "state": "active", "direct_report": "comms-desk"})
        manifests, _ = wslib.discover_manifests(self.ws_root)
        problems = wslib.find_problems(manifests)
        kinds = [k for k, _d in problems]
        self.assertIn("malformed direct_report", kinds)

    def test_find_problems_dangling_direct_report(self):
        self._write_manifest("a", {"name": "a", "state": "active",
                                   "direct_report": {"name": "ghost", "session": "nope"}})
        manifests, _ = wslib.discover_manifests(self.ws_root)
        problems = wslib.find_problems(manifests)
        kinds = [k for k, _d in problems]
        self.assertIn("dangling direct_report", kinds)

    def test_find_problems_stale_direct_report_closed_target(self):
        self._write_manifest("b", {"name": "b", "state": "closed"})
        self._write_manifest("a", {"name": "a", "state": "active",
                                   "direct_report": {"name": "b", "session": "b"}})
        manifests, _ = wslib.discover_manifests(self.ws_root)
        problems = wslib.find_problems(manifests)
        kinds = [k for k, _d in problems]
        self.assertIn("stale direct_report (re-home)", kinds)

    def test_find_problems_born_session_mismatch(self):
        """R0-5: born_session IS the directory name (I1); a manifest whose
        born_session no longer matches its dir is flagged (the identity was
        rewritten, or the dir moved)."""
        self._write_manifest("a", {"name": "a", "state": "active",
                                   "born_session": "HACKED"})
        manifests, _ = wslib.discover_manifests(self.ws_root)
        problems = wslib.find_problems(manifests)
        kinds = [k for k, _d in problems]
        self.assertIn("born_session mismatch", kinds)

    def test_find_problems_born_session_match_is_clean(self):
        self._write_manifest("a", {"name": "a", "state": "active",
                                   "born_session": "a"})
        manifests, _ = wslib.discover_manifests(self.ws_root)
        kinds = [k for k, _d in wslib.find_problems(manifests)]
        self.assertNotIn("born_session mismatch", kinds)

    def test_find_problems_one_sided_collaborate(self):
        self._write_manifest("b", {"name": "b", "state": "active", "collaborate": []})
        self._write_manifest("a", {"name": "a", "state": "active",
                                   "collaborate": [{"name": "b", "session": "b"}]})
        manifests, _ = wslib.discover_manifests(self.ws_root)
        problems = wslib.find_problems(manifests)
        kinds = [k for k, _d in problems]
        self.assertIn("one-sided collaborate", kinds)

    def test_find_problems_reciprocal_collaborate_clean(self):
        self._write_manifest("b", {"name": "b", "state": "active",
                                   "collaborate": [{"name": "a", "session": "a"}]})
        self._write_manifest("a", {"name": "a", "state": "active",
                                   "collaborate": [{"name": "b", "session": "b"}]})
        manifests, _ = wslib.discover_manifests(self.ws_root)
        problems = wslib.find_problems(manifests)
        kinds = [k for k, _d in problems]
        self.assertNotIn("one-sided collaborate", kinds)

    def test_find_problems_legacy_parents_flagged(self):
        self._write_manifest("a", {"name": "a", "state": "active", "parents": ["x"]})
        manifests, _ = wslib.discover_manifests(self.ws_root)
        problems = wslib.find_problems(manifests)
        kinds = [k for k, _d in problems]
        self.assertIn("legacy field on active manifest", kinds)

    def test_find_problems_legacy_parents_exempt_when_closed(self):
        self._write_manifest("a", {"name": "a", "state": "closed", "parents": ["x"]})
        manifests, _ = wslib.discover_manifests(self.ws_root)
        problems = wslib.find_problems(manifests)
        kinds = [k for k, _d in problems]
        self.assertNotIn("legacy field on active manifest", kinds)

    def test_resolve_ref_display_resolves_by_session(self):
        manifests = {"b": {"name": "current-name", "state": "active"}}
        disp, state = wslib.resolve_ref_display({"name": "old-cached-name", "session": "b"}, manifests)
        self.assertEqual(disp, "current-name")
        self.assertEqual(state, "active")

    def test_resolve_ref_display_unresolved_badge(self):
        disp, state = wslib.resolve_ref_display({"name": "ghost", "session": "nope"}, {})
        self.assertEqual(disp, "ghost [unresolved]")
        self.assertIsNone(state)


class DependencyDegradeTests(unittest.TestCase):
    """The INSTALLED/fallback resolution chain. The real plugin tree now
    carries vendor/ballast/, which ballast_script/ballast_available resolve
    FIRST and which no env manipulation can hide, so neutralize the
    vendored-first shortcut here; the vendored path has its own tests below."""

    def setUp(self):
        self._old_env = dict(os.environ)
        self._orig_vendored = wslib._vendored_ballast_script
        wslib._vendored_ballast_script = lambda name: None

    def tearDown(self):
        wslib._vendored_ballast_script = self._orig_vendored
        os.environ.clear()
        os.environ.update(self._old_env)

    def test_ballast_unavailable_by_default(self):
        os.environ.pop("CLAUDE_PLUGIN_ROOT", None)
        os.environ["HOME"] = tempfile.mkdtemp(prefix="ws_home_")
        os.environ.pop("USERPROFILE", None)
        self.assertFalse(wslib.ballast_available())

    def test_ballast_available_when_installed_sibling(self):
        plugins_root = tempfile.mkdtemp(prefix="ws_plugins_root_")
        try:
            own = os.path.join(plugins_root, "workstream")
            os.makedirs(own)
            ballast_scripts = os.path.join(plugins_root, "ballast", "scripts")
            os.makedirs(ballast_scripts)
            with open(os.path.join(ballast_scripts, "ballast.py"), "w") as f:
                f.write("# stub\n")
            os.environ["CLAUDE_PLUGIN_ROOT"] = own
            self.assertTrue(wslib.ballast_available())
        finally:
            shutil.rmtree(plugins_root, ignore_errors=True)

    # 2026-09-07 fork incident coverage: CLAUDE_PLUGIN_ROOT unset (a shell-
    # run check, not a hook) must still find ballast via the marketplace-
    # nested installed-cache shape (cache/<marketplace>/ballast/<semver>/
    # scripts/ballast.py), which the plain HOME fallback alone misses.
    def test_ballast_available_var_unset_cache_marketplace_scan_hit(self):
        home = tempfile.mkdtemp(prefix="ws_home_")
        try:
            os.environ.pop("CLAUDE_PLUGIN_ROOT", None)
            os.environ["HOME"] = home
            os.environ.pop("USERPROFILE", None)
            ballast_scripts = os.path.join(home, ".claude", "plugins", "cache",
                                          "staff-plugins", "ballast", "0.1.0", "scripts")
            os.makedirs(ballast_scripts)
            with open(os.path.join(ballast_scripts, "ballast.py"), "w") as f:
                f.write("# stub\n")
            self.assertTrue(wslib.ballast_available())
        finally:
            shutil.rmtree(home, ignore_errors=True)

    def test_ballast_available_var_unset_registry_only_hit(self):
        home = tempfile.mkdtemp(prefix="ws_home_")
        try:
            os.environ.pop("CLAUDE_PLUGIN_ROOT", None)
            os.environ["HOME"] = home
            os.environ.pop("USERPROFILE", None)
            plugins_dir = os.path.join(home, ".claude", "plugins")
            os.makedirs(plugins_dir)
            with open(os.path.join(plugins_dir, "installed_plugins.json"), "w") as f:
                json.dump({"version": 2, "plugins": {"ballast@staff-plugins": []}}, f)
            self.assertTrue(wslib.ballast_available())
        finally:
            shutil.rmtree(home, ignore_errors=True)

    # ballast_script() - what manifest.py's approval gate resolves in order
    # to RUN ballast's approve.py. Same two path-based steps
    # ballast_available() uses; deliberately no registry step, since a
    # registry key is a belief with nothing to execute at the end of it.
    def test_ballast_script_none_when_ballast_absent(self):
        os.environ.pop("CLAUDE_PLUGIN_ROOT", None)
        os.environ["HOME"] = tempfile.mkdtemp(prefix="ws_home_")
        os.environ.pop("USERPROFILE", None)
        self.assertIsNone(wslib.ballast_script("approve.py"))

    def test_ballast_script_found_as_a_sibling_plugin(self):
        plugins_root = tempfile.mkdtemp(prefix="ws_plugins_root_")
        try:
            own = os.path.join(plugins_root, "workstream")
            os.makedirs(own)
            scripts = os.path.join(plugins_root, "ballast", "scripts")
            os.makedirs(scripts)
            approve = os.path.join(scripts, "approve.py")
            with open(approve, "w") as f:
                f.write("# stub\n")
            os.environ["CLAUDE_PLUGIN_ROOT"] = own
            self.assertEqual(wslib.ballast_script("approve.py"), approve)
            # a script ballast does not ship is still None, not a guess
            self.assertIsNone(wslib.ballast_script("nonesuch.py"))
        finally:
            shutil.rmtree(plugins_root, ignore_errors=True)

    def test_ballast_script_found_in_the_versioned_cache(self):
        home = tempfile.mkdtemp(prefix="ws_home_")
        try:
            os.environ.pop("CLAUDE_PLUGIN_ROOT", None)
            os.environ["HOME"] = home
            os.environ.pop("USERPROFILE", None)
            scripts = os.path.join(home, ".claude", "plugins", "cache",
                                   "staff-plugins", "ballast", "0.1.2", "scripts")
            os.makedirs(scripts)
            approve = os.path.join(scripts, "approve.py")
            with open(approve, "w") as f:
                f.write("# stub\n")
            self.assertEqual(wslib.ballast_script("approve.py"), approve)
        finally:
            shutil.rmtree(home, ignore_errors=True)

    def test_ballast_script_prefers_the_highest_installed_version(self):
        home = tempfile.mkdtemp(prefix="ws_home_")
        try:
            os.environ.pop("CLAUDE_PLUGIN_ROOT", None)
            os.environ["HOME"] = home
            os.environ.pop("USERPROFILE", None)
            base = os.path.join(home, ".claude", "plugins", "cache",
                                "staff-plugins", "ballast")
            for version in ("0.1.1", "0.1.10", "0.1.2"):
                scripts = os.path.join(base, version, "scripts")
                os.makedirs(scripts)
                with open(os.path.join(scripts, "approve.py"), "w") as f:
                    f.write("# stub\n")
            self.assertIn(os.path.join("0.1.10", "scripts"),
                          wslib.ballast_script("approve.py"))
        finally:
            shutil.rmtree(home, ignore_errors=True)

    def test_a_registry_key_alone_yields_no_script_path(self):
        """ballast_available() accepts installed_plugins.json as a last,
        weakest signal; ballast_script() must not - there is nothing to
        execute at the end of a belief."""
        home = tempfile.mkdtemp(prefix="ws_home_")
        try:
            os.environ.pop("CLAUDE_PLUGIN_ROOT", None)
            os.environ["HOME"] = home
            os.environ.pop("USERPROFILE", None)
            plugins_dir = os.path.join(home, ".claude", "plugins")
            os.makedirs(plugins_dir)
            with open(os.path.join(plugins_dir, "installed_plugins.json"), "w") as f:
                json.dump({"plugins": {"ballast@staff-plugins": []}}, f)
            self.assertTrue(wslib.ballast_available())
            self.assertIsNone(wslib.ballast_script("approve.py"))
        finally:
            shutil.rmtree(home, ignore_errors=True)

    def test_adopt_precheck_refuses_without_ballast(self):
        os.environ.pop("CLAUDE_PLUGIN_ROOT", None)
        os.environ["HOME"] = tempfile.mkdtemp(prefix="ws_home_")
        os.environ.pop("USERPROFILE", None)
        ok, message = wslib.adopt_precheck()
        self.assertFalse(ok)
        self.assertIn("refuses", message)
        self.assertIn("ballast", message)

    # Vendored-first: the real plugin tree carries vendor/ballast/, so a
    # self-contained install resolves ballast there before any installed
    # copy. These restore the shortcut this class's setUp neutralizes.
    def test_vendored_copy_makes_ballast_available(self):
        wslib._vendored_ballast_script = self._orig_vendored
        os.environ.pop("CLAUDE_PLUGIN_ROOT", None)
        os.environ["HOME"] = tempfile.mkdtemp(prefix="ws_home_")
        os.environ.pop("USERPROFILE", None)
        self.assertTrue(wslib.ballast_available())
        script = wslib.ballast_script("approve.py")
        self.assertIsNotNone(script)
        self.assertIn(os.path.join("vendor", "ballast"), script)

    def test_vendored_copy_wins_over_an_installed_sibling(self):
        wslib._vendored_ballast_script = self._orig_vendored
        plugins_root = tempfile.mkdtemp(prefix="ws_plugins_root_")
        try:
            own = os.path.join(plugins_root, "workstream")
            os.makedirs(own)
            scripts = os.path.join(plugins_root, "ballast", "scripts")
            os.makedirs(scripts)
            with open(os.path.join(scripts, "approve.py"), "w") as f:
                f.write("# installed stub\n")
            os.environ["CLAUDE_PLUGIN_ROOT"] = own
            self.assertIn(os.path.join("vendor", "ballast"),
                          wslib.ballast_script("approve.py"))
        finally:
            shutil.rmtree(plugins_root, ignore_errors=True)

    def test_adopt_precheck_ok_with_ballast(self):
        plugins_root = tempfile.mkdtemp(prefix="ws_plugins_root_")
        try:
            own = os.path.join(plugins_root, "workstream")
            os.makedirs(own)
            ballast_scripts = os.path.join(plugins_root, "ballast", "scripts")
            os.makedirs(ballast_scripts)
            with open(os.path.join(ballast_scripts, "ballast.py"), "w") as f:
                f.write("# stub\n")
            os.environ["CLAUDE_PLUGIN_ROOT"] = own
            ok, message = wslib.adopt_precheck()
            self.assertTrue(ok)
            self.assertIsNone(message)
        finally:
            shutil.rmtree(plugins_root, ignore_errors=True)

    def test_vault_lock_unavailable_by_default(self):
        vault = make_vault()
        try:
            self.assertFalse(wslib.vault_lock_available(vault))
        finally:
            shutil.rmtree(vault, ignore_errors=True)

    def test_vault_lock_available_when_materialized(self):
        vault = make_vault()
        try:
            binp = os.path.join(vault, ".vault-meta", "bin")
            os.makedirs(binp)
            with open(os.path.join(binp, "vault-lock.sh"), "w") as f:
                f.write("#!/bin/bash\n")
            self.assertTrue(wslib.vault_lock_available(vault))
        finally:
            shutil.rmtree(vault, ignore_errors=True)

    def test_grill_unavailable_by_default(self):
        os.environ.pop("CLAUDE_PLUGIN_ROOT", None)
        os.environ["HOME"] = tempfile.mkdtemp(prefix="ws_home_")
        os.environ.pop("USERPROFILE", None)
        self.assertFalse(wslib.grill_available())

    def test_grill_available_when_installed(self):
        plugins_root = tempfile.mkdtemp(prefix="ws_plugins_root_")
        try:
            own = os.path.join(plugins_root, "workstream")
            os.makedirs(own)
            grilling = os.path.join(plugins_root, "grill", "skills", "grilling")
            os.makedirs(grilling)
            with open(os.path.join(grilling, "SKILL.md"), "w") as f:
                f.write("---\nname: grilling\n---\n")
            os.environ["CLAUDE_PLUGIN_ROOT"] = own
            self.assertTrue(wslib.grill_available())
        finally:
            shutil.rmtree(plugins_root, ignore_errors=True)


class AtomicWriteTests(unittest.TestCase):
    def test_atomic_write_lf_no_tmp_litter_on_success(self):
        d = tempfile.mkdtemp(prefix="ws_atomic_")
        try:
            path = os.path.join(d, "out.md")
            wslib.atomic_write_lf(path, "hello\n")
            with open(path) as f:
                self.assertEqual(f.read(), "hello\n")
            self.assertEqual(sorted(os.listdir(d)), ["out.md"])
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_atomic_write_lf_dry_run_touches_nothing(self):
        d = tempfile.mkdtemp(prefix="ws_atomic_")
        try:
            path = os.path.join(d, "out.md")
            wslib.atomic_write_lf(path, "hello\n", dry_run=True)
            self.assertFalse(os.path.isfile(path))
            self.assertEqual(os.listdir(d), [])
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_atomic_write_json_dry_run_touches_nothing(self):
        d = tempfile.mkdtemp(prefix="ws_atomic_")
        try:
            path = os.path.join(d, "out.json")
            wslib.atomic_write_json(path, {"a": 1}, dry_run=True)
            self.assertFalse(os.path.isfile(path))
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_atomic_write_json_roundtrip(self):
        d = tempfile.mkdtemp(prefix="ws_atomic_")
        try:
            path = os.path.join(d, "out.json")
            wslib.atomic_write_json(path, {"a": 1})
            with open(path) as f:
                self.assertEqual(json.load(f), {"a": 1})
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
