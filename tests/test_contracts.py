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

RUN_PY_PATH = os.path.join(_paths.SCRIPTS_DIR, "relay", "run.py")
CLOSEOUT_PY_PATH = os.path.join(_paths.SCRIPTS_DIR, "relay", "closeout.py")

# Matches digest.get("key") / ctx.digest.get("key") / closeout_digest.get("key") /
# (digest or {}).get("key") on the top-level digest object, not a nested lookup like
# envelope.get(...) or finding.get(...). "digest" may be bare, dotted (ctx.digest), or suffixed
# (closeout_digest), and may sit behind a "(... or {})" fallback wrapper.
DIGEST_GET_RE = re.compile(
    r"\(?\s*((?:\w+\.)*\w*digest)\s*(?:or\s+\{\}\s*\))?\s*\.get\(\s*[\"']([A-Za-z_]+)[\"']"
)
# Matches digest["key"] = ... / ctx.digest["key"] = ..., a key the reading module adds to the
# dict itself rather than one classify.classify() guarantees, e.g. run.py's own "task_id" and
# "timeout" bookkeeping. Excluded from the cross-module contract check below.
DIGEST_SET_RE = re.compile(r"((?:\w+\.)*\w*digest)\s*\[\s*[\"']([A-Za-z_]+)[\"']\s*\]\s*=")


def _digest_keys_read(source_path):
    """Top-level digest keys a module reads via `.get(...)`, minus keys the same module writes
    onto its own digest object first (e.g. run.py's "task_id" and "timeout" bookkeeping).
    Those are local additions, not part of the cross-module classify contract."""
    with open(source_path, encoding="utf-8") as handle:
        source = handle.read()
    read_keys = {key for _, key in DIGEST_GET_RE.findall(source)}
    locally_set_keys = {key for _, key in DIGEST_SET_RE.findall(source)}
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

    def test_the_run_scoped_halt_classes_are_the_three_that_implicate_every_later_task(self):
        """Issue #15: a halt the runner may continue past is decided from the repo, not from
        its class. Only these three always stop, because each one puts something outside the
        failing task in question: the remote, the lease, or the runner itself."""
        self.assertEqual(sorted(contracts.RUN_SCOPED_HALT_CLASSES), sorted((
            contracts.HALT_REMOTE_ADVANCED,
            contracts.HALT_RUNNER_CRASHED,
            contracts.HALT_UNEXPECTED_ERROR,
        )))
        for cls in contracts.RUN_SCOPED_HALT_CLASSES:
            self.assertIn(cls, contracts.HALT_CLASSES)
            self.assertNotIn(cls, contracts.FINDING_CLASSES)

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

    @classmethod
    def setUpClass(cls):
        cls.read_keys = _digest_keys_read(RUN_PY_PATH) | _digest_keys_read(CLOSEOUT_PY_PATH)

    def test_readers_stay_inside_digest_keys(self):
        unknown = self.read_keys - contracts.DIGEST_KEYS
        self.assertFalse(unknown, "reader(s) use digest key(s) not in DIGEST_KEYS: %s" % unknown)

    def test_classify_sets_every_key_readers_use(self):
        required = self.read_keys & contracts.DIGEST_KEYS
        result = classify.classify("/nonexistent/path.jsonl", SimpleNamespace(timed_out=False, exit_code=None))
        missing = required - set(result)
        self.assertFalse(missing, "classify() no longer sets key(s) readers depend on: %s" % missing)


class ClaudeDirScanRegex(unittest.TestCase):
    """The leading character class in CLAUDE_DIR_SCAN_REGEX (brief.py:147's one consumer)
    must catch a path wrapped in markdown link, bold, italic, or list-marker syntax, while
    still not matching the prefix as the tail of a longer word."""

    def _matches(self, text):
        return bool(contracts.CLAUDE_DIR_SCAN_REGEX.search(text))

    def test_markdown_link_display_text_matches(self):
        self.assertTrue(self._matches("[.claude/skills/x/SKILL.md](docs/x.md)"))

    def test_bold_matches(self):
        self.assertTrue(self._matches("**.claude/settings.json**"))

    def test_italic_matches(self):
        self.assertTrue(self._matches("*.claude/settings.json*"))

    def test_list_marker_with_no_space_matches(self):
        self.assertTrue(self._matches("*.claude/hooks/pre.sh"))

    def test_forms_that_already_matched_still_match(self):
        forms = [
            ".claude/skills/foo/SKILL.md",  # start of line
            "edit .claude/skills/foo/SKILL.md",  # whitespace
            'the file ".claude/settings.json" needs a hook',  # double quote
            "the file '.claude/settings.json' needs a hook",  # single quote
            "see `.claude/hooks/pre.sh`",  # backtick
            "(.claude/agents/x.md) is stale",  # open paren
            "path /Users/x/repo/.claude/skills/y/SKILL.md",  # mid-path slash
        ]
        for form in forms:
            with self.subTest(form=form):
                self.assertTrue(self._matches(form), "no longer matches: %s" % form)

    def test_prefix_as_suffix_of_a_longer_word_does_not_match(self):
        self.assertFalse(self._matches("x.claude/skills/y/SKILL.md"))

    def test_bare_word_claude_with_no_path_does_not_match(self):
        self.assertFalse(self._matches("run this under claude and check the output"))


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
