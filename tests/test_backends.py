"""Backends U4: one package answers every seam question per backend.

U1 is dispatch and the capability record. U2 is the shared callable surface.
Construction must work on a machine that has only claude.
"""
import os
import shutil
import subprocess
import sys
import unittest
from dataclasses import fields
from unittest import mock

import _paths  # noqa: F401
from relay import backends, contracts, manifest as mf


PLACEHOLDERS = ("", "TODO", "TBD")


class Dispatch(unittest.TestCase):
    def test_build_returns_the_named_module(self):
        for name in mf.BACKENDS:
            module = backends.build(name)
            self.assertEqual(module.__name__.rsplit(".", 1)[-1], name)

    def test_unknown_name_raises_configuration_error_naming_the_set(self):
        with self.assertRaises(backends.ConfigurationError) as raised:
            backends.build("unknown")
        message = str(raised.exception)
        self.assertIn("claude", message)
        self.assertIn("codex", message)
        self.assertIn("grok", message)

    def test_build_performs_no_subprocess_and_touches_no_filesystem(self):
        def boom(*_args, **_kwargs):
            raise AssertionError("build must not probe the machine")

        for name in mf.BACKENDS:
            sys.modules.pop("relay.backends." + name, None)
        with mock.patch.object(subprocess, "run", boom), \
                mock.patch.object(os.path, "exists", boom), \
                mock.patch.object(shutil, "which", boom):
            for name in mf.BACKENDS:
                backends.build(name)

    def test_the_three_closed_sets_are_equal(self):
        self.assertEqual(set(mf.BACKENDS), set(contracts.BACKEND_PINS))
        accepted = []
        for name in list(mf.BACKENDS) + ["unknown"]:
            try:
                backends.build(name)
            except backends.ConfigurationError:
                continue
            accepted.append(name)
        self.assertEqual(set(accepted), set(mf.BACKENDS))


class CapabilityRecord(unittest.TestCase):
    def test_every_record_is_a_complete_copy_of_the_pins(self):
        declared = {item.name for item in fields(backends.Capability)}
        for name in mf.BACKENDS:
            pins = contracts.BACKEND_PINS[name]
            self.assertEqual(set(pins), declared, name)
            record = backends.build(name).CAPABILITY
            rebuilt = backends.Capability(**pins)
            self.assertEqual(record, rebuilt, name)
            for field, value in pins.items():
                if isinstance(value, str):
                    self.assertNotIn(value, PLACEHOLDERS, "%s.%s" % (name, field))

    def test_enforces_at_launch_is_the_demonstrated_bit(self):
        self.assertTrue(backends.build("claude").CAPABILITY.enforces_at_launch)
        self.assertFalse(backends.build("codex").CAPABILITY.enforces_at_launch)
        self.assertTrue(backends.build("grok").CAPABILITY.enforces_at_launch)

    def test_credential_list_is_prefixes_only(self):
        self.assertFalse(hasattr(backends.Capability, "credential_variables"))
        for name in mf.BACKENDS:
            prefixes = backends.build(name).CAPABILITY.credential_prefixes
            self.assertTrue(prefixes, name)

    def test_forbidden_spellings_are_non_empty_and_include_the_u1_findings(self):
        grok = backends.build("grok").CAPABILITY.forbidden_permission_modes
        self.assertTrue(grok)
        self.assertIn("dontAsk", grok)
        codex = backends.build("codex").CAPABILITY.forbidden_permission_modes
        self.assertIn("--dangerously-bypass-approvals-and-sandbox", codex)
        self.assertTrue(backends.build("claude").CAPABILITY.forbidden_permission_modes)

    def test_extra_writable_dirs_is_uniform_and_codex_keeps_the_git_token(self):
        self.assertEqual(backends.build("claude").CAPABILITY.extra_writable_dirs, ())
        self.assertEqual(backends.build("grok").CAPABILITY.extra_writable_dirs, ())
        self.assertEqual(backends.build("codex").CAPABILITY.extra_writable_dirs, ("<repo>/.git",))

    def test_codex_allow_and_deny_flags_may_be_none(self):
        cap = backends.build("codex").CAPABILITY
        self.assertIsNone(cap.allow_flag)
        self.assertIsNone(cap.deny_flag)

    def test_plugin_version_pattern_is_present_for_each_backend(self):
        for name in mf.BACKENDS:
            self.assertTrue(backends.build(name).CAPABILITY.plugin_version_pattern, name)

    def test_plugin_version_patterns_parse_the_observed_list_shapes(self):
        samples = {
            "claude": ("  ❯ compound-engineering@compound-engineering-plugin\n"
                       "    Version: 3.23.4\n"
                       "    Scope: user\n"
                       "    Status: ✔ enabled"),
            "codex": "compound-engineering@compound-engineering-plugin  installed, enabled  3.23.4   /tmp/plugin",
            "grok": '{"name":"compound-engineering", "version":"3.23.4"}',
        }
        for name, output in samples.items():
            self.assertEqual(mf._plugin_version(backends.build(name).CAPABILITY, output), "3.23.4", name)

    def test_grok_pattern_does_not_borrow_another_plugins_version(self):
        output = ('[{"name":"compound-engineering", "version":"3.0.0"}, '
                  '{"name":"other-plugin", "version":"9.0.0"}]')
        self.assertEqual(mf._plugin_version(backends.build("grok").CAPABILITY, output), "3.0.0")

    def test_claude_pattern_rejects_a_disabled_plugin(self):
        # skills-relay-contracts-129-disabled-plugin-ready: a disabled plugin still reports its
        # installed version on the `Version:` line, so readiness must also require `Status:`.
        output = ("  ❯ compound-engineering@compound-engineering-plugin\n"
                  "    Version: 3.23.4\n"
                  "    Scope: user\n"
                  "    Status: ✘ disabled")
        self.assertIsNone(mf._plugin_version(backends.build("claude").CAPABILITY, output))

    def test_claude_pattern_does_not_borrow_a_later_plugins_enabled_status(self):
        output = ("  ❯ compound-engineering@compound-engineering-plugin\n"
                  "    Version: 3.23.4\n"
                  "    Scope: user\n"
                  "    Status: ✘ disabled\n"
                  "\n"
                  "  ❯ other-plugin@other\n"
                  "    Version: 1.0.0\n"
                  "    Scope: user\n"
                  "    Status: ✔ enabled")
        self.assertIsNone(mf._plugin_version(backends.build("claude").CAPABILITY, output))

    def test_codex_pattern_already_excludes_a_disabled_status(self):
        # codex's CLI has no disable/enable subcommand and cannot currently produce this state,
        # but the pattern's literal "installed, enabled" requirement already excludes it.
        output = "compound-engineering@compound-engineering-plugin  installed, disabled  3.23.4   /tmp/plugin"
        self.assertIsNone(mf._plugin_version(backends.build("codex").CAPABILITY, output))

    def test_claude_pattern_accepts_a_status_line_with_no_glyph(self):
        # The glyph before "enabled"/"disabled" is optional in the pattern so a future CLI
        # dropping it doesn't silently stop matching; pin that branch directly.
        output = ("  ❯ compound-engineering@compound-engineering-plugin\n"
                  "    Version: 3.23.4\n"
                  "    Scope: user\n"
                  "    Status: enabled")
        self.assertEqual(mf._plugin_version(backends.build("claude").CAPABILITY, output), "3.23.4")


class SharedSurface(unittest.TestCase):
    def test_every_backend_implements_exactly_the_interface(self):
        for name in mf.BACKENDS:
            module = backends.build(name)
            for method in backends.INTERFACE:
                self.assertTrue(
                    callable(getattr(module, method, None)),
                    "%s lacks %s" % (name, method),
                )
            public = {
                attr for attr in dir(module)
                if not attr.startswith("_") and callable(getattr(module, attr))
            }
            self.assertEqual(public, set(backends.INTERFACE), "%s exposes more than the interface" % name)

    def test_parse_version_reads_each_observed_sample(self):
        for name in mf.BACKENDS:
            module = backends.build(name)
            sample = module.CAPABILITY.version_output_sample
            self.assertEqual(module.parse_version(sample), module.CAPABILITY.version_tested, name)

    def test_parse_version_returns_none_rather_than_raising(self):
        for name in mf.BACKENDS:
            module = backends.build(name)
            self.assertIsNone(module.parse_version(""))
            self.assertIsNone(module.parse_version("update available"))

    def test_parse_version_rejects_a_dotless_digit_in_an_update_banner(self):
        # A banner ahead of the real version line must not be mistaken for one just
        # because it starts with a digit after the name token is skipped.
        self.assertIsNone(backends.build("grok").parse_version("grok 3 updates available"))
        self.assertIsNone(backends.build("codex").parse_version("codex-cli 5 new updates"))
        self.assertIsNone(backends.build("claude").parse_version("3 updates available"))

    def test_qualify_skill_interpolates_the_pin_form(self):
        self.assertEqual(backends.build("claude").qualify_skill("ce-plan"), "compound-engineering:ce-plan")
        self.assertEqual(backends.build("codex").qualify_skill("ce-plan"), "$ce-plan")
        self.assertEqual(backends.build("grok").qualify_skill("ce-plan"), "/ce-plan")

    def test_deferred_callables_exist_and_do_not_read_fixtures(self):
        deferred = (
            "readable",
            "normalize_transcript",
            "normalize_stream",
        )

        def boom(*_args, **_kwargs):
            raise AssertionError("deferred callables must not read fixtures")

        with mock.patch("builtins.open", boom):
            for name in mf.BACKENDS:
                module = backends.build(name)
                for method in deferred:
                    getattr(module, method)()


if __name__ == "__main__":
    unittest.main()
