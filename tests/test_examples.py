"""U11: the shipped skill and the example manifests.

Two things are proved here. Every example validates against a real temp repo, so a fresh clone
can run `relay validate` on one without editing Relay (R38). And nothing in what ships names a
real project, tracker site, or person (R40).
"""
import glob
import os
import re
import unittest

import _paths
import _repo
from relay import cli, manifest as mf

REPO_ROOT = _paths.REPO_ROOT
EXAMPLES = os.path.join(REPO_ROOT, "docs", "examples")
SKILL = os.path.join(REPO_ROOT, "skills", "relay", "SKILL.md")

# Every verb the plan's runner subcommand table names.
VERBS = ("validate", "run", "status", "tail", "summary", "verify", "lease")

# What must never appear in anything Relay ships (R40). These are the shapes a real project
# leaks in: a Jira key, the operator's own repo, and a live Atlassian site.
LEAK_PATTERNS = (
    r"IW-[0-9]+",
    r"support-workbench",
    r"[a-z0-9-]+\.atlassian\.net/[a-z]",
)
SHIPPED = ("skills", "docs/examples", "README.md")


def example_paths():
    return sorted(glob.glob(os.path.join(EXAMPLES, "*.toml")))


class Examples(unittest.TestCase):
    def setUp(self):
        import tempfile

        self.tmp = tempfile.TemporaryDirectory()
        self.repo = _repo.make_repo(self.tmp.name, files={"tracker.md": "- [ ] T-1 A task\n"})

    def tearDown(self):
        self.tmp.cleanup()

    def localised(self, path):
        """Point an example at the temp repo, the way an operator points it at their own."""
        with open(path) as handle:
            text = handle.read()
        text = re.sub(r'^repo = ".*"$', 'repo = "%s"' % self.repo, text, count=1, flags=re.M)
        target = os.path.join(self.tmp.name, os.path.basename(path))
        with open(target, "w") as handle:
            handle.write(text)
        return target

    def test_three_examples_ship_one_per_adapter(self):
        adapters = set()
        for path in example_paths():
            adapters.add(mf.load(path).tracker.adapter)
        self.assertEqual(adapters, {"jira", "github", "markdown"})

    def test_every_example_loads_and_validates_against_a_real_repo(self):
        env = {"JIRA_API_TOKEN": "placeholder", "JIRA_EMAIL": "placeholder@example.invalid"}
        for path in example_paths():
            with self.subTest(example=os.path.basename(path)):
                manifest = mf.load(self.localised(path))
                result = mf.validate(manifest, env=dict(os.environ, **env))
                self.assertEqual(result.errors, [], os.path.basename(path))

    def test_every_example_names_its_four_qualifying_satisfiers(self):
        for path in example_paths():
            manifest = mf.load(path)
            for key in mf.QUALIFYING_KEYS:
                self.assertTrue(getattr(manifest.qualifying, key).strip(),
                                "%s has no %s satisfier" % (os.path.basename(path), key))

    def test_every_example_gate_and_mirror_are_argument_lists(self):
        for path in example_paths():
            manifest = mf.load(path)
            self.assertIsInstance(manifest.gate.command, tuple, os.path.basename(path))
            self.assertIsInstance(manifest.project.mirror, tuple, os.path.basename(path))

    def test_no_example_names_bypass_permissions_or_a_permission_mode(self):
        for path in example_paths():
            with open(path) as handle:
                text = handle.read()
            self.assertNotIn("bypassPermissions", text)
            self.assertNotIn("permission_mode", text)

    def test_the_validate_verb_accepts_every_example_through_the_cli(self):
        import io

        env = dict(os.environ, JIRA_API_TOKEN="placeholder", JIRA_EMAIL="p@example.invalid")
        for path in example_paths():
            with self.subTest(example=os.path.basename(path)):
                out = io.StringIO()
                code = cli.main(["validate", self.localised(path)], env=env, out=out)
                self.assertEqual(code, cli.EXIT_OK, out.getvalue())


class Skill(unittest.TestCase):
    def setUp(self):
        with open(SKILL) as handle:
            self.text = handle.read()

    def test_the_runner_script_is_resolved_from_the_skill_directory(self):
        self.assertIn("scripts/relay_cli.py", self.text)
        self.assertRegex(self.text, r"(?i)this skill's (own )?directory")

    def test_every_runner_verb_appears_with_an_invocation(self):
        """The skill resolves the script path once and then invokes it as <runner>, so an
        invocation is a line running <runner> with the verb."""
        for verb in VERBS:
            self.assertRegex(self.text, r"(?m)^python3 <runner> %s\b" % verb,
                             "SKILL.md has no invocation for the %s verb" % verb)

    def test_the_skill_launches_detached_the_way_ktd14_names(self):
        for fragment in ("setsid", "caffeinate", "runner.log"):
            self.assertIn(fragment, self.text)
        self.assertRegex(self.text, r"(?i)lid close")

    def test_the_skill_refuses_to_launch_without_the_four_qualifying_satisfiers(self):
        for key in mf.QUALIFYING_KEYS:
            self.assertIn("qualifying.%s" % key, self.text)
        self.assertRegex(self.text, r"(?i)refuse")

    def test_the_skill_diagnoses_from_state_rather_than_from_a_transcript(self):
        self.assertNotRegex(self.text, r"(?i)read the transcript")
        self.assertRegex(self.text, r"(?i)do not open a session transcript")
        self.assertRegex(self.text, r"(?m)^python3 <runner> summary <manifest> --json")

    def test_the_skill_names_no_permission_mode_but_dont_ask(self):
        self.assertNotIn("bypassPermissions", self.text)

    def test_the_skill_points_at_the_backend_rubric_in_its_own_directory(self):
        self.assertIn("references/backend-rubric.md", self.text)
        self.assertRegex(self.text, r"(?i)do not write a backend the operator")
        self.assertRegex(self.text, r"(?i)unenforced_acceptance")
        self.assertRegex(self.text, r"(?i)never invent")

    def test_the_skill_still_carries_its_frontmatter_name_and_description(self):
        self.assertRegex(self.text, r"(?m)^name: relay$")
        self.assertRegex(self.text, r"(?m)^description: .{40,}$")
        self.assertNotRegex(self.text, r"(?i)stub")


class NoProjectLeakage(unittest.TestCase):
    def shipped_files(self):
        for entry in SHIPPED:
            path = os.path.join(REPO_ROOT, entry)
            if os.path.isfile(path):
                yield path
                continue
            for root, _, names in os.walk(path):
                if "__pycache__" in root:
                    continue
                for name in names:
                    yield os.path.join(root, name)

    def test_nothing_shipped_names_a_real_project_tracker_or_person(self):
        for path in self.shipped_files():
            with open(path, encoding="utf-8", errors="replace") as handle:
                text = handle.read()
            for pattern in LEAK_PATTERNS:
                found = re.search(pattern, text)
                self.assertIsNone(found, "%s names %r" % (os.path.relpath(path, REPO_ROOT),
                                                          found.group(0) if found else ""))


class Readme(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(REPO_ROOT, "README.md")) as handle:
            self.text = handle.read()

    def test_the_readme_tells_a_fresh_machine_how_to_install_and_validate(self):
        self.assertRegex(self.text, r"(?i)install")
        self.assertIn("relay_cli.py", self.text)
        self.assertIn("docs/examples/", self.text)

    def test_the_readme_points_at_the_plan_and_the_vocabulary(self):
        self.assertIn("docs/plans/2026-08-25-1346-feat-relay-outer-loop-plan.md", self.text)
        self.assertIn("CONCEPTS.md", self.text)


if __name__ == "__main__":
    unittest.main()
