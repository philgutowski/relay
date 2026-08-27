"""U1: every pinned contract string is traceable to a source line in the installed plugin."""
import os
import re
import unittest
from types import SimpleNamespace

import _paths  # noqa: F401
from relay import classify, contracts

PLUGIN_ROOT = os.path.expanduser(
    "~/.claude/plugins/cache/compound-engineering-plugin/compound-engineering/%s" % contracts.PLUGIN_MIN_VERSION
)

RELAY_SRC_DIR = os.path.dirname(os.path.abspath(classify.__file__))
RUN_PY_PATH = os.path.join(RELAY_SRC_DIR, "run.py")
CLOSEOUT_PY_PATH = os.path.join(RELAY_SRC_DIR, "closeout.py")

# Matches digest.get("key") / ctx.digest.get("key") / closeout_digest.get("key") on the
# top-level digest object, not a nested lookup like envelope.get(...) or finding.get(...).
DIGEST_GET_RE = re.compile(r"\b(?:\w+\.)?digest\s*\.get\(\s*[\"']([A-Za-z_]+)[\"']")
# Matches digest["key"] = ... / ctx.digest["key"] = ..., a key the reading module adds to the
# dict itself rather than one classify.classify() guarantees, e.g. run.py's own "task_id" and
# "timeout" bookkeeping. Excluded from the cross-module contract check below.
DIGEST_SET_RE = re.compile(r"\b(?:\w+\.)?digest\s*\[\s*[\"']([A-Za-z_]+)[\"']\s*\]\s*=")


def _digest_keys_read(source_path):
    with open(source_path, encoding="utf-8") as handle:
        source = handle.read()
    read_keys = set(DIGEST_GET_RE.findall(source))
    locally_set_keys = set(DIGEST_SET_RE.findall(source))
    return read_keys - locally_set_keys


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


class DigestKeysContract(unittest.TestCase):
    """classify.classify() (U7) returns a digest dict that run.py and closeout.py each read
    with their own string-keyed lookups. DIGEST_KEYS is the set classify guarantees; these
    tests fail if either reader reaches for a key classify does not set, or if classify stops
    setting a key either reader depends on."""

    def test_readers_stay_inside_digest_keys(self):
        run_keys = _digest_keys_read(RUN_PY_PATH)
        closeout_keys = _digest_keys_read(CLOSEOUT_PY_PATH)
        unknown = (run_keys | closeout_keys) - contracts.DIGEST_KEYS
        self.assertFalse(unknown, "reader(s) use digest key(s) not in DIGEST_KEYS: %s" % unknown)

    def test_classify_sets_every_key_readers_use(self):
        run_keys = _digest_keys_read(RUN_PY_PATH)
        closeout_keys = _digest_keys_read(CLOSEOUT_PY_PATH)
        required = (run_keys | closeout_keys) & contracts.DIGEST_KEYS
        result = classify.classify("/nonexistent/path.jsonl", SimpleNamespace(timed_out=False, exit_code=None))
        missing = required - set(result)
        self.assertFalse(missing, "classify() no longer sets key(s) readers depend on: %s" % missing)


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
