"""U1: the notifier.

Nothing here executes `osascript`. Every case either asserts on the argv the module would have
passed, or on `build` refusing to hand back a notifier at all. That is the whole reason `build`
returns None rather than a no op callable: a disabled notifier is provable by identity.
"""
import subprocess
import unittest

import _paths
from relay import notify


class Recorder:
    """Stands in for `subprocess.run` and keeps what it was called with."""

    def __init__(self, raises=None):
        self.calls = []
        self.raises = raises

    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        if self.raises is not None:
            raise self.raises
        return None


def present(_name):
    return "/usr/bin/osascript"


def absent(_name):
    return None


class Available(unittest.TestCase):
    def test_darwin_with_the_binary_is_available(self):
        self.assertTrue(notify.available(platform="darwin", which=present))

    def test_darwin_without_the_binary_is_not(self):
        self.assertFalse(notify.available(platform="darwin", which=absent))

    def test_another_platform_is_not_available_even_with_a_binary_on_the_path(self):
        for platform in ("linux", "win32"):
            self.assertFalse(notify.available(platform=platform, which=present), platform)


class Build(unittest.TestCase):
    def test_disabled_returns_none_even_on_a_capable_host(self):
        self.assertIsNone(notify.build(False, platform="darwin", which=present))

    def test_enabled_on_an_incapable_host_returns_none_rather_than_failing(self):
        self.assertIsNone(notify.build(True, platform="darwin", which=absent))
        self.assertIsNone(notify.build(True, platform="linux", which=present))

    def test_enabled_on_a_capable_host_returns_a_callable(self):
        self.assertTrue(callable(notify.build(True, platform="darwin", which=present)))

    def test_the_built_notifier_sends_through_the_injected_runner(self):
        recorder = Recorder()
        notifier = notify.build(True, platform="darwin", which=present, runner=recorder)
        notifier("Relay", "T-1 task")
        self.assertEqual(len(recorder.calls), 1)
        argv, _kwargs = recorder.calls[0]
        self.assertEqual(argv[0], notify.BINARY)
        self.assertEqual(argv[1], "-e")
        self.assertIn("T-1 task", argv[2])
        self.assertIn("Relay", argv[2])


class Script(unittest.TestCase):
    def test_the_script_is_the_documented_applescript_shape(self):
        self.assertEqual(notify.script_for("Relay", "run halted"),
                         'display notification "run halted" with title "Relay"')

    def test_a_double_quote_in_the_body_is_escaped_rather_than_closing_the_literal(self):
        script = notify.script_for("Relay", 'T-1 said "done"')
        self.assertIn('\\"done\\"', script)
        # Four unescaped quotes remain: the two literals' own delimiters.
        self.assertEqual(script.count('"') - script.count('\\"'), 4)

    def test_a_backslash_is_escaped_before_the_quotes_are(self):
        script = notify.script_for("Relay", 'a\\b"c')
        self.assertIn('a\\\\b\\"c', script)

    def test_the_title_is_escaped_too(self):
        self.assertIn('\\"R\\"', notify.script_for('"R"', "body"))


class Send(unittest.TestCase):
    def test_send_passes_an_argv_list_and_never_a_shell_string(self):
        recorder = Recorder()
        notify.send("Relay", "T-2 is now landed", runner=recorder)
        argv, kwargs = recorder.calls[0]
        self.assertIsInstance(argv, list)
        self.assertFalse(kwargs.get("shell", False))

    def test_send_discards_the_command_output(self):
        recorder = Recorder()
        notify.send("Relay", "body", runner=recorder)
        _argv, kwargs = recorder.calls[0]
        self.assertEqual(kwargs["stdout"], subprocess.DEVNULL)
        self.assertEqual(kwargs["stderr"], subprocess.DEVNULL)

    def test_an_oserror_from_the_runner_does_not_propagate(self):
        """A follower that died because a notification failed would be worse than a quiet one."""
        notify.send("Relay", "body", runner=Recorder(raises=OSError("no such binary")))


if __name__ == "__main__":
    unittest.main()
