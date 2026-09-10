#!/usr/bin/env python3
"""Structural tests for skills/ — the three VENDORED ballast templates and
the shape every skill in this plugin has to keep.

The vendored three (policy, glossary, check) are copies
of ballast's `templates/skills/<name>/SKILL.md`, kept in step by ballast's
own `sync-templates.py --check`. That check needs the ballast repo
alongside this one, so it is an acceptance step rather than a test here;
what IS testable without a sibling checkout is everything that would make
the check meaningless if it drifted locally - the template marker, the
consumer-extras marker below which this plugin's own text lives, and the
frontmatter name the vendoring step sets.
"""
import os
import re
import unittest

PLUGIN_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
SKILLS = os.path.join(PLUGIN_ROOT, "skills")

VENDORED = ("policy", "glossary", "check")
EXTRAS_MARKER = "<!-- consumer-extras -->"
TEMPLATE_MARKER_RE = re.compile(r"^<!-- ballast-template: ([a-z-]+) v\d+ -->$",
                                re.MULTILINE)

# Every skill this plugin registers. Listed rather than globbed so a skill
# that silently disappears from the tree fails a test instead of shrinking
# the check.
EXPECTED_SKILLS = (
    "absorb", "adopt", "check", "close", "connect", "fork", "glossary",
    "global-policy", "graph", "list", "manifest", "notify", "policy",
    "refocus", "sidecar", "status",
)


def read(name):
    with open(os.path.join(SKILLS, name, "SKILL.md"), encoding="utf-8") as handle:
        return handle.read()


def frontmatter_name(text):
    match = re.search(r"^name:\s*(.+)$", text, re.MULTILINE)
    return match.group(1).strip() if match else None


class SkillTreeTests(unittest.TestCase):
    def test_every_expected_skill_exists_and_nothing_else(self):
        found = sorted(d for d in os.listdir(SKILLS)
                       if os.path.isfile(os.path.join(SKILLS, d, "SKILL.md")))
        self.assertEqual(found, sorted(EXPECTED_SKILLS))

    def test_frontmatter_name_matches_the_directory(self):
        for name in EXPECTED_SKILLS:
            self.assertEqual(frontmatter_name(read(name)), name)


class ScriptInvocationTests(unittest.TestCase):
    """R0-1: a skill runs from the session cwd (the vault root), where no
    `scripts/` directory exists - the scripts live at the plugin root. Every
    `scripts/<x>.py` a SKILL.md names must therefore be spelled
    `${CLAUDE_PLUGIN_ROOT}/scripts/<x>.py`, or the command cannot resolve its
    own script. This scans EVERY SKILL.md (frontmatter included) and fails on
    any `scripts/<x>.py` not immediately preceded by `${CLAUDE_PLUGIN_ROOT}/`."""

    SCRIPT_RE = re.compile(r"scripts/[a-z_]+\.py")
    PREFIX = "${CLAUDE_PLUGIN_ROOT}/"

    def test_no_bare_script_path_in_any_skill(self):
        offenders = []
        for name in EXPECTED_SKILLS:
            text = read(name)
            for m in self.SCRIPT_RE.finditer(text):
                start = m.start()
                if text[max(0, start - len(self.PREFIX)):start] != self.PREFIX:
                    line = text.count("\n", 0, start) + 1
                    offenders.append("%s:%d %s" % (name, line, m.group(0)))
        self.assertEqual(offenders, [],
                         "bare scripts/<x>.py without ${CLAUDE_PLUGIN_ROOT}/: "
                         + "; ".join(offenders))


class VendoredSkillTests(unittest.TestCase):
    def test_each_vendored_skill_carries_its_template_marker(self):
        for name in VENDORED:
            text = read(name)
            match = TEMPLATE_MARKER_RE.search(text)
            self.assertIsNotNone(match, name)
            self.assertEqual(match.group(1), name)

    def test_the_extras_marker_appears_exactly_once(self):
        for name in VENDORED:
            self.assertEqual(read(name).count(EXTRAS_MARKER), 1, name)

    def test_this_plugin_own_text_lives_below_the_marker(self):
        """Anything above the marker is ballast's and is overwritten on the
        next re-sync; a workstream-specific instruction placed there would
        be silently lost."""
        for name in VENDORED:
            above, _, below = read(name).partition(EXTRAS_MARKER)
            self.assertNotIn("workstream:", above.split("---", 2)[-1], name)
            self.assertIn("workstream", below, name)

    def test_a_skill_that_is_not_vendored_carries_no_template_marker(self):
        for name in EXPECTED_SKILLS:
            if name in VENDORED:
                continue
            self.assertIsNone(TEMPLATE_MARKER_RE.search(read(name)), name)


if __name__ == "__main__":
    unittest.main()
