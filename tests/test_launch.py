"""U6: launching one claude process, bounding it, and killing its whole group on timeout.

Every test runs against tests/stub-claude with a temp HOME and a PATH that finds the stub first.
Nothing here launches a real claude.
"""
import json
import os
import subprocess
import tempfile
import time
import unittest

import _paths
import _repo
from relay import contracts, launch, manifest as mf

FIXTURE = os.path.join(_paths.FIXTURES_DIR, "manifests", "complete.toml")
TRANSCRIPTS = os.path.join(_paths.FIXTURES_DIR, "transcripts")

BRIEF = "Relay task T-1\n\nDo the one task.\n"


def alive(pid):
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    return True


def write_entry(queue, n, fixture, exit_code=0, sleep=0, git_sh=None):
    entry_dir = os.path.join(queue, str(n))
    os.makedirs(entry_dir)
    with open(os.path.join(entry_dir, "entry.json"), "w") as handle:
        json.dump({"fixture": fixture, "exit": exit_code, "sleep": sleep}, handle)
    if git_sh:
        with open(os.path.join(entry_dir, "git.sh"), "w") as handle:
            handle.write(git_sh)


class LaunchCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = _repo.make_repo(self.tmp.name)
        self.home = os.path.join(self.tmp.name, "home")
        self.queue = os.path.join(self.tmp.name, "queue")
        os.makedirs(self.home)
        os.makedirs(self.queue)
        with open(FIXTURE) as handle:
            self.toml = handle.read().replace("__REPO__", self.repo)
        self.manifest = self.load()
        self.log = os.path.join(self.tmp.name, "T-1.stdout.log")
        self.base_env = dict(
            os.environ,
            HOME=self.home,
            RELAY_STUB_QUEUE=self.queue,
            PATH=_paths.STUB_DIR + os.pathsep + os.environ.get("PATH", ""),
        )
        for name in ("RELAY_STUB_SLEEP", "RELAY_STUB_CHILD"):
            self.base_env.pop(name, None)

    def tearDown(self):
        self.tmp.cleanup()

    def load(self, text=None, name="manifest.toml"):
        path = os.path.join(self.tmp.name, name)
        with open(path, "w") as handle:
            handle.write(text if text is not None else self.toml)
        return mf.load(path)

    def go(self, timeout_seconds=30, **kwargs):
        kwargs.setdefault("base_env", self.base_env)
        kwargs.setdefault("home", self.home)
        kwargs.setdefault("sigkill_grace_seconds", 2)
        kwargs.setdefault("stream", lambda line: None)
        return launch.launch(self.manifest, self.manifest.tasks[0], BRIEF, self.log,
                             timeout_seconds, **kwargs)


class Slug(unittest.TestCase):
    def test_the_slug_replaces_every_character_outside_the_alphanumeric_set(self):
        path = "/Users/p.g/Documents/PhilAI/support_workbench"
        self.assertEqual(contracts.slug_for(path),
                         "-Users-p-g-Documents-PhilAI-support-workbench")

    def test_the_transcript_path_is_the_slug_directory_and_the_session_id(self):
        session = "11111111-1111-4111-8111-111111111111"
        path = contracts.transcript_path("/home/x", "/repo/a.b", session)
        self.assertEqual(path, "/home/x/.claude/projects/-repo-a-b/%s.jsonl" % session)

    # The stub and the runner agreeing on the slug is proved end to end by U1's
    # test_stub.test_slug_agrees_with_runner_on_a_symlinked_path, which checks the path the stub
    # actually wrote rather than re-deriving the rule here.


class Arguments(LaunchCase):
    def args(self):
        return launch.build_args(self.manifest, self.manifest.tasks[0], BRIEF, "sid")

    def test_the_argument_list_carries_the_flags_the_cli_contract_names(self):
        args = self.args()
        self.assertEqual(args[0], "claude")
        self.assertEqual(args[1], "-p")
        self.assertEqual(args[2], BRIEF)
        for flag in contracts.CLI_FLAGS:
            self.assertIn(flag, args)
        self.assertEqual(args[args.index("--permission-mode") + 1], contracts.PERMISSION_MODE)
        self.assertEqual(args[args.index("--output-format") + 1], contracts.OUTPUT_FORMAT)
        self.assertEqual(args[args.index("--model") + 1], "opus")
        self.assertEqual(args[args.index("--effort") + 1], "high")
        self.assertEqual(args[args.index("--session-id") + 1], "sid")

    def test_the_argument_list_never_names_bypass_permissions(self):
        self.assertNotIn(contracts.FORBIDDEN_PERMISSION_MODE, " ".join(self.args()))

    def test_the_disallow_list_carries_every_variant_from_contracts(self):
        disallowed = self.args()[self.args().index("--disallowedTools") + 1]
        for pattern in contracts.DISALLOWED_TOOLS:
            self.assertIn(pattern, disallowed)

    def test_the_allowlist_comes_from_the_manifest(self):
        allowed = self.args()[self.args().index("--allowedTools") + 1]
        for tool in self.manifest.permissions.allowed:
            self.assertIn(tool, allowed)


class ChildEnvironment(LaunchCase):
    def test_the_tracker_token_and_the_nested_session_markers_are_scrubbed(self):
        parent = dict(self.base_env, JIRA_API_TOKEN="secret", JIRA_EMAIL="e@x.invalid",
                      CLAUDECODE="1", CLAUDE_CODE_ENTRYPOINT="cli", CLAUDE_CODE_SSE_PORT="1234")
        env = launch.child_env(self.manifest, parent)
        for name in ("JIRA_API_TOKEN", "JIRA_EMAIL", "CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT",
                     "CLAUDE_CODE_SSE_PORT"):
            self.assertNotIn(name, env, "%s reached the child process" % name)
        self.assertEqual(env["HOME"], self.home)
        self.assertIn("PATH", env)

    def test_the_scrub_follows_the_manifest_when_it_names_other_variables(self):
        manifest = self.load(self.toml.replace('done_statuses = ["done"]',
                                               'token_env = "MY_TOKEN"\nemail_env = "MY_EMAIL"\ndone_statuses = ["done"]'),
                             name="other-env.toml")
        env = launch.child_env(manifest, dict(self.base_env, MY_TOKEN="s", MY_EMAIL="e"))
        self.assertNotIn("MY_TOKEN", env)
        self.assertNotIn("MY_EMAIL", env)


class PopenContract(LaunchCase):
    def test_the_child_starts_detached_in_the_repo_with_no_inherited_stdin(self):
        seen = {}

        class Recording:
            def __init__(inner, args, **kwargs):
                seen["args"] = args
                seen["kwargs"] = kwargs
                inner.proc = subprocess.Popen(args, **kwargs)

            def __getattr__(inner, name):
                return getattr(inner.proc, name)

        write_entry(self.queue, 1, os.path.join(TRANSCRIPTS, "success.jsonl"))
        self.go(popen=Recording)
        self.assertEqual(seen["kwargs"]["stdin"], subprocess.DEVNULL)
        self.assertTrue(seen["kwargs"]["start_new_session"])
        self.assertEqual(seen["kwargs"]["cwd"], os.path.realpath(self.repo))


class SuccessfulRun(LaunchCase):
    def test_the_transcript_lands_at_the_derived_path_and_the_log_is_written(self):
        write_entry(self.queue, 1, os.path.join(TRANSCRIPTS, "success.jsonl"))
        result = self.go()
        self.assertEqual(result.exit_code, 0)
        self.assertFalse(result.timed_out)
        self.assertTrue(result.transcript_present, result.transcript_path)
        self.assertEqual(
            result.transcript_path,
            contracts.transcript_path(self.home, os.path.realpath(self.repo), result.session_id))
        with open(self.log) as handle:
            self.assertIn("stub_done", handle.read())

    def test_a_nonzero_exit_is_returned_rather_than_raised(self):
        write_entry(self.queue, 1, os.path.join(TRANSCRIPTS, "blocked.jsonl"), exit_code=3)
        result = self.go()
        self.assertEqual(result.exit_code, 3)
        self.assertFalse(result.timed_out)

    def test_both_wall_and_active_seconds_are_recorded(self):
        write_entry(self.queue, 1, os.path.join(TRANSCRIPTS, "success.jsonl"))
        result = self.go()
        self.assertGreater(result.wall_seconds, 0)
        self.assertGreater(result.active_seconds, 0)

    def test_a_transcript_written_under_another_slug_is_still_found_by_session_id(self):
        session = "33333333-3333-4333-8333-333333333333"
        other = os.path.join(self.home, ".claude", "projects", "-some-other-slug")
        os.makedirs(other)
        with open(os.path.join(other, session + ".jsonl"), "w") as handle:
            handle.write('{"type": "assistant", "message": {"content": []}}\n')
        result = self.go(session_id=session)
        self.assertTrue(result.transcript_present)
        self.assertTrue(result.transcript_path.endswith(session + ".jsonl"))
        self.assertIn("-some-other-slug", result.transcript_path)


class Timeout(LaunchCase):
    def test_a_run_past_its_deadline_is_killed_and_reported_as_timed_out(self):
        write_entry(self.queue, 1, os.path.join(TRANSCRIPTS, "success.jsonl"), sleep=20)
        result = self.go(timeout_seconds=1)
        self.assertTrue(result.timed_out)
        self.assertTrue(result.killed_group)
        self.assertLess(result.active_seconds, 15)

    def test_the_whole_process_group_dies_so_no_grandchild_outlives_the_task(self):
        write_entry(self.queue, 1, os.path.join(TRANSCRIPTS, "success.jsonl"), sleep=20)
        result = self.go(timeout_seconds=1,
                         base_env=dict(self.base_env, RELAY_STUB_CHILD="1"))
        self.assertTrue(result.timed_out)
        child_pid = None
        with open(self.log) as handle:
            for line in handle:
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if event.get("subtype") == "stub_child":
                    child_pid = event["pid"]
        self.assertIsNotNone(child_pid, "the stub never reported a child pid")
        deadline = time.monotonic() + 5
        while alive(child_pid) and time.monotonic() < deadline:
            time.sleep(0.1)
        self.assertFalse(alive(child_pid), "the grandchild outlived the task process")


class Heartbeat(LaunchCase):
    def test_the_lease_heartbeat_keeps_ticking_while_a_quiet_process_runs(self):
        ticks = []
        write_entry(self.queue, 1, os.path.join(TRANSCRIPTS, "success.jsonl"), sleep=3)
        self.go(heartbeat=lambda: ticks.append(1), heartbeat_interval=1)
        self.assertGreaterEqual(len(ticks), 2, "the heartbeat did not survive a quiet process")

    def test_a_heartbeat_that_reports_a_lost_lease_stops_the_run(self):
        write_entry(self.queue, 1, os.path.join(TRANSCRIPTS, "success.jsonl"), sleep=20)
        result = self.go(timeout_seconds=30, heartbeat=lambda: False, heartbeat_interval=1)
        self.assertTrue(result.lease_lost)
        self.assertTrue(result.killed_group)


if __name__ == "__main__":
    unittest.main()


class LaunchFailure(LaunchCase):
    """A binary that is missing or unexecutable must be a result, not an exception: the reader
    thread, the heartbeat, and the signal handlers are all installed after Popen, so a raise
    there skips every piece of cleanup (review finding #3)."""

    def test_a_missing_binary_returns_a_launch_error_rather_than_raising(self):
        env = dict(self.base_env, PATH=os.path.join(self.tmp.name, "empty"))
        os.makedirs(os.path.join(self.tmp.name, "empty"), exist_ok=True)
        result = self.go(base_env=env)
        self.assertIsNotNone(result.launch_error)
        self.assertIn("claude", result.launch_error)
        self.assertFalse(result.timed_out)
        self.assertIsNone(result.exit_code)


class TimeoutWithASurvivingGrandchild(LaunchCase):
    """The deadline used to be conjoined with the direct child still running, so once the stub
    exited the timeout could never fire while a grandchild held the inherited stdout pipe open
    (review finding #15)."""

    def test_a_fast_exit_with_a_grandchild_holding_the_pipe_still_times_out(self):
        write_entry(self.queue, 1, os.path.join(TRANSCRIPTS, "success.jsonl"))
        result = self.go(timeout_seconds=2,
                         base_env=dict(self.base_env, RELAY_STUB_CHILD="1"))
        self.assertTrue(result.timed_out, "the deadline never fired; the loop would have hung")
        self.assertTrue(result.killed_group)
        self.assertLess(result.active_seconds, 20)


class CloseoutDisallowList(LaunchCase):
    def test_the_closeout_extra_disallow_entries_reach_the_argument_list(self):
        args = launch.build_args(self.manifest, self.manifest.tasks[0], BRIEF, "sid",
                                 disallowed=contracts.CLOSEOUT_DISALLOWED_EXTRA)
        disallowed = args[args.index("--disallowedTools") + 1]
        for pattern in contracts.CLOSEOUT_DISALLOWED_EXTRA:
            self.assertIn(pattern, disallowed)
        for pattern in contracts.DISALLOWED_TOOLS:
            self.assertIn(pattern, disallowed)

    def test_a_task_launch_keeps_the_ordinary_list_so_pr_mode_can_still_push(self):
        args = launch.build_args(self.manifest, self.manifest.tasks[0], BRIEF, "sid")
        disallowed = args[args.index("--disallowedTools") + 1]
        self.assertNotIn("Bash(git push*)", disallowed.split(","))


class _FakeCompletedProcess:
    def __init__(self, stdout="", returncode=0):
        self.stdout = stdout
        self.returncode = returncode


class CliVersion(unittest.TestCase):
    """U6's version probe: a single blocking `claude --version` call, read once per run so a
    drift from contracts.CLI_VERSION_TESTED is visible rather than silently invisible."""

    def test_a_clean_version_line_is_parsed_to_its_leading_token(self):
        fake = lambda *a, **k: _FakeCompletedProcess("2.1.247 (Claude Code)\n", 0)
        self.assertEqual(launch.cli_version({}, run=fake), "2.1.247")

    def test_a_nonzero_exit_returns_none_rather_than_a_stale_value(self):
        fake = lambda *a, **k: _FakeCompletedProcess("2.1.247 (Claude Code)\n", 1)
        self.assertIsNone(launch.cli_version({}, run=fake))

    def test_a_missing_binary_returns_none_rather_than_raising(self):
        def fake(*a, **k):
            raise FileNotFoundError("no such file: claude")
        self.assertIsNone(launch.cli_version({}, run=fake))

    def test_a_timeout_returns_none_rather_than_raising(self):
        def fake(*a, **k):
            raise subprocess.TimeoutExpired(cmd=["claude", "--version"], timeout=10)
        self.assertIsNone(launch.cli_version({}, run=fake))

    def test_empty_stdout_returns_none(self):
        fake = lambda *a, **k: _FakeCompletedProcess("", 0)
        self.assertIsNone(launch.cli_version({}, run=fake))

    def test_a_leading_non_version_token_returns_none_rather_than_the_wrong_word(self):
        # A banner or update notice ahead of the real version line must not be mistaken for one.
        fake = lambda *a, **k: _FakeCompletedProcess(
            "Update available: run claude update\n2.1.247 (Claude Code)\n", 0)
        self.assertIsNone(launch.cli_version({}, run=fake))
