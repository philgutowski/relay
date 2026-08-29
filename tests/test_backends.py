"""Backends U4: one package answers every seam question per backend.

U1 is dispatch and the capability record. U2 is the shared callable surface.
Construction must work on a machine that has only claude.
"""
import os
import shutil
import subprocess
import unittest
from unittest import mock

import _paths  # noqa: F401
from relay import contracts, manifest as mf


PLACEHOLDERS = ("", "TODO", "TBD")


class Dispatch(unittest.TestCase):
    def test_build_returns_the_named_module(self):
        from relay import backends

        for name in mf.BACKENDS:
            module = backends.build(name)
            self.assertEqual(module.__name__.rsplit(".", 1)[-1], name)

    def test_unknown_name_raises_configuration_error_naming_the_set(self):
        from relay import backends

        with self.assertRaises(backends.ConfigurationError) as raised:
            backends.build("unknown")
        message = str(raised.exception)
        self.assertIn("claude", message)
        self.assertIn("codex", message)
        self.assertIn("grok", message)

    def test_build_performs_no_subprocess_and_touches_no_filesystem(self):
        from relay import backends

        def boom(*_args, **_kwargs):
            raise AssertionError("build must not probe the machine")

        with mock.patch.object(subprocess, "run", boom), \
                mock.patch.object(os.path, "exists", boom), \
                mock.patch.object(shutil, "which", boom):
            for name in mf.BACKENDS:
                backends.build(name)

    def test_the_three_closed_sets_are_equal(self):
        from relay import backends

        self.assertEqual(set(mf.BACKENDS), set(contracts.BACKEND_PINS))
        self.assertEqual(set(mf.BACKENDS), set(backends.BACKENDS))


class CapabilityRecord(unittest.TestCase):
    def test_every_record_is_a_complete_copy_of_the_pins(self):
        from relay import backends

        fields = {item.name for item in backends.Capability.__dataclass_fields__.values()}
        for name in mf.BACKENDS:
            pins = contracts.BACKEND_PINS[name]
            self.assertEqual(set(pins), fields, name)
            record = backends.build(name).CAPABILITY
            rebuilt = backends.Capability(**pins)
            self.assertEqual(record, rebuilt, name)
            for field, value in pins.items():
                if isinstance(value, str):
                    self.assertNotIn(value, PLACEHOLDERS, "%s.%s" % (name, field))

    def test_enforces_at_launch_is_the_demonstrated_bit(self):
        from relay import backends

        self.assertTrue(backends.build("claude").CAPABILITY.enforces_at_launch)
        self.assertFalse(backends.build("codex").CAPABILITY.enforces_at_launch)
        self.assertTrue(backends.build("grok").CAPABILITY.enforces_at_launch)

    def test_credential_list_is_prefixes_only(self):
        from relay import backends

        self.assertFalse(hasattr(backends.Capability, "credential_variables"))
        for name in mf.BACKENDS:
            prefixes = backends.build(name).CAPABILITY.credential_prefixes
            self.assertTrue(prefixes, name)

    def test_forbidden_spellings_are_non_empty_and_include_the_u1_findings(self):
        from relay import backends

        grok = backends.build("grok").CAPABILITY.forbidden_permission_modes
        self.assertTrue(grok)
        self.assertIn("dontAsk", grok)
        codex = backends.build("codex").CAPABILITY.forbidden_permission_modes
        self.assertIn("--dangerously-bypass-approvals-and-sandbox", codex)
        self.assertTrue(backends.build("claude").CAPABILITY.forbidden_permission_modes)

    def test_extra_writable_dirs_is_uniform_and_codex_keeps_the_git_token(self):
        from relay import backends

        self.assertEqual(backends.build("claude").CAPABILITY.extra_writable_dirs, ())
        self.assertEqual(backends.build("grok").CAPABILITY.extra_writable_dirs, ())
        self.assertEqual(backends.build("codex").CAPABILITY.extra_writable_dirs, ("<repo>/.git",))

    def test_codex_allow_and_deny_flags_may_be_none(self):
        from relay import backends

        cap = backends.build("codex").CAPABILITY
        self.assertIsNone(cap.allow_flag)
        self.assertIsNone(cap.deny_flag)


if __name__ == "__main__":
    unittest.main()
