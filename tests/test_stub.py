"""U1: the stub claude writes its transcript where the runner will look and obeys its queue."""
import json
import os
import subprocess
import tempfile
import unittest

import _paths
from relay import contracts

STUB = os.path.join(_paths.STUB_DIR, "claude")
FIXTURES = os.path.join(_paths.FIXTURES_DIR, "transcripts")

RUNNER_FLAGS = [
    "--model", "sonnet", "--effort", "low", "--permission-mode", "dontAsk",
    "--allowedTools", "Read,Edit", "--disallowedTools", ",".join(contracts.DISALLOWED_TOOLS),
    "--output-format", "stream-json", "--verbose",
]


def write_entry(queue, n, fixture, exit_code=0, sleep=0, git_sh=None):
    entry_dir = os.path.join(queue, str(n))
    os.makedirs(entry_dir)
    with open(os.path.join(entry_dir, "entry.json"), "w") as handle:
        json.dump({"fixture": fixture, "exit": exit_code, "sleep": sleep}, handle)
    if git_sh:
        with open(os.path.join(entry_dir, "git.sh"), "w") as handle:
            handle.write(git_sh)


class StubClaude(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = os.path.join(self.tmp.name, "home")
        self.cwd = os.path.join(self.tmp.name, "repo")
        self.queue = os.path.join(self.tmp.name, "queue")
        for path in (self.home, self.cwd, self.queue):
            os.makedirs(path)
        self.env = dict(os.environ, HOME=self.home, RELAY_STUB_QUEUE=self.queue,
                        PATH=_paths.STUB_DIR + os.pathsep + os.environ.get("PATH", ""))
        self.env.pop("RELAY_STUB_SLEEP", None)
        self.env.pop("RELAY_STUB_CHILD", None)

    def tearDown(self):
        self.tmp.cleanup()

    def invoke(self, session_id):
        return subprocess.run(
            ["claude", "-p", "brief text", "--session-id", session_id] + RUNNER_FLAGS,
            cwd=self.cwd, env=self.env, capture_output=True, text=True, timeout=30,
        )

    def test_two_entry_queue_replays_fixtures_exit_codes_and_git_script(self):
        write_entry(self.queue, 1, os.path.join(FIXTURES, "success.jsonl"), exit_code=0)
        write_entry(self.queue, 2, os.path.join(FIXTURES, "blocked.jsonl"), exit_code=3,
                    git_sh="#!/bin/bash\necho ran > marker.txt\n")
        first = self.invoke("11111111-1111-4111-8111-111111111111")
        second = self.invoke("22222222-2222-4222-8222-222222222222")
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.assertEqual(second.returncode, 3, second.stdout + second.stderr)
        realcwd = os.path.realpath(self.cwd)
        for sid, fixture in (("11111111-1111-4111-8111-111111111111", "success.jsonl"),
                             ("22222222-2222-4222-8222-222222222222", "blocked.jsonl")):
            expected = contracts.transcript_path(self.home, realcwd, sid)
            self.assertTrue(os.path.exists(expected), "transcript missing at %s" % expected)
            with open(expected) as got, open(os.path.join(FIXTURES, fixture)) as want:
                self.assertEqual(got.read(), want.read())
        self.assertTrue(os.path.exists(os.path.join(self.cwd, "marker.txt")))
        third = self.invoke("33333333-3333-4333-8333-333333333333")
        self.assertEqual(third.returncode, 97, "a spent queue must fail loudly")

    def test_stdout_is_line_json(self):
        write_entry(self.queue, 1, os.path.join(FIXTURES, "success.jsonl"))
        proc = self.invoke("11111111-1111-4111-8111-111111111111")
        for line in proc.stdout.splitlines():
            json.loads(line)

    def test_slug_agrees_with_runner_on_a_symlinked_path(self):
        link = os.path.join(self.tmp.name, "link")
        os.symlink(self.cwd, link)
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
        """A grandchild inherits stdout, so the pipe stays open after the stub exits. U6 relies
        on this shape to prove its group kill; here we only check the child exists."""
        write_entry(self.queue, 1, os.path.join(FIXTURES, "success.jsonl"))
        env = dict(self.env, RELAY_STUB_CHILD="1")
        log_path = os.path.join(self.tmp.name, "stub.log")
        with open(log_path, "w") as log:
            proc = subprocess.Popen(
                ["claude", "-p", "x", "--session-id", "55555555-5555-4555-8555-555555555555"] + RUNNER_FLAGS,
                cwd=self.cwd, env=env, stdout=log, stderr=subprocess.STDOUT,
            )
            proc.wait(timeout=30)
        with open(log_path) as log:
            pids = [json.loads(l)["pid"] for l in log if "stub_child" in l]
        self.assertEqual(len(pids), 1)
        os.kill(pids[0], 0)
        os.kill(pids[0], 9)

if __name__ == "__main__":
    unittest.main()
