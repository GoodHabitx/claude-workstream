#!/usr/bin/env python3
"""Tests for scripts/status.py - the read-only artifact printer behind
`/workstream:status`.

The thing worth guarding here is that status PRINTS. Every assertion
below is about bytes reaching stdout unchanged, about arithmetic over two
numbers, or about the words `absent`/`disabled` standing in for a file
that is not there - never about a judgment. The second is that it writes
nothing: a diagnostic that repairs what it is describing cannot be
trusted to describe it.
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
import status as status_mod

HOT_FIXTURE = """<!-- ballast:hot v2 -->
focus: prove the printer prints
next: assert on the bytes
blocked: none
done: nothing yet
updated: 2026-09-08T00:00:00Z
who-acts-next: session
looping: none
progressing: yes
stall: 0
"""

# A stub standing in for the installed ballast's ballast_lib, so the
# per-slot path is exercised without a sibling checkout. Only the two
# names status.py reads are defined.
STUB_BALLAST_LIB = '''
HOT_SLOT_SPECS = (
    ("focus", 300, ""),
    ("next", 400, ""),
    ("blocked", 300, "none"),
    ("goal", 300, "none"),
)


def parse_hot_slots(text):
    slots = {}
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("<!--") or ":" not in line:
            continue
        key, _, value = line.partition(":")
        slots[key.strip().lower().replace("_", "-").replace(" ", "-")] = value.strip()
    return slots
'''


def make_vault():
    d = tempfile.mkdtemp(prefix="ws_status_test_")
    os.makedirs(os.path.join(d, "staff"), exist_ok=True)
    return d


def tree_snapshot(root):
    out = {}
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            path = os.path.join(dirpath, name)
            with open(path, "rb") as handle:
                out[os.path.relpath(path, root)] = handle.read()
    return out


class StatusTests(unittest.TestCase):
    def setUp(self):
        self.vault = make_vault()
        self._old_env = dict(os.environ)
        os.environ.pop("CLAUDE_PLUGIN_ROOT", None)
        os.environ["HOME"] = tempfile.mkdtemp(prefix="ws_home_")
        os.environ.pop("USERPROFILE", None)
        mprim.create_manifest(self.vault, "born-s", "status-ws", "print it")
        self.ws_dir = os.path.join(wslib.state_root(self.vault), "born-s")
        self.state_root = wslib.state_root(self.vault)

    def tearDown(self):
        shutil.rmtree(self.vault, ignore_errors=True)
        os.environ.clear()
        os.environ.update(self._old_env)

    # --- helpers -------------------------------------------------------
    def write(self, name, text, where=None):
        path = os.path.join(where or self.ws_dir, name)
        wslib.atomic_write_lf(path, text)
        return path

    def scope(self, **overrides):
        data = {"root": ".", "hot": "hot.md", "policy": "policy.md",
                "glossary": "glossary.md", "playbook": "playbook.md"}
        data.update(overrides)
        wslib.atomic_write_json(os.path.join(self.ws_dir, "ballast.json"), data)

    def build(self, only=None):
        return status_mod.build(self.vault, "born-s", only)

    def run_cli(self, *args):
        return subprocess.run([sys.executable, os.path.join(SCRIPTS, "status.py")]
                              + list(args), cwd=self.vault, capture_output=True,
                              text=True)

    # --- the six -------------------------------------------------------
    def test_all_six_artifacts_print_in_order(self):
        out = self.build()
        headings = [line for line in out.splitlines() if line.startswith("### ")]
        self.assertEqual([h.split(" - ")[0] for h in headings],
                         ["### manifest", "### hot", "### policy",
                          "### global-policy", "### glossary", "### playbook"])

    def test_one_argument_prints_only_that_artifact(self):
        self.write("policy.md", "# Policy\n\n- one rule\n")
        out = self.build(only="policy")
        self.assertIn("### policy", out)
        for other in ("### hot", "### manifest", "### glossary", "### playbook",
                      "### global-policy"):
            self.assertNotIn(other, out)

    def test_a_missing_file_prints_absent(self):
        out = self.build()
        # only the manifest exists in this fixture
        self.assertEqual(out.count("absent"), 5)
        for name in ("hot", "policy", "glossary", "playbook"):
            block = out.split("### %s" % name, 1)[1].split("###", 1)[0]
            self.assertIn("absent", block)

    # --- sizes and caps ------------------------------------------------
    def test_size_line_names_the_cap_and_its_key(self):
        self.write("policy.md", "x" * 100)
        out = self.build(only="policy")
        self.assertIn("100 B / 7,168 B cap (policy_cap_bytes)", out)

    def test_caps_come_from_the_scope_when_it_declares_them(self):
        self.scope(policy_cap_bytes=99)
        self.write("policy.md", "x" * 10)
        self.assertIn("10 B / 99 B cap (policy_cap_bytes)", self.build(only="policy"))

    def test_over_cap_prints_the_arithmetic(self):
        self.scope(policy_cap_bytes=10)
        self.write("policy.md", "x" * 25)
        self.assertIn("25 B / 10 B cap (policy_cap_bytes) - OVER by 15 B",
                      self.build(only="policy"))

    def test_an_unmigrated_scope_falls_back_to_the_documented_defaults(self):
        """A 0.1.1 scope declares neither glossary nor playbook; the
        printer still names a cap, and says the key came from the
        default."""
        self.scope()
        self.write("glossary.md", "- **term** - gloss\n")
        self.assertIn("B cap (glossary_cap_bytes)", self.build(only="glossary"))

    def test_a_null_file_field_prints_disabled_not_absent(self):
        self.scope(glossary=None)
        out = self.build(only="glossary")
        self.assertIn("disabled", out)
        self.assertNotIn("absent", out)

    def test_a_missing_ballast_json_is_reported_not_an_error(self):
        out = self.build()
        self.assertIn("no ballast.json at this scope", out)

    # --- verbatim ------------------------------------------------------
    def test_contents_are_printed_verbatim(self):
        body = "# Policy\n\n- a rule with `backticks`, a <tag>, and  double  spaces\n"
        self.write("policy.md", body)
        out = self.build(only="policy")
        self.assertIn(body.rstrip("\n"), out)

    def test_a_non_ascii_file_survives_intact(self):
        body = "- **delta** — the log.md lines newer than hot.md\n"
        self.write("glossary.md", body)
        self.assertIn(body.rstrip("\n"), self.build(only="glossary"))

    # --- hot.md's per-slot report ---------------------------------------
    def test_hot_slot_lengths_print_without_ballast(self):
        self.write("hot.md", HOT_FIXTURE)
        out = self.build(only="hot")
        self.assertIn("per-slot caps unknown", out)
        self.assertIn("focus: 24 chars", out)

    def test_hot_slot_caps_come_from_the_installed_ballast(self):
        plugins_root = tempfile.mkdtemp(prefix="ws_plugins_root_")
        try:
            own = os.path.join(plugins_root, "workstream")
            os.makedirs(own)
            scripts = os.path.join(plugins_root, "ballast", "scripts")
            os.makedirs(scripts)
            wslib.atomic_write_lf(os.path.join(scripts, "ballast_lib.py"),
                                  STUB_BALLAST_LIB)
            os.environ["CLAUDE_PLUGIN_ROOT"] = own
            self.write("hot.md", HOT_FIXTURE)
            out = self.build(only="hot")
            self.assertIn("focus: 24/300 chars", out)
            self.assertIn("next: 19/400 chars", out)
            self.assertIn("goal: absent", out)       # declared but not in the file
            self.assertNotIn("per-slot caps unknown", out)
        finally:
            shutil.rmtree(plugins_root, ignore_errors=True)

    def test_an_over_cap_slot_prints_the_arithmetic(self):
        plugins_root = tempfile.mkdtemp(prefix="ws_plugins_root_")
        try:
            own = os.path.join(plugins_root, "workstream")
            os.makedirs(own)
            scripts = os.path.join(plugins_root, "ballast", "scripts")
            os.makedirs(scripts)
            wslib.atomic_write_lf(
                os.path.join(scripts, "ballast_lib.py"),
                STUB_BALLAST_LIB.replace('("blocked", 300,', '("blocked", 3,'))
            os.environ["CLAUDE_PLUGIN_ROOT"] = own
            self.write("hot.md", HOT_FIXTURE)
            self.assertIn("blocked: 4/3 chars - OVER by 1", self.build(only="hot"))
        finally:
            shutil.rmtree(plugins_root, ignore_errors=True)

    # --- the global scope -----------------------------------------------
    def test_global_policy_says_no_global_scope_is_seeded(self):
        out = self.build(only="global-policy")
        self.assertIn("absent", out)
        self.assertIn("_global", out)

    def test_global_policy_prints_from_the_global_scope(self):
        global_dir = os.path.join(self.state_root, "_global")
        os.makedirs(global_dir)
        wslib.atomic_write_json(os.path.join(global_dir, "ballast.json"),
                                {"root": ".", "policy": "global-policy.md",
                                 "policy_cap_bytes": 4096})
        self.write("global-policy.md", "- one rule for everyone\n", where=global_dir)
        out = self.build(only="global-policy")
        self.assertIn("24 B / 4,096 B cap (policy_cap_bytes)", out)
        self.assertIn("one rule for everyone", out)

    # --- read-only -------------------------------------------------------
    def test_the_fixture_write_helper_respects_dry_run(self):
        """This file writes its fixtures through workstream_lib's own
        dry_run-capable primitive rather than raw file calls - the same
        auto-writer discipline every script in this plugin follows,
        asserted here the way tests/test_ballast_dispatch.py asserts it
        for ballast-dispatch's own scope write."""
        path = os.path.join(self.ws_dir, "would-not-exist.md")
        wslib.atomic_write_lf(path, "x", dry_run=True)
        self.assertFalse(os.path.isfile(path))

    def test_status_writes_nothing(self):
        self.write("hot.md", HOT_FIXTURE)
        self.write("policy.md", "# Policy\n")
        before = tree_snapshot(self.vault)
        self.build()
        for name in status_mod.ARTIFACTS:
            self.build(only=name)
        self.assertEqual(tree_snapshot(self.vault), before)

    # --- the CLI ---------------------------------------------------------
    def test_cli_prints_for_a_born_session(self):
        proc = self.run_cli("--born-session", "born-s")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("### manifest", proc.stdout)

    def test_cli_resolves_a_bound_transcript_session(self):
        sidecar.write(self.vault, "sess-s", "born-s")
        proc = self.run_cli("--session", "sess-s")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("born-s", proc.stdout)

    def test_cli_refuses_an_unbound_session(self):
        proc = self.run_cli("--session", "sess-nope")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("not bound", proc.stderr)
        self.assertEqual(proc.stdout, "")

    def test_cli_needs_a_target(self):
        proc = self.run_cli()
        self.assertEqual(proc.returncode, 2)
        self.assertIn("--born-session", proc.stderr)

    def test_cli_refuses_an_unknown_workstream(self):
        proc = self.run_cli("--born-session", "born-nope")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("no workstream directory", proc.stderr)

    def test_cli_rejects_an_unknown_artifact_name(self):
        proc = self.run_cli("hotter", "--born-session", "born-s")
        self.assertEqual(proc.returncode, 2)

    def test_cli_rejects_an_unsafe_born_session(self):
        proc = self.run_cli("--born-session", "../escape")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("unsafe", proc.stderr)


if __name__ == "__main__":
    unittest.main()
