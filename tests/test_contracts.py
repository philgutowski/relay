"""U1: every pinned contract string is traceable to a source line in the installed plugin."""
import os
import unittest

import _paths  # noqa: F401
from relay import contracts

PLUGIN_ROOT = os.path.expanduser(
    "~/.claude/plugins/cache/compound-engineering-plugin/compound-engineering/%s" % contracts.PLUGIN_MIN_VERSION
)


class PinsTraceToSource(unittest.TestCase):
    def test_every_pin_is_found_in_its_named_source(self):
        if not os.path.isdir(PLUGIN_ROOT):
            self.skipTest("compound-engineering %s is not installed at %s" % (contracts.PLUGIN_MIN_VERSION, PLUGIN_ROOT))
        for name, needle, rel_path in contracts.PLUGIN_PINS:
            with self.subTest(pin=name):
                path = os.path.join(PLUGIN_ROOT, rel_path)
                self.assertTrue(os.path.exists(path), "%s names a missing source %s" % (name, rel_path))
                with open(path, encoding="utf-8") as handle:
                    self.assertIn(needle, handle.read(), "%s: %r not in %s" % (name, needle, rel_path))

    def test_pin_values_match_module_constants(self):
        for name, needle, _ in contracts.PLUGIN_PINS:
            with self.subTest(pin=name):
                value = getattr(contracts, name)
                if isinstance(value, bool):
                    continue
                self.assertEqual(needle.strip("`"), value)


class OwnVocabulary(unittest.TestCase):
    def test_every_class_that_can_print_has_a_cause_line_and_nothing_else_does(self):
        self.assertEqual(set(contracts.HALT_LINES), set(contracts.LINE_CLASSES))

    def test_the_halt_class_set_stays_closed_and_the_finding_classes_sit_inside_the_line_set(self):
        """KTD6 fixes the halt class set. A closeout finding is never a record's own class, so
        it belongs to LINE_CLASSES and not to HALT_CLASSES."""
        for cls in (contracts.CLOSEOUT_UNFINISHED, contracts.BLOCKED_UNRECORDED):
            self.assertNotIn(cls, contracts.HALT_CLASSES)
            self.assertIn(cls, contracts.LINE_CLASSES)
        for cls in contracts.FINDING_CLASSES:
            self.assertIn(cls, contracts.LINE_CLASSES)

    def test_disallow_list_covers_the_four_r10_operations(self):
        joined = "\n".join(contracts.DISALLOWED_TOOLS)
        for fragment in ("git push --force", "git reset --hard", "rm -rf", "git clean"):
            self.assertIn(fragment, joined)
        self.assertNotIn(contracts.FORBIDDEN_PERMISSION_MODE, joined)

    def test_denial_regex_matches_the_observed_text(self):
        text = "Permission to use Edit has been denied because Claude Code is running in don't ask mode."
        self.assertEqual(contracts.DENIAL_REGEX.match(text).group(1), "Edit")
        self.assertIsNone(contracts.DENIAL_REGEX.match("Edit succeeded"))

    def test_lease_ttl_is_shorter_than_the_default_task_timeout(self):
        self.assertLess(contracts.LEASE_TTL_SECONDS, contracts.DEFAULT_TASK_TIMEOUT_MINUTES * 60)
        self.assertLess(contracts.LEASE_HEARTBEAT_SECONDS, contracts.LEASE_TTL_SECONDS)


class SlugRule(unittest.TestCase):
    def test_matches_the_on_machine_examples(self):
        cases = {
            "/Users/pgutowski/Documents/PhilAI/relay": "-Users-pgutowski-Documents-PhilAI-relay",
            "/Users/pgutowski/.config/raycast/script-commands": "-Users-pgutowski--config-raycast-script-commands",
            "/private/tmp": "-private-tmp",
            "/Users/x/Documents/PhilAI/DJ_tools": "-Users-x-Documents-PhilAI-DJ-tools",
            "/Users/x/a.b c": "-Users-x-a-b-c",
        }
        for path, slug in cases.items():
            self.assertEqual(contracts.slug_for(path), slug)

    def test_transcript_path_uses_home_and_slug(self):
        path = contracts.transcript_path("/h", "/r/x", "abc")
        self.assertEqual(path, "/h/.claude/projects/-r-x/abc.jsonl")


if __name__ == "__main__":
    unittest.main()
