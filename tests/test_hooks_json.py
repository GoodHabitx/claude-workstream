#!/usr/bin/env python3
"""Tests for hooks/hooks.json - the wiring itself, asserted entry by entry.

A hooks.json is the one file in this plugin nothing else exercises: a
dropped `--part`, a global delivery accidentally wired to Stop, or a
PreToolUse entry that never got added are all silent at runtime (the
plugin simply does less than it says). So this file enumerates EXACTLY
the entries the 0.1.5 wiring declares - event, matcher, script and the
argv after `${CLAUDE_PLUGIN_ROOT}` - and fails on any drift in either
direction.

It also checks the two properties every command line must hold: the
four-candidate interpreter shim (neither `python3` nor `python` resolves
on every host), and `exec`, which is what leaves the script's own exit
code - above all ballast's PreToolUse refusal, exit 2 - as the hook's.
"""
import json
import os
import re
import unittest

PLUGIN_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
HOOKS_PATH = os.path.join(PLUGIN_ROOT, "hooks", "hooks.json")

COMMAND_RE = re.compile(
    r'exec \$c "\$\{CLAUDE_PLUGIN_ROOT\}/scripts/([A-Za-z0-9_.-]+)"(.*?); fi; done;')

# (event, matcher, script, argv after the plugin-root argument)
EXPECTED = [
    ("SessionStart", None, "boot.py", ""),
    ("SessionStart", None, "ballast-dispatch.py", "SessionStart --part hot"),
    ("SessionStart", None, "ballast-dispatch.py", "SessionStart --part policy"),
    ("SessionStart", None, "ballast-dispatch.py", "SessionStart --part glossary"),
    ("SessionStart", None, "ballast-dispatch.py",
     "SessionStart --part policy --scope-kind global"),
    ("UserPromptSubmit", None, "ballast-dispatch.py", "UserPromptSubmit"),
    ("UserPromptSubmit", None, "remind.py", ""),
    ("PreToolUse", "Write|Edit|NotebookEdit", "ballast-dispatch.py", "PreToolUse"),
    ("PostToolUse", "Write|Edit|NotebookEdit|Bash|PowerShell",
     "ballast-dispatch.py", "PostToolUse"),
    ("PreCompact", None, "precompact.py", ""),
    ("PreCompact", None, "ballast-dispatch.py", "PreCompact"),
    ("Stop", None, "ballast-dispatch.py", "Stop"),
]


def load():
    with open(HOOKS_PATH, encoding="utf-8") as handle:
        return json.load(handle)


def flatten(data):
    out = []
    for event, entries in data["hooks"].items():
        for entry in entries:
            for hook in entry["hooks"]:
                command = hook["command"]
                match = COMMAND_RE.search(command)
                if match is None:
                    out.append((event, entry.get("matcher"), command, None))
                    continue
                script, rest = match.group(1), match.group(2)
                # Drop the ${CLAUDE_PLUGIN_ROOT} argument every script takes;
                # what is left is the argv this entry actually declares.
                rest = rest.replace('"${CLAUDE_PLUGIN_ROOT}"', " ")
                out.append((event, entry.get("matcher"), script,
                            " ".join(rest.split())))
    return out


class HooksJsonTests(unittest.TestCase):
    def test_hooks_json_parses(self):
        data = load()
        self.assertIn("hooks", data)
        self.assertIsInstance(data["hooks"], dict)

    def test_entries_are_exactly_the_declared_wiring(self):
        self.assertEqual(flatten(load()), EXPECTED)

    def test_every_command_uses_the_interpreter_shim_and_exec(self):
        for event, entries in load()["hooks"].items():
            for entry in entries:
                for hook in entry["hooks"]:
                    command = hook["command"]
                    self.assertIn('for c in python3 python "py -3" py', command,
                                  "%s: no interpreter shim" % event)
                    # exec, not a plain call: a subshell would swallow the
                    # script's exit code, and ballast's approval refusal (2)
                    # is carried by exactly that code.
                    self.assertIn("exec $c", command, "%s: not exec'd" % event)
                    self.assertEqual(hook["type"], "command")
                    self.assertIsInstance(hook["timeout"], int)

    def test_global_scope_is_sessionstart_only(self):
        """Ballast's own docs: wire a shared scope to SessionStart, never to
        Stop (the harness's Stop anti-loop flag is per-turn, so a second
        ballast-backed Stop hook makes BOTH fail open) and never to
        PreCompact (it re-injects at the compact-source SessionStart)."""
        for event, _matcher, _script, args in flatten(load()):
            if "--scope-kind global" in (args or ""):
                self.assertEqual(event, "SessionStart")

    def test_the_three_sessionstart_parts_are_each_wired_once(self):
        parts = [args.split("--part ")[1].split()[0]
                 for event, _m, script, args in flatten(load())
                 if event == "SessionStart" and script == "ballast-dispatch.py"
                 and "--part " in args and "--scope-kind" not in args]
        self.assertEqual(sorted(parts), ["glossary", "hot", "policy"])

    def test_each_sessionstart_part_is_its_own_hook_command(self):
        """The split exists because the harness caps EACH hook command's
        stdout independently - two parts sharing one command would share one
        envelope and defeat it."""
        for entry in load()["hooks"]["SessionStart"]:
            self.assertEqual(len(entry["hooks"]), 1)


if __name__ == "__main__":
    unittest.main()
