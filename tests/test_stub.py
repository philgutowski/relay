"""Backends U12: the stub claude, codex, and grok binaries write evidence where the runner
will look and obey a shared queue, without launching a real CLI."""
import json
import os
import re
import subprocess
import tempfile
import types
import unittest

import _paths
from relay import backends, contracts

STUB = os.path.join(_paths.STUB_DIR, "claude")
FIXTURES = os.path.join(_paths.FIXTURES_DIR, "transcripts")
BACKEND_FIXTURES = os.path.join(_paths.FIXTURES_DIR, "backends")

RUNNER_FLAGS = [
    "--model", "sonnet", "--effort", "low", "--permission-mode", "dontAsk",
    "--allowedTools", "Read,Edit", "--disallowedTools", ",".join(contracts.DISALLOWED_TOOLS),
    "--output-format", "stream-json", "--verbose",
]


def write_entry(queue, n, fixture, exit_code=0, sleep=0, git_sh=None, stream=None):
    entry_dir = os.path.join(queue, str(n))
    os.makedirs(entry_dir)
    entry = {"fixture": fixture, "exit": exit_code, "sleep": sleep}
    if stream:
        entry["stream"] = stream
    with open(os.path.join(entry_dir, "entry.json"), "w") as handle:
        json.dump(entry, handle)
    if git_sh:
        with open(os.path.join(entry_dir, "git.sh"), "w") as handle:
            handle.write(git_sh)


class _StubTestCase(unittest.TestCase):
    """The tempdir, HOME, queue, and PATH scaffolding every stub test class needs, shared the
    way _stub.py shares the binaries' own queue-and-replay machinery."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = os.path.join(self.tmp.name, "home")
        self.repo = os.path.join(self.tmp.name, "repo")
        self.queue = os.path.join(self.tmp.name, "queue")
        for path in (self.home, self.repo, self.queue):
            os.makedirs(path)
        self.env = dict(os.environ, HOME=self.home, RELAY_STUB_QUEUE=self.queue,
                        PATH=_paths.STUB_DIR + os.pathsep + os.environ.get("PATH", ""))
        self.env.pop("RELAY_STUB_SLEEP", None)
        self.env.pop("RELAY_STUB_CHILD", None)

    def tearDown(self):
        self.tmp.cleanup()

    def assert_child_holds_pipe(self, argv):
        """A grandchild inherits stdout, so the pipe stays open after the stub exits. U6 relies
        on this shape to prove its group kill; here we only check the child exists."""
        env = dict(self.env, RELAY_STUB_CHILD="1")
        stub_log = os.path.join(self.tmp.name, "stub.log")
        with open(stub_log, "w") as log:
            proc = subprocess.Popen(argv, cwd=self.repo, env=env, stdout=log,
                                     stderr=subprocess.STDOUT)
            proc.wait(timeout=30)
        with open(stub_log) as log:
            pids = [json.loads(l)["pid"] for l in log if "stub_child" in l]
        self.assertEqual(len(pids), 1)
        os.kill(pids[0], 0)
        os.kill(pids[0], 9)


class StubClaude(_StubTestCase):
    def invoke(self, session_id):
        return subprocess.run(
            ["claude", "-p", "brief text", "--session-id", session_id] + RUNNER_FLAGS,
            cwd=self.repo, env=self.env, capture_output=True, text=True, timeout=30,
        )

    def test_two_entry_queue_replays_fixtures_exit_codes_and_git_script(self):
        write_entry(self.queue, 1, os.path.join(FIXTURES, "success.jsonl"), exit_code=0)
        write_entry(self.queue, 2, os.path.join(FIXTURES, "blocked.jsonl"), exit_code=3,
                    git_sh="#!/bin/bash\necho ran > marker.txt\n")
        first = self.invoke("11111111-1111-4111-8111-111111111111")
        second = self.invoke("22222222-2222-4222-8222-222222222222")
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.assertEqual(second.returncode, 3, second.stdout + second.stderr)
        realcwd = os.path.realpath(self.repo)
        for sid, fixture in (("11111111-1111-4111-8111-111111111111", "success.jsonl"),
                             ("22222222-2222-4222-8222-222222222222", "blocked.jsonl")):
            expected = contracts.transcript_path(self.home, realcwd, sid)
            self.assertTrue(os.path.exists(expected), "transcript missing at %s" % expected)
            with open(expected) as got, open(os.path.join(FIXTURES, fixture)) as want:
                self.assertEqual(got.read(), want.read())
        self.assertTrue(os.path.exists(os.path.join(self.repo, "marker.txt")))
        third = self.invoke("33333333-3333-4333-8333-333333333333")
        self.assertEqual(third.returncode, 97, "a spent queue must fail loudly")

    def test_stdout_is_line_json(self):
        write_entry(self.queue, 1, os.path.join(FIXTURES, "success.jsonl"))
        proc = self.invoke("11111111-1111-4111-8111-111111111111")
        for line in proc.stdout.splitlines():
            json.loads(line)

    def test_slug_agrees_with_runner_on_a_symlinked_path(self):
        link = os.path.join(self.tmp.name, "link")
        os.symlink(self.repo, link)
        write_entry(self.queue, 1, os.path.join(FIXTURES, "success.jsonl"))
        proc = subprocess.run(
            ["claude", "-p", "x", "--session-id", "44444444-4444-4444-8444-444444444444"] + RUNNER_FLAGS,
            cwd=link, env=self.env, capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        written = [json.loads(l)["path"] for l in proc.stdout.splitlines() if "stub_transcript" in l][0]
        predicted = contracts.transcript_path(
            self.home, os.path.realpath(link), "44444444-4444-4444-8444-444444444444")
        self.assertEqual(written, predicted)

    def test_stub_child_env_spawns_a_child_that_holds_the_pipe(self):
        write_entry(self.queue, 1, os.path.join(FIXTURES, "success.jsonl"))
        self.assert_child_holds_pipe(
            ["claude", "-p", "x", "--session-id", "55555555-5555-4555-8555-555555555555"] + RUNNER_FLAGS)


class StubCodex(_StubTestCase):
    def setUp(self):
        super().setUp()
        self.mod = backends.build("codex")
        self.pins = contracts.BACKEND_PINS["codex"]
        self.fixtures = os.path.join(BACKEND_FIXTURES, "codex")

    def build_args(self, log_path, allowed=(), disallowed=()):
        task = types.SimpleNamespace(model="gpt-5-codex", effort="medium")
        manifest = types.SimpleNamespace(project=types.SimpleNamespace(repo=self.repo))
        return self.mod.build_args(manifest, task, "brief text", "unused-session",
                                    allowed=allowed, disallowed=disallowed,
                                    log_path=log_path, repo=self.repo)

    def test_fixture_lands_where_evidence_sources_predicts(self):
        fixture = os.path.join(self.fixtures, "last-message-complete.txt")
        write_entry(self.queue, 1, fixture)
        log_path = os.path.join(self.tmp.name, "T-1.stdout.log")
        args = self.build_args(log_path)
        proc = subprocess.run(args, cwd=self.repo, env=self.env, capture_output=True, text=True,
                               timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        last_message, _log = self.mod.evidence_sources(self.home, self.repo, "unused-session",
                                                         log_path=log_path)
        self.assertTrue(os.path.exists(last_message))
        with open(last_message) as got, open(fixture) as want:
            self.assertEqual(got.read(), want.read())

    def test_stream_entry_echoes_tool_events_around_the_non_json_line(self):
        """R8: a real Codex run interleaves one non-JSON line
        ("Reading additional input from stdin...") into its own JSON stdout. The stub's
        stream echo must carry both kinds through unchanged, the same way a real run does."""
        stream = os.path.join(self.tmp.name, "stream.jsonl")
        with open(stream, "w") as handle:
            handle.write('{"type": "item.completed", "item": {"type": "agent_message"}}\n')
            handle.write("Reading additional input from stdin...\n")
        write_entry(self.queue, 1, os.path.join(self.fixtures, "last-message-complete.txt"),
                    stream=stream)
        log_path = os.path.join(self.tmp.name, "T-4.stdout.log")
        proc = subprocess.run(self.build_args(log_path), cwd=self.repo, env=self.env,
                               capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn('"type": "item.completed"', proc.stdout)
        self.assertIn("Reading additional input from stdin...", proc.stdout)

    def test_version_output_parses_to_the_pinned_version(self):
        proc = subprocess.run(["codex", "--version"], env=self.env, capture_output=True,
                               text=True, timeout=10)
        self.assertEqual(self.mod.parse_version(proc.stdout), self.pins["version_tested"])

    def test_plugin_list_output_matches_the_pinned_pattern(self):
        proc = subprocess.run(["codex", "plugin", "list"], env=self.env, capture_output=True,
                               text=True, timeout=10)
        match = re.search(self.pins["plugin_version_pattern"], proc.stdout)
        self.assertIsNotNone(match, proc.stdout)
        self.assertEqual(match.group("version"), self.pins["plugin_version"])

    def test_build_args_grammar_accepted_a_renamed_flag_rejected(self):
        fixture = os.path.join(self.fixtures, "last-message-complete.txt")
        write_entry(self.queue, 1, fixture)
        log_path = os.path.join(self.tmp.name, "T-2.stdout.log")
        args = self.build_args(log_path)
        good = subprocess.run(args, cwd=self.repo, env=self.env, capture_output=True, text=True,
                               timeout=30)
        self.assertEqual(good.returncode, 0, good.stdout + good.stderr)

        bad = list(args)
        bad[bad.index("--sandbox")] = "--bogus-flag"
        result = subprocess.run(bad, cwd=self.repo, env=self.env, capture_output=True, text=True,
                                 timeout=30)
        self.assertNotEqual(result.returncode, 0)

    def test_dropping_either_config_override_token_is_rejected(self):
        """Issue #51. The allow list alone would accept a shorter argv, so the grammar has to ask
        the second question too: was every flag build_args always emits actually there. A dropped
        `-c` pair is the regression that silently re-fences the sandbox, and the blocked halt it
        produces arrives a whole task later than this test does."""
        fixture = os.path.join(self.fixtures, "last-message-complete.txt")
        args = self.build_args(os.path.join(self.tmp.name, "T-5.stdout.log"))
        override = args.index("-c")

        write_entry(self.queue, 1, fixture)
        good = subprocess.run(args, cwd=self.repo, env=self.env, capture_output=True, text=True,
                               timeout=30)
        self.assertEqual(good.returncode, 0, good.stdout + good.stderr)

        without_override = args[:override] + args[override + 2:]
        result = subprocess.run(without_override, cwd=self.repo, env=self.env,
                                 capture_output=True, text=True, timeout=30)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("-c", result.stderr)

        without_strict = [arg for arg in args if arg != "--strict-config"]
        result = subprocess.run(without_strict, cwd=self.repo, env=self.env,
                                 capture_output=True, text=True, timeout=30)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--strict-config", result.stderr)

    def test_stub_child_env_spawns_a_child_that_holds_the_pipe(self):
        fixture = os.path.join(self.fixtures, "last-message-complete.txt")
        write_entry(self.queue, 1, fixture)
        log_path = os.path.join(self.tmp.name, "T-3.stdout.log")
        self.assert_child_holds_pipe(self.build_args(log_path))


class StubGrok(_StubTestCase):
    def setUp(self):
        super().setUp()
        self.mod = backends.build("grok")
        self.pins = contracts.BACKEND_PINS["grok"]
        self.fixtures = os.path.join(BACKEND_FIXTURES, "grok")

    def build_args(self, session_id, allowed=(), disallowed=()):
        task = types.SimpleNamespace(model="grok-4", effort="low")
        manifest = types.SimpleNamespace(project=types.SimpleNamespace(repo=self.repo))
        return self.mod.build_args(manifest, task, "brief text", session_id,
                                    allowed=allowed, disallowed=disallowed)

    def test_fixture_lands_where_evidence_sources_predicts(self):
        fixture = os.path.join(self.fixtures, "session-transcript-complete.jsonl")
        write_entry(self.queue, 1, fixture)
        args = self.build_args("66666666-6666-4666-8666-666666666666")
        proc = subprocess.run(args, cwd=self.repo, env=self.env, capture_output=True, text=True,
                               timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        realrepo = os.path.realpath(self.repo)
        (predicted,) = self.mod.evidence_sources(self.home, realrepo,
                                                  "66666666-6666-4666-8666-666666666666")
        self.assertTrue(os.path.exists(predicted), predicted)
        with open(predicted) as got, open(fixture) as want:
            self.assertEqual(got.read(), want.read())

    def test_fixture_lands_where_evidence_sources_predicts_with_a_cwd_needing_url_encoding(self):
        # A real directory (not a symlink, which os.path.realpath would resolve away) whose
        # name needs percent-encoding, so the stub and evidence_sources must agree on the
        # quoted form rather than on a plain path both happen to accept unencoded.
        cwd = os.path.join(self.tmp.name, "repo with spaces")
        os.makedirs(cwd)
        fixture = os.path.join(self.fixtures, "session-transcript-complete.jsonl")
        write_entry(self.queue, 1, fixture)
        args = self.build_args("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
        proc = subprocess.run(args, cwd=cwd, env=self.env, capture_output=True, text=True,
                               timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        realpath = os.path.realpath(cwd)
        (predicted,) = self.mod.evidence_sources(self.home, realpath,
                                                   "cccccccc-cccc-4ccc-8ccc-cccccccccccc")
        self.assertIn("%20", predicted)
        self.assertTrue(os.path.exists(predicted), predicted)
        with open(predicted) as got, open(fixture) as want:
            self.assertEqual(got.read(), want.read())

    def test_stream_entry_echoes_token_events(self):
        """R9: a real Grok run emits one JSON object per token on stdout. The stub's stream
        echo must carry that shape through unchanged so tail's reassembly has something to
        decode; this only proves the echo, not tail's own reassembly (covered elsewhere)."""
        stream = os.path.join(self.tmp.name, "stream.jsonl")
        with open(stream, "w") as handle:
            handle.write('{"type": "text", "data": "hel"}\n')
            handle.write('{"type": "text", "data": "lo"}\n')
            handle.write('{"type": "tool_call", "toolName": "read_file"}\n')
        write_entry(self.queue, 1, os.path.join(self.fixtures, "session-transcript-complete.jsonl"),
                    stream=stream)
        args = self.build_args("dddddddd-dddd-4ddd-8ddd-dddddddddddd")
        proc = subprocess.run(args, cwd=self.repo, env=self.env, capture_output=True, text=True,
                               timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn('{"type": "text", "data": "hel"}', proc.stdout)
        self.assertIn('{"type": "tool_call", "toolName": "read_file"}', proc.stdout)

    def test_version_output_parses_to_the_pinned_version(self):
        proc = subprocess.run(["grok", "--version"], env=self.env, capture_output=True,
                               text=True, timeout=10)
        self.assertEqual(self.mod.parse_version(proc.stdout), self.pins["version_tested"])

    def test_plugin_list_output_matches_the_pinned_pattern(self):
        proc = subprocess.run(["grok", "plugin", "list", "--json"], env=self.env,
                               capture_output=True, text=True, timeout=10)
        match = re.search(self.pins["plugin_version_pattern"], proc.stdout)
        self.assertIsNotNone(match, proc.stdout)
        self.assertEqual(match.group("version"), self.pins["plugin_version"])

    def test_build_args_grammar_with_allow_deny_pairs_accepted_a_renamed_flag_rejected(self):
        fixture = os.path.join(self.fixtures, "session-transcript-complete.jsonl")
        write_entry(self.queue, 1, fixture)
        args = self.build_args("77777777-7777-4777-8777-777777777777",
                                allowed=("Read", "Edit"), disallowed=("Bash(rm -rf*)",))
        good = subprocess.run(args, cwd=self.repo, env=self.env, capture_output=True, text=True,
                               timeout=30)
        self.assertEqual(good.returncode, 0, good.stdout + good.stderr)

        bad = list(args)
        bad[bad.index("--effort")] = "--bogus-flag"
        result = subprocess.run(bad, cwd=self.repo, env=self.env, capture_output=True, text=True,
                                 timeout=30)
        self.assertNotEqual(result.returncode, 0)

    def test_stub_child_env_spawns_a_child_that_holds_the_pipe(self):
        fixture = os.path.join(self.fixtures, "session-transcript-complete.jsonl")
        write_entry(self.queue, 1, fixture)
        self.assert_child_holds_pipe(self.build_args("88888888-8888-4888-8888-888888888888"))


class CrossBackendQueue(_StubTestCase):
    """R11: one queue, drained by a Task on one backend and a Closeout on another, stays in
    strict numeric order. This is what makes the queue safe for a manifest that mixes
    backends within one run."""

    def test_claude_then_codex_consume_entries_in_order_and_a_third_backend_sees_it_spent(self):
        claude_fixture = os.path.join(FIXTURES, "success.jsonl")
        codex_fixture = os.path.join(BACKEND_FIXTURES, "codex", "last-message-complete.txt")
        write_entry(self.queue, 1, claude_fixture)
        write_entry(self.queue, 2, codex_fixture)

        claude_session = "99999999-9999-4999-8999-999999999999"
        claude_proc = subprocess.run(
            ["claude", "-p", "brief", "--session-id", claude_session] + RUNNER_FLAGS,
            cwd=self.repo, env=self.env, capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(claude_proc.returncode, 0, claude_proc.stdout + claude_proc.stderr)
        claude_path = contracts.transcript_path(self.home, os.path.realpath(self.repo),
                                                 claude_session)
        with open(claude_path) as got, open(claude_fixture) as want:
            self.assertEqual(got.read(), want.read(), "claude must consume entry 1, not entry 2")

        codex_mod = backends.build("codex")
        task = types.SimpleNamespace(model="gpt-5-codex", effort="medium")
        manifest = types.SimpleNamespace(project=types.SimpleNamespace(repo=self.repo))
        log_path = os.path.join(self.tmp.name, "T-2.stdout.log")
        codex_args = codex_mod.build_args(manifest, task, "brief", "unused-session",
                                           allowed=(), disallowed=(), log_path=log_path,
                                           repo=self.repo)
        codex_proc = subprocess.run(codex_args, cwd=self.repo, env=self.env,
                                     capture_output=True, text=True, timeout=30)
        self.assertEqual(codex_proc.returncode, 0, codex_proc.stdout + codex_proc.stderr)
        last_message, _log = codex_mod.evidence_sources(self.home, self.repo, "unused-session",
                                                          log_path=log_path)
        with open(last_message) as got, open(codex_fixture) as want:
            self.assertEqual(got.read(), want.read(), "codex must consume entry 2, not entry 1")

        grok_mod = backends.build("grok")
        grok_task = types.SimpleNamespace(model="grok-4", effort="low")
        grok_args = grok_mod.build_args(manifest, grok_task, "brief",
                                         "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                                         allowed=(), disallowed=())
        third = subprocess.run(grok_args, cwd=self.repo, env=self.env, capture_output=True,
                                text=True, timeout=30)
        self.assertEqual(third.returncode, 97, "a spent queue must fail loudly for any backend")


if __name__ == "__main__":
    unittest.main()
