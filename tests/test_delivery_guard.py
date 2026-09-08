#!/usr/bin/env python3
"""Tests for the whole-output guard (B5) - workstream_lib.guard_delivery
and its wiring into this plugin's three own hooks.

The guard is a BACKSTOP: boot.py's identity block and precompact.py's note
each carry their own much smaller cap, so in ordinary operation nothing
here ever fires. That is exactly why it is worth testing - an unreachable
bound nobody exercises is how a payload silently grows past the harness's
inline cap and gets replaced by a ~2 KB preview, which is defect D1 in a
new costume.

The wiring tests drive each hook's `main()` in-process with the ceiling
temporarily lowered, because there is no legitimate input that pushes
these three past 9,431 B - forcing the branch is the only way to see that
it is connected at all.
"""
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
import contextlib

SCRIPTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts")
sys.path.insert(0, SCRIPTS)
import workstream_lib as wslib
import manifest as mprim
import sidecar
import boot as boot_mod
import remind as remind_mod
import precompact as precompact_mod


def make_vault():
    d = tempfile.mkdtemp(prefix="ws_guard_test_")
    os.makedirs(os.path.join(d, "staff"), exist_ok=True)
    return d


class GuardDeliveryTests(unittest.TestCase):
    def test_the_fixture_write_helper_respects_dry_run(self):
        """This file writes its one fixture (remind's turn counter) through
        workstream_lib's own dry_run-capable primitive rather than a raw
        file call - the auto-writer discipline every script in this plugin
        follows, asserted the way tests/test_ballast_dispatch.py asserts it
        for ballast-dispatch's own scope write."""
        directory = tempfile.mkdtemp(prefix="ws_guard_dryrun_")
        try:
            path = os.path.join(directory, "would-not-exist.json")
            wslib.atomic_write_json(path, {"count": 0}, dry_run=True)
            self.assertFalse(os.path.isfile(path))
        finally:
            shutil.rmtree(directory, ignore_errors=True)

    def test_the_budget_is_the_measured_ceiling_less_the_margin(self):
        self.assertEqual(wslib.INLINE_CEILING, 9687)
        self.assertEqual(wslib.delivery_limit(), 9687 - wslib.GUARD_MARGIN_BYTES)

    def test_under_budget_is_returned_unchanged(self):
        text = "a short delivery\n"
        self.assertEqual(wslib.guard_delivery(text, "x"), text)

    def test_exactly_at_the_budget_is_unchanged(self):
        text = "x" * wslib.delivery_limit()
        self.assertEqual(wslib.guard_delivery(text, "x"), text)

    def test_one_byte_over_is_trimmed_and_marked(self):
        text = "x" * (wslib.delivery_limit() + 1)
        out = wslib.guard_delivery(text, "SessionStart")
        self.assertTrue(out.startswith(wslib.DELIVERY_TRIM_PREFIX))
        self.assertIn("SessionStart", out.splitlines()[0])
        self.assertLessEqual(len(out.encode("utf-8")), wslib.delivery_limit())

    def test_the_marker_names_the_overage_and_both_numbers(self):
        text = "x" * (wslib.delivery_limit() + 500)
        first = wslib.guard_delivery(text, "PreCompact").splitlines()[0]
        self.assertIn("500 B over", first)
        self.assertIn(str(wslib.delivery_limit()), first)
        self.assertIn("9687", first.replace(",", ""))
        self.assertIn(str(wslib.GUARD_MARGIN_BYTES), first)

    def test_a_second_pass_never_stacks_a_second_marker(self):
        once = wslib.guard_delivery("x" * (wslib.delivery_limit() + 10), "a")
        twice = wslib.guard_delivery(once, "a")
        self.assertEqual(twice.count(wslib.DELIVERY_TRIM_PREFIX), 1)

    def test_multibyte_text_is_measured_in_bytes_and_never_split_mid_character(self):
        text = "—" * wslib.delivery_limit()      # 3 bytes each
        out = wslib.guard_delivery(text, "x")
        self.assertLessEqual(len(out.encode("utf-8")), wslib.delivery_limit())
        out.encode("utf-8").decode("utf-8")           # still valid UTF-8

    def test_empty_and_none_are_safe(self):
        self.assertEqual(wslib.guard_delivery("", "x"), "")
        self.assertIsNone(wslib.guard_delivery(None, "x"))


class HookWiringTests(unittest.TestCase):
    """Each of the three hooks routes its ENTIRE stdout through the guard."""

    def setUp(self):
        self.vault = make_vault()
        self._cwd = os.getcwd()
        self._ceiling = wslib.INLINE_CEILING
        os.chdir(self.vault)
        mprim.create_manifest(self.vault, "born-g", "guard-ws", "F" * 4000)
        sidecar.write(self.vault, "sess-g", "born-g")

    def tearDown(self):
        wslib.INLINE_CEILING = self._ceiling
        os.chdir(self._cwd)
        shutil.rmtree(self.vault, ignore_errors=True)

    def _run(self, module, payload, main_args=()):
        """Drive one hook's main() in-process with a crafted payload, the
        ceiling lowered far enough that its ordinary output is over
        budget."""
        wslib.INLINE_CEILING = 300 + wslib.GUARD_MARGIN_BYTES
        buffer = io.StringIO()
        original = module.read_stdin_payload
        module.read_stdin_payload = lambda: payload
        try:
            with contextlib.redirect_stdout(buffer):
                module.main(*main_args)
        finally:
            module.read_stdin_payload = original
        return buffer.getvalue()

    def test_boot_bounds_its_whole_output(self):
        out = self._run(boot_mod, {"session_id": "sess-g"})
        self.assertTrue(out.startswith(wslib.DELIVERY_TRIM_PREFIX), out[:120])
        self.assertIn("workstream boot", out.splitlines()[0])
        self.assertLessEqual(len(out.encode("utf-8")), wslib.delivery_limit())

    def test_precompact_bounds_its_whole_output(self):
        out = self._run(precompact_mod, {"session_id": "sess-g"})
        self.assertTrue(out.startswith(wslib.DELIVERY_TRIM_PREFIX), out[:120])
        self.assertIn("PreCompact", out.splitlines()[0])
        self.assertLessEqual(len(out.encode("utf-8")), wslib.delivery_limit())

    def test_remind_bounds_its_whole_output(self):
        counter = os.path.join(wslib.sessions_root(self.vault), "sess-g.remind.json")
        wslib.atomic_write_json(counter, {"count": remind_mod.REMIND_EVERY_N - 1})
        out = self._run(remind_mod, {"session_id": "sess-g"})
        self.assertTrue(out.startswith(wslib.DELIVERY_TRIM_PREFIX), out[:120])
        self.assertIn("workstream remind", out.splitlines()[0])
        self.assertLessEqual(len(out.encode("utf-8")), wslib.delivery_limit())

    def test_ordinary_output_is_nowhere_near_the_real_guard(self):
        """With the real ceiling, none of the three comes close - the guard
        is a bound, not the working limit."""
        original = boot_mod.read_stdin_payload
        boot_mod.read_stdin_payload = lambda: {"session_id": "sess-g"}
        try:
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                boot_mod.main()
            out = buffer.getvalue()
        finally:
            boot_mod.read_stdin_payload = original
        self.assertNotIn(wslib.DELIVERY_TRIM_PREFIX, out)
        self.assertLess(len(out.encode("utf-8")), 2048)


if __name__ == "__main__":
    unittest.main()
