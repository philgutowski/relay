"""U2: the manifest loads into typed values and every validation rule names its field."""
import os
import re
import subprocess
import tempfile
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest import mock

import _paths
import _repo
from relay import backends, contracts, manifest as mf

FIXTURE = os.path.join(_paths.FIXTURES_DIR, "manifests", "complete.toml")

# One model name each backend is known to accept, for a fixture that moves a Task off claude.
# R9 refuses a backend paired with a model another backend claims, so a fixture that reassigns
# a Task's backend has to reassign its model in the same edit.
BACKEND_MODELS = {"claude": "opus", "codex": "gpt-5-codex", "grok": "grok-4"}


def drop_table(text, name):
    """Remove a TOML table and its body up to the next table heading."""
    return re.sub(r"^\[%s\]\n(?:(?!^\[).*\n?)*" % name, "", text, flags=re.M)


class ManifestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = _repo.make_repo(self.tmp.name)
        with open(FIXTURE) as handle:
            self.base = handle.read().replace("__REPO__", self.repo)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, text, name="manifest.toml"):
        path = os.path.join(self.tmp.name, name)
        with open(path, "w") as handle:
            handle.write(text)
        return path

    def load(self, text=None):
        return mf.load(self.write(text if text is not None else self.base))

    def edit(self, pattern, replacement, count=1):
        new, n = re.subn(pattern, replacement, self.base, count=count, flags=re.MULTILINE)
        assert n == count, "edit did not match: %r" % pattern
        return new

    def remodel(self, text, task_id, model):
        """`text` with one Task's model replaced. Anchored on the Task id because the [closeout]
        table carries a `model` line of its own."""
        pattern = r'id = "%s"\nmodel = "[^"]*"' % re.escape(task_id)
        body = 'id = "%s"\nmodel = "%s"' % (task_id, model)
        new, n = re.subn(pattern, lambda _match: body, text, count=1)
        assert n == 1, "remodel did not match task %s" % task_id
        return new

    def retarget(self, text, task_id, backend, extra=""):
        """`text` with one Task moved onto `backend`, model and all. The model moves with the
        backend because R9 refuses a Task whose model belongs to a different CLI, so a fixture
        that only swapped the backend would be testing that refusal instead of its own rule."""
        text = self.remodel(text, task_id, BACKEND_MODELS[backend])
        return text.replace('id = "%s"' % task_id,
                            'id = "%s"\nbackend = "%s"%s' % (task_id, backend, extra), 1)


class CompleteManifest(ManifestCase):
    def test_every_field_is_typed_and_defaults_are_named(self):
        m = self.load()
        self.assertEqual(m.project.repo, self.repo)
        self.assertEqual(m.tracker.adapter, "markdown")
        self.assertEqual(m.gate.command, ("true",))
        self.assertEqual(m.timeouts.task_minutes, 90)
        self.assertEqual(m.tasks[0].id, "T-1")
        self.assertTrue(m.tasks[1].excluded)
        self.assertTrue(m.on_blocked.merge_partial)
        result = mf.validate(m)
        self.assertTrue(result.ok, result.errors)
        # The fixture is markdown tracked, so the Jira credential names are not defaults here:
        # the first Cratekit run's validate named them on a GitHub manifest.
        self.assertNotIn("tracker.token_env = 'JIRA_API_TOKEN'", result.defaults_applied)
        self.assertNotIn("tracker.email_env = 'JIRA_EMAIL'", result.defaults_applied)
        self.assertEqual(m.tracker.token_env, "")
        self.assertEqual(result.allowed_paths, ["docs/", "CONCEPTS.md", "tracker.md"])

    def test_the_jira_credential_names_default_only_under_the_jira_adapter(self):
        text = self.edit(r'^adapter = "markdown"$', 'adapter = "jira"\nbase_url = "https://x.atlassian.net"\nproject_key = "PROJ"')
        m = self.load(text)
        self.assertEqual(m.tracker.token_env, "JIRA_API_TOKEN")
        result = mf.validate(m)
        self.assertIn("tracker.token_env = 'JIRA_API_TOKEN'", result.defaults_applied)

    def test_defaults_apply_by_name_when_timeouts_and_closeout_are_absent(self):
        text = drop_table(self.base, "timeouts")
        text = drop_table(text, "closeout")
        m = self.load(text)
        self.assertEqual(m.timeouts.task_minutes, contracts.DEFAULT_TASK_TIMEOUT_MINUTES)
        self.assertEqual(m.closeout.model, contracts.DEFAULT_CLOSEOUT_MODEL)
        names = mf.validate(m).defaults_applied
        self.assertIn("timeouts.task_minutes = 120", names)
        self.assertIn("closeout.model = 'sonnet'", names)

    def test_on_halt_defaults_off_by_name_and_reads_true_when_set(self):
        """Issue #15: continuing past a halt is opt in, and the default is named like every
        other default so nothing applies silently (KTD11)."""
        m = self.load()
        self.assertFalse(m.on_halt.continue_past_task_halt)
        result = mf.validate(m)
        self.assertTrue(result.ok, result.errors)
        self.assertIn("on_halt.continue_past_task_halt = False", result.defaults_applied)
        m = self.load(self.base + "\n[on_halt]\ncontinue_past_task_halt = true\n")
        self.assertTrue(m.on_halt.continue_past_task_halt)
        result = mf.validate(m)
        self.assertTrue(result.ok, result.errors)
        self.assertFalse(any("on_halt" in d for d in result.defaults_applied))

    def test_frozen(self):
        m = self.load()
        with self.assertRaises(Exception):
            m.shipping_mode = "pr_terminal"

    def test_branch_prefix_defaults_to_relay_slash_and_is_named(self):
        m = self.load()
        self.assertEqual(m.project.branch_prefix, "relay/")
        result = mf.validate(m)
        self.assertTrue(result.ok, result.errors)
        self.assertIn("project.branch_prefix = 'relay/'", result.defaults_applied)

    def test_branch_prefix_override_is_not_a_named_default(self):
        m = self.load(self.edit(r"^mirror = \[\]", 'mirror = []\nbranch_prefix = "IW-"'))
        self.assertEqual(m.project.branch_prefix, "IW-")
        result = mf.validate(m)
        self.assertTrue(result.ok, result.errors)
        self.assertFalse(any("branch_prefix" in d for d in result.defaults_applied))

    def test_an_empty_branch_prefix_is_a_real_override(self):
        m = self.load(self.edit(r"^mirror = \[\]", 'mirror = []\nbranch_prefix = ""'))
        self.assertEqual(m.project.branch_prefix, "")
        result = mf.validate(m)
        self.assertTrue(result.ok, result.errors)
        self.assertFalse(any("branch_prefix" in d for d in result.defaults_applied))


class NegativeManifests(ManifestCase):
    def assert_error(self, text, fragment):
        result = mf.validate(mf.load(self.write(text)))
        self.assertFalse(result.ok)
        self.assertTrue(any(fragment in e for e in result.errors), "%r not in %r" % (fragment, result.errors))
        return result

    def test_pr_terminal_is_refused_as_unimplemented_rather_than_as_a_typo(self):
        """Decided 2026-08-26. The mode is in the schema, the example, and the brief templates,
        and the run loop has no sequence for it, so a run under it halts on its first task. The
        refusal belongs before the run starts, and it has to say why rather than reading like a
        misspelled mode name."""
        result = self.assert_error(self.edit(r'^mode = "local_merge"$', 'mode = "pr_terminal"'),
                                   "shipping.mode pr_terminal is not implemented")
        self.assertTrue(any("Use local_merge" in error for error in result.errors))
        self.assertFalse(any("must be one of" in error for error in result.errors),
                          "an unimplemented mode is not the same error as an unknown one")

    def test_an_unknown_shipping_mode_still_reads_as_a_typo(self):
        self.assert_error(self.edit(r'^mode = "local_merge"$', 'mode = "pr-terminal"'),
                          "shipping.mode must be one of")

    def test_gate_command_as_a_string_fails_naming_the_field(self):
        self.assert_error(self.edit(r'^command = \["true"\]', 'command = "make test"'), "gate.command must be a non-empty array")

    def test_mirror_as_a_string_fails(self):
        self.assert_error(self.edit(r"^mirror = \[\]", 'mirror = "git push origin main:master"'), "project.mirror must be an array")

    def test_a_non_string_branch_prefix_fails_naming_the_field(self):
        self.assert_error(self.edit(r"^mirror = \[\]", "mirror = []\nbranch_prefix = 1"),
                          "project.branch_prefix")

    def test_missing_qualifying_gate_names_the_property(self):
        self.assert_error(self.edit(r"^gate = \"A pre push hook.*$", ""), "qualifying.gate has no satisfier")

    def test_three_qualifying_sentences_fails_naming_the_fourth(self):
        self.assert_error(self.edit(r"^editors = .*$", ""), "qualifying.editors")

    def test_missing_force_push_pattern_is_added_with_a_warning(self):
        text = self.edit(r'^  "Bash\(git push --force\*\)",\n', "")
        result = mf.validate(mf.load(self.write(text)))
        self.assertTrue(result.ok, result.errors)
        self.assertIn("Bash(git push --force*)", result.disallowed)
        self.assertTrue(any("Bash(git push --force*)" in w for w in result.warnings))

    def test_bypass_permissions_in_allowed_fails(self):
        self.assert_error(self.edit(r'^allowed = \[', 'allowed = ["bypassPermissions", '), "bypassPermissions")

    def test_permission_mode_field_fails(self):
        self.assert_error(self.edit(r"^\[permissions\]", '[permissions]\npermission_mode = "dontAsk"'), "permission_mode is not a field")

    def test_a_repo_with_no_origin_remote_fails(self):
        """The runner pushes the merge, so a repo it cannot push to is refused before a run. The
        pr_terminal half of this rule went with the mode when validate started refusing it."""
        repo = _repo.make_repo(self.tmp.name, name="noremote", origin=False)
        self.assert_error(self.base.replace(self.repo, repo),
                          "local_merge requires an origin remote to push to")

    def test_excluded_without_reason_fails(self):
        self.assert_error(self.edit(r'^reason = .*$', ""), "excluded but carries no reason")

    def test_task_missing_effort_fails(self):
        self.assert_error(self.edit(r'^effort = "high"\n', ""), "tasks[0].effort is required")

    def test_repo_without_identity_fails_naming_both_keys(self):
        repo = _repo.make_repo(self.tmp.name, name="noid", identity=False)
        text = self.base.replace(self.repo, repo)
        result = mf.validate(mf.load(self.write(text)), env=_repo.scrubbed_env())
        joined = "\n".join(result.errors)
        self.assertIn("user.name", joined)
        self.assertIn("user.email", joined)

    def test_local_merge_requires_in_review_status(self):
        self.assert_error(self.edit(r'^in_review_status = .*$', ""), "tracker.in_review_status is required")

    def test_markdown_warns_that_the_no_envelope_route_cannot_fire(self):
        """Finding 20: the route needs a third card state the markdown line does not have."""
        result = mf.validate(mf.load(self.write(self.base)), env=_repo.scrubbed_env())
        self.assertEqual(result.errors, [])
        joined = "\n".join(result.warnings)
        self.assertIn("markdown adapter reports only open or closed", joined)
        self.assertIn("cannot fire", joined)

    def test_unknown_adapter_fails(self):
        self.assert_error(self.edit(r'^adapter = "markdown"', 'adapter = "trello"'), "tracker.adapter must be one of")

    def test_task_timeout_below_lease_ttl_fails(self):
        self.assert_error(self.edit(r"^task_minutes = 90", "task_minutes = 5"), "must exceed the lease TTL")

    def test_missing_required_table_raises(self):
        with self.assertRaises(mf.ManifestError) as ctx:
            mf.load(self.write(drop_table(self.base, "qualifying")))
        self.assertIn("qualifying", str(ctx.exception))

    def test_single_tasks_table_raises_manifest_error(self):
        text = self.base.replace("[[tasks]]", "[tasks]", 1)
        text = text[: text.rindex("[[tasks]]")]
        with self.assertRaises(mf.ManifestError) as ctx:
            mf.load(self.write(text))
        self.assertIn("[[tasks]]", str(ctx.exception))

    def test_non_list_allowed_is_reported_not_raised(self):
        self.assert_error(self.edit(r"^allowed = \[.*$", 'allowed = "Bash,Read"'), "permissions.allowed must be a non-empty array")

    def test_explicit_zero_timeout_is_rejected_not_defaulted(self):
        result = self.assert_error(self.edit(r"^closeout_minutes = 15", "closeout_minutes = 0"), "timeouts.closeout_minutes must be a positive integer")
        self.assertFalse(any("closeout_minutes" in d for d in result.defaults_applied))

    def test_bad_toml_raises(self):
        with self.assertRaises(mf.ManifestError):
            mf.load(self.write("this is = not [toml"))


class AllowedPaths(ManifestCase):
    def test_docs_root_from_target_config_yaml(self):
        os.makedirs(os.path.join(self.repo, ".compound-engineering"))
        with open(os.path.join(self.repo, ".compound-engineering", "config.yaml"), "w") as handle:
            handle.write("# checkout config\ncross_model_review_mode: off\ndocs_root: notes\n")
        result = mf.validate(self.load())
        self.assertEqual(result.allowed_paths, ["notes/", "CONCEPTS.md", "tracker.md"])

    def test_manifest_extras_are_appended(self):
        text = self.edit(r"^allowed_paths = \[\]", 'allowed_paths = ["CHANGELOG.md"]')
        result = mf.validate(mf.load(self.write(text)))
        self.assertEqual(result.allowed_paths[-1], "CHANGELOG.md")

    def test_jira_manifest_has_no_tracker_file_in_paths(self):
        text = self.edit(r'^adapter = "markdown"\nfile = "tracker.md"',
                         'adapter = "jira"\nsite = "example.atlassian.net"\nproject_key = "XX"')
        result = mf.validate(mf.load(self.write(text)))
        self.assertTrue(result.ok, result.errors)
        self.assertEqual(result.allowed_paths, ["docs/", "CONCEPTS.md"])


class Backends(ManifestCase):
    """U2: R1, R2, KTD11. A Task names the CLI it runs on, a manifest may default it, and an
    invalid value is refused rather than quietly replaced."""

    def test_a_manifest_with_no_backend_key_anywhere_puts_every_task_on_claude(self):
        # The backward-compatibility case: the fixture predates backends entirely.
        m = self.load()
        self.assertTrue(m.tasks)
        for task in m.tasks:
            self.assertEqual(task.backend, "claude")
        self.assertTrue(mf.validate(m).ok)

    def test_the_defaulted_backend_is_reported_rather_than_silent(self):
        result = mf.validate(self.load())
        self.assertIn("defaults.backend = 'claude'", result.defaults_applied)

    def _with_unenforced_gate(self, text):
        return text.replace("[permissions]",
                            '[permissions]\n'
                            'unenforced_acceptance = "fixture: operator accepts unenforced Codex"\n'
                            'task_allowed_paths = ["src/"]',
                            1)

    def test_a_defaults_table_value_is_inherited_by_a_task_that_names_none(self):
        text = self.base.replace("[[tasks]]", '[defaults]\nbackend = "codex"\n\n[[tasks]]', 1)
        # Both Tasks inherit codex, so both models move with them: R9 refuses a codex Task still
        # carrying the fixture's claude model.
        for task_id in ("T-1", "T-2"):
            text = self.remodel(text, task_id, BACKEND_MODELS["codex"])
        text = self._with_unenforced_gate(text)
        m = self.load(text)
        for task in m.tasks:
            self.assertEqual(task.backend, "codex")
        result = mf.validate(m)
        self.assertTrue(result.ok, result.errors)
        # The operator wrote this one, so it is not a default Relay applied.
        self.assertNotIn("defaults.backend = 'codex'", result.defaults_applied)

    def test_a_per_task_backend_overrides_the_default(self):
        text = self.base.replace("[[tasks]]", '[defaults]\nbackend = "codex"\n\n[[tasks]]', 1)
        text = self.retarget(text, "T-1", "grok", '\nreason = "fixture: grok for this Task"')
        text = self.remodel(text, "T-2", BACKEND_MODELS["codex"])
        text = self._with_unenforced_gate(text)
        m = self.load(text)
        self.assertEqual(m.tasks[0].backend, "grok")
        self.assertEqual(m.tasks[1].backend, "codex")
        self.assertTrue(mf.validate(m).ok)

    def test_a_mixed_manifest_validates(self):
        text = self._with_unenforced_gate(
            self.retarget(self.base, "T-1", "codex", '\nreason = "fixture: mixed Codex Task"'))
        m = self.load(text)
        self.assertEqual([t.backend for t in m.tasks], ["codex", "claude"])
        self.assertTrue(mf.validate(m).ok)

    def test_an_unrecognized_backend_is_refused_and_names_the_valid_set(self):
        text = self.base.replace('id = "T-1"', 'id = "T-1"\nbackend = "gpt5"', 1)
        result = mf.validate(self.load(text))
        self.assertFalse(result.ok)
        message = " ".join(result.errors)
        self.assertIn("backend", message)
        for name in mf.BACKENDS:
            self.assertIn(name, message)

    def test_an_empty_string_backend_is_refused_rather_than_silently_defaulted(self):
        text = self.base.replace('id = "T-1"', 'id = "T-1"\nbackend = ""', 1)
        m = self.load(text)
        self.assertEqual(m.tasks[0].backend, "")
        self.assertFalse(mf.validate(m).ok)

    def test_an_empty_defaults_backend_is_refused_for_every_inheriting_task(self):
        text = self.base.replace("[[tasks]]", '[defaults]\nbackend = ""\n\n[[tasks]]', 1)
        result = mf.validate(self.load(text))
        self.assertFalse(result.ok)
        self.assertTrue(any(error.startswith("defaults.backend") for error in result.errors))
        self.assertEqual(sum("tasks[" in e and ".backend" in e for e in result.errors),
                         len(self.load(text).tasks))


class BackendReason(ManifestCase):
    """U13: a Task whose backend differs from the resolved default needs a reason string."""

    def _mixed_codex(self, extra_task="", extra_permissions=None):
        # retarget, not a bare backend swap: R9 refuses a codex Task still carrying the
        # fixture's claude model, and this class is about the reason rule, not that one.
        text = self.retarget(self.base, "T-1", "codex", extra_task)
        perms = extra_permissions if extra_permissions is not None else (
            'unenforced_acceptance = "fixture: operator accepts unenforced Codex"\n'
            'task_allowed_paths = ["src/"]')
        if perms:
            text = text.replace("[permissions]", "[permissions]\n" + perms, 1)
        return text

    def test_a_task_that_differs_from_the_default_without_a_reason_is_refused(self):
        result = mf.validate(self.load(self._mixed_codex()))
        self.assertFalse(result.ok)
        self.assertTrue(any("no reason" in error for error in result.errors))

    def test_a_non_empty_reason_lets_a_differing_backend_validate(self):
        text = self._mixed_codex('\nreason = "fixture: spend Codex budget on mechanical work"')
        self.assertTrue(mf.validate(self.load(text)).ok)

    def test_a_task_that_inherits_the_default_needs_no_reason(self):
        self.assertTrue(mf.validate(self.load()).ok)
        self.assertIsNone(self.load().tasks[0].reason)

    def test_a_task_that_names_the_default_explicitly_needs_no_reason(self):
        text = self.base.replace('id = "T-1"', 'id = "T-1"\nbackend = "claude"', 1)
        result = mf.validate(self.load(text))
        self.assertTrue(result.ok, result.errors)

    def test_a_task_that_repeats_a_non_claude_default_needs_no_reason(self):
        text = self.base.replace("[[tasks]]", '[defaults]\nbackend = "codex"\n\n[[tasks]]', 1)
        text = self.retarget(text, "T-1", "codex")
        # T-2 inherits the codex default, so its model moves with it too (R9).
        text = self.remodel(text, "T-2", BACKEND_MODELS["codex"])
        text = text.replace("[permissions]",
                            '[permissions]\n'
                            'unenforced_acceptance = "fixture: operator accepts unenforced Codex"\n'
                            'task_allowed_paths = ["src/"]',
                            1)
        self.assertTrue(mf.validate(self.load(text)).ok, mf.validate(self.load(text)).errors)

    def test_a_task_that_leaves_a_non_claude_default_without_a_reason_is_refused(self):
        text = self.base.replace("[[tasks]]", '[defaults]\nbackend = "codex"\n\n[[tasks]]', 1)
        text = self.retarget(text, "T-1", "claude")
        text = self.remodel(text, "T-2", BACKEND_MODELS["codex"])
        text = text.replace("[permissions]",
                            '[permissions]\n'
                            'unenforced_acceptance = "fixture: operator accepts unenforced Codex"\n'
                            'task_allowed_paths = ["src/"]',
                            1)
        result = mf.validate(self.load(text))
        self.assertFalse(result.ok)
        self.assertTrue(any("differs from the default" in error for error in result.errors))

    def test_a_grok_task_without_a_reason_is_refused_without_unenforced_fields(self):
        text = self.retarget(self.base, "T-1", "grok")
        result = mf.validate(self.load(text))
        self.assertFalse(result.ok)
        self.assertTrue(any("differs from the default" in error for error in result.errors))
        self.assertFalse(any("unenforced_acceptance" in error for error in result.errors))

    def test_an_invalid_defaults_backend_does_not_skip_the_reason_check(self):
        text = self.base.replace("[[tasks]]", '[defaults]\nbackend = "CODEX"\n\n[[tasks]]', 1)
        text = self.retarget(text, "T-1", "grok")
        result = mf.validate(self.load(text))
        self.assertFalse(result.ok)
        joined = " ".join(result.errors)
        self.assertIn("defaults.backend", joined)
        self.assertTrue(any("differs from the default" in error for error in result.errors))

    def test_whitespace_only_reason_is_refused_when_the_backend_differs(self):
        text = self._mixed_codex('\nreason = "   "')
        result = mf.validate(self.load(text))
        self.assertFalse(result.ok)
        self.assertTrue(any("no reason" in error for error in result.errors))

    def test_an_excluded_task_on_the_default_still_fails_only_the_excluded_check(self):
        text = self.edit(r'^reason = .*$', 'reason = ""')
        result = mf.validate(self.load(text))
        self.assertFalse(result.ok)
        joined = " ".join(result.errors)
        self.assertIn("excluded but carries no reason", joined)
        self.assertFalse(any("differs from the default" in error for error in result.errors))

    def test_one_reason_covers_exclusion_and_a_differing_backend(self):
        text = self.retarget(self.base, "T-2", "codex")
        text = text.replace("[permissions]",
                            '[permissions]\n'
                            'unenforced_acceptance = "fixture: operator accepts unenforced Codex"\n'
                            'task_allowed_paths = ["src/"]',
                            1)
        self.assertTrue(mf.validate(self.load(text)).ok)
        text_missing = text.replace('reason = "brief says stop and ask on the schema question"',
                                    'reason = ""')
        result = mf.validate(self.load(text_missing))
        self.assertFalse(result.ok)
        joined = " ".join(result.errors)
        self.assertIn("excluded but carries no reason", joined)
        self.assertTrue(any("differs from the default" in error for error in result.errors))


class BackendModelCoherence(ManifestCase):
    """U6: R9, KTD10, KTD11. A resolved backend paired with a model another backend claims is
    refused at validate, before any Task launches. A model no backend claims passes."""

    CODEX_PERMISSIONS = ('unenforced_acceptance = "fixture: operator accepts unenforced Codex"\n'
                         'task_allowed_paths = ["src/"]')

    def _all_on(self, backend, model, permissions=""):
        """The fixture with a [defaults] backend and both Tasks carrying the same model. This is
        the reported hazard's own shape: one [defaults] edit, every Task's old model string
        handed to the new CLI."""
        text = self.base.replace("[[tasks]]", '[defaults]\nbackend = "%s"\n\n[[tasks]]' % backend, 1)
        for task_id in ("T-1", "T-2"):
            text = self.remodel(text, task_id, model)
        if permissions:
            text = text.replace("[permissions]", "[permissions]\n" + permissions, 1)
        return text

    def test_a_grok_default_with_a_claude_model_is_refused_naming_the_task(self):
        # AE7. The fixture's T-2 keeps its claude model, sonnet, while [defaults] sends it to grok.
        text = self.base.replace("[[tasks]]", '[defaults]\nbackend = "grok"\n\n[[tasks]]', 1)
        result = mf.validate(self.load(text))
        self.assertFalse(result.ok)
        offending = [error for error in result.errors if "T-2" in error]
        self.assertTrue(offending, result.errors)
        self.assertTrue(any("grok" in error and "sonnet" in error for error in offending), offending)

    def test_a_model_no_backend_claims_is_allowed_through(self):
        # KTD11: the check is negative, so a model name Relay has never heard of is not refused.
        # A positive allowlist would refuse this the day a provider ships a new model.
        result = mf.validate(self.load(self._all_on("grok", "mercury-2")))
        self.assertTrue(result.ok, result.errors)

    def test_a_backend_paired_with_its_own_model_validates(self):
        for backend, model in BACKEND_MODELS.items():
            with self.subTest(backend=backend):
                permissions = self.CODEX_PERMISSIONS if backend == "codex" else ""
                result = mf.validate(self.load(self._all_on(backend, model, permissions)))
                self.assertTrue(result.ok, result.errors)

    def test_an_invalid_defaults_backend_does_not_skip_the_mismatch_check(self):
        # An invalid reference value must not switch off an unrelated per Task rule, the trap
        # recorded in docs/solutions/logic-errors/
        # invalid-defaults-backend-silently-turned-off-the-reason-check.md. Both Tasks name grok
        # themselves and keep their claude models.
        text = self.base.replace("[[tasks]]", '[defaults]\nbackend = "GROK"\n\n[[tasks]]', 1)
        text = text.replace('id = "T-1"', 'id = "T-1"\nbackend = "grok"', 1)
        text = text.replace('id = "T-2"', 'id = "T-2"\nbackend = "grok"', 1)
        result = mf.validate(self.load(text))
        self.assertFalse(result.ok)
        self.assertTrue(any(error.startswith("defaults.backend") for error in result.errors))
        mismatches = [error for error in result.errors if "belongs to backend" in error]
        self.assertEqual(len(mismatches), 2, result.errors)

    def test_a_model_two_backends_both_claim_is_valid_on_both(self):
        # No two capability records share a name today, so the overlap is staged here rather
        # than waiting for a provider to ship one.
        grok = backends.build("grok")
        shared = replace(grok.CAPABILITY,
                         known_models=grok.CAPABILITY.known_models + ("opus",))
        with mock.patch.object(grok, "CAPABILITY", shared):
            result = mf.validate(self.load(self._all_on("grok", "opus")))
        self.assertTrue(result.ok, result.errors)
        self.assertTrue(mf.validate(self.load(self._all_on("claude", "opus"))).ok)


class BackendReadiness(ManifestCase):
    """U3: capability-record preflight runs for CLI validation, not schema reads."""

    def environment(self):
        return dict(os.environ, PATH="/test-bin")

    def plugin_result(self, output, code=0):
        return SimpleNamespace(returncode=code, stdout=output, stderr="")

    def claude_plugin_output(self, version="3.23.4", status="enabled"):
        symbol = "✔" if status == "enabled" else "✘"
        return ("  ❯ compound-engineering@compound-engineering-plugin\n"
                "    Version: %s\n"
                "    Scope: user\n"
                "    Status: %s %s" % (version, symbol, status))

    def test_missing_binary_names_the_backend_before_launch(self):
        with mock.patch.object(mf.shutil, "which", return_value=None) as which, \
                mock.patch.object(mf, "_run_plugin_query") as run:
            result = mf.validate(self.load(), check_repo=False, check_environment=True, env=self.environment())
        self.assertFalse(result.ok)
        self.assertTrue(any("claude" in error and "binary" in error for error in result.errors))
        which.assert_called_once_with("claude", path="/test-bin")
        run.assert_not_called()

    def test_missing_plugin_is_distinct_from_missing_binary(self):
        with mock.patch.object(mf.shutil, "which", return_value="/test-bin/claude"), \
                mock.patch.object(mf, "_run_plugin_query", return_value=self.plugin_result("other-plugin 9.0.0")):
            result = mf.validate(self.load(), check_repo=False, check_environment=True, env=self.environment())
        self.assertFalse(result.ok)
        self.assertTrue(any("claude" in error and "plugin" in error for error in result.errors))
        self.assertFalse(any("binary" in error for error in result.errors))

    def test_disabled_plugin_is_refused_even_at_a_qualifying_version(self):
        # skills-relay-contracts-129-disabled-plugin-ready: a listed, version-qualifying plugin
        # that is disabled cannot provide the skills a Task process needs.
        with mock.patch.object(mf.shutil, "which", return_value="/test-bin/claude"), \
                mock.patch.object(mf, "_run_plugin_query",
                                  return_value=self.plugin_result(self.claude_plugin_output(status="disabled"))):
            result = mf.validate(self.load(), check_repo=False, check_environment=True, env=self.environment())
        self.assertFalse(result.ok)
        self.assertTrue(any("claude" in error and "plugin" in error for error in result.errors))

    def test_the_stub_binary_produces_output_the_real_pattern_accepts(self):
        # End-to-end, no mocking of _run_plugin_query: runs the actual stub `claude` subprocess
        # and feeds its real stdout through the real extraction regex, so the stub's plugin-list
        # shape and the pattern it stands in for cannot silently drift apart the way every other
        # test in this class (which mocks _run_plugin_query) would never catch.
        capability = backends.build("claude").CAPABILITY
        env = dict(os.environ, PATH=_paths.STUB_DIR + os.pathsep + os.environ.get("PATH", ""))
        completed = mf._run_plugin_query(capability, env)
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(mf._plugin_version(capability, completed.stdout), "3.23.4")

    def test_below_floor_plugin_is_refused(self):
        with mock.patch.object(mf.shutil, "which", return_value="/test-bin/claude"), \
                mock.patch.object(mf, "_run_plugin_query", return_value=self.plugin_result(self.claude_plugin_output("3.0.0"))):
            result = mf.validate(self.load(), check_repo=False, check_environment=True, env=self.environment())
        self.assertFalse(result.ok)
        self.assertTrue(any("3.23.4" in error for error in result.errors))

    def test_each_distinct_backend_is_probed_once(self):
        text = self.base.replace('id = "T-2"', 'id = "T-2"\nbackend = "claude"', 1)
        with mock.patch.object(mf.shutil, "which", return_value="/test-bin/claude") as which, \
                mock.patch.object(mf, "_run_plugin_query", return_value=self.plugin_result(self.claude_plugin_output())) as run:
            result = mf.validate(self.load(text), check_repo=False, check_environment=True, env=self.environment())
        self.assertTrue(result.ok, result.errors)
        which.assert_called_once()
        run.assert_called_once()

    def test_schema_validation_skips_backend_environment_probes(self):
        def boom(*_args, **_kwargs):
            raise AssertionError("schema validation must not probe backends")

        with mock.patch.object(mf.shutil, "which", boom), mock.patch.object(mf, "_run_plugin_query", boom):
            result = mf.validate(self.load())
        self.assertTrue(result.ok, result.errors)

    def test_a_probe_exception_is_reported_as_a_validation_error(self):
        exceptions = (
            subprocess.TimeoutExpired(cmd=["claude"], timeout=15),
            OSError("no such file or directory"),
        )
        for exc in exceptions:
            with self.subTest(exc=type(exc).__name__), \
                    mock.patch.object(mf.shutil, "which", return_value="/test-bin/claude"), \
                    mock.patch.object(mf, "_run_plugin_query", side_effect=exc):
                result = mf.validate(self.load(), check_repo=False, check_environment=True, env=self.environment())
                self.assertFalse(result.ok)
                self.assertTrue(any("claude" in error and "probe failed" in error for error in result.errors))

    def test_jira_codex_pair_is_refused_without_environment_probes(self):
        text = self.base.replace('adapter = "markdown"\nfile = "tracker.md"',
                                 'adapter = "jira"\nsite = "example.atlassian.net"\nproject_key = "XX"')
        text = text.replace('id = "T-1"',
                            'id = "T-1"\nbackend = "codex"\nreason = "fixture: mixed Codex Task"', 1)
        text = text.replace("[permissions]",
                            '[permissions]\n'
                            'unenforced_acceptance = "fixture: operator accepts unenforced Codex"\n'
                            'task_allowed_paths = ["src/"]',
                            1)
        result = mf.validate(self.load(text))
        self.assertFalse(result.ok)
        self.assertTrue(any("jira" in error and "codex" in error for error in result.errors))


class TaskAllowedPaths(ManifestCase):
    """U2: R21, KTD13. The Task path bound is opt in, and it is not the Closeout's list."""

    def test_unset_resolves_to_the_whole_repository(self):
        m = self.load()
        self.assertEqual(m.permissions.task_allowed_paths, ())
        # None, not an empty tuple: gitwrite.path_allowed reads empty as "allow nothing", so a
        # caller that could not tell the two apart would refuse every merge on this manifest.
        self.assertIsNone(mf.task_allowed_paths(m))
        self.assertTrue(mf.validate(m).ok)

    def test_set_resolves_to_the_named_prefixes(self):
        text = self.edit(r"^\[permissions\]$",
                         '[permissions]\ntask_allowed_paths = ["toolkit/", "README.md"]')
        m = self.load(text)
        self.assertEqual(mf.task_allowed_paths(m), ("toolkit/", "README.md"))
        self.assertTrue(mf.validate(m).ok, mf.validate(m).errors)

    def test_it_is_never_confused_with_the_closeouts_own_list(self):
        text = self.edit(r"^\[permissions\]$",
                         '[permissions]\ntask_allowed_paths = ["toolkit/"]')
        m = self.load(text)
        result = mf.validate(m)
        # The Closeout's set is the docs root and the tracker file, which would refuse every
        # code Task's own commit. The two must not collapse into one another.
        self.assertEqual(result.allowed_paths, ["docs/", "CONCEPTS.md", "tracker.md"])
        self.assertEqual(mf.task_allowed_paths(m), ("toolkit/",))
        self.assertNotIn("toolkit/", result.allowed_paths)

    def test_an_absolute_or_escaping_entry_is_refused(self):
        for bad in ('["/toolkit/"]', '["../outside/"]'):
            text = self.edit(r"^\[permissions\]$", "[permissions]\ntask_allowed_paths = %s" % bad)
            result = mf.validate(self.load(text))
            self.assertFalse(result.ok, bad)

    def test_an_empty_entry_is_refused(self):
        text = self.edit(r"^\[permissions\]$", '[permissions]\ntask_allowed_paths = ["", "toolkit/"]')
        self.assertFalse(mf.validate(self.load(text)).ok)


class UnenforcedAcceptance(ManifestCase):
    """Parent R19. A Codex Task cannot validate without the sentence and a set bound."""

    def _codex_task(self, extra_permissions=""):
        # The model moves to codex with the backend, so R9 is not what refuses these fixtures.
        text = self.retarget(self.base, "T-1", "codex",
                             '\nreason = "fixture: mixed Codex Task"')
        if extra_permissions:
            text = text.replace("[permissions]", "[permissions]\n" + extra_permissions, 1)
        return text

    def test_a_codex_task_without_the_sentence_is_refused(self):
        text = self._codex_task('task_allowed_paths = ["src/"]')
        result = mf.validate(self.load(text))
        self.assertFalse(result.ok)
        self.assertTrue(any("unenforced_acceptance" in error for error in result.errors))

    def test_a_codex_task_without_the_bound_is_refused(self):
        text = self._codex_task('unenforced_acceptance = "fixture: operator accepts unenforced Codex"')
        result = mf.validate(self.load(text))
        self.assertFalse(result.ok)
        self.assertTrue(any("task_allowed_paths must be set" in error for error in result.errors))

    def test_adding_both_lets_it_validate(self):
        text = self._codex_task(
            'unenforced_acceptance = "fixture: operator accepts unenforced Codex"\n'
            'task_allowed_paths = ["src/"]')
        self.assertTrue(mf.validate(self.load(text)).ok)

    def test_a_claude_only_manifest_needs_neither_field(self):
        self.assertTrue(mf.validate(self.load()).ok)

    def test_an_excluded_codex_task_still_requires_both_fields(self):
        text = self.retarget(self.base, "T-2", "codex")
        result = mf.validate(self.load(text))
        self.assertFalse(result.ok)
        joined = " ".join(result.errors)
        self.assertIn("unenforced_acceptance", joined)
        self.assertIn("task_allowed_paths must be set", joined)

    def test_an_empty_bound_list_is_refused_for_codex(self):
        text = self._codex_task(
            'unenforced_acceptance = "fixture: operator accepts unenforced Codex"\n'
            'task_allowed_paths = []')
        result = mf.validate(self.load(text))
        self.assertFalse(result.ok)
        self.assertTrue(any("task_allowed_paths must be set" in error for error in result.errors))

    def test_whitespace_only_sentence_is_refused(self):
        text = self._codex_task(
            'unenforced_acceptance = "   "\n'
            'task_allowed_paths = ["src/"]')
        result = mf.validate(self.load(text))
        self.assertFalse(result.ok)
        self.assertTrue(any("unenforced_acceptance" in error for error in result.errors))


if __name__ == "__main__":
    unittest.main()
