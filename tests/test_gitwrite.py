"""U8: the runner's mutating git calls and the local merge tail.

Every test builds a real temp repo with a bare origin through tests/_repo.py. Nothing here
launches claude, reaches a network, or shells out to `gh`; the PR mode helpers take an injected
run callable and an injected clock.
"""
import os
import subprocess
import tempfile
import time
import unittest

import _paths
import _repo
from _fakes import FakeRun, RecordingOps
from relay import contracts, gitread, gitwrite


def commit_on_branch(repo, branch, files, message, base=None):
    """Create or switch to `branch`, write files, commit, and return the new head sha."""
    if gitread.branch_exists(repo, branch):
        _repo.git(repo, "checkout", "-q", branch)
    else:
        args = ["checkout", "-q", "-b", branch]
        if base:
            args.append(base)
        _repo.git(repo, *args)
    for rel, text in files.items():
        path = os.path.join(repo, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as handle:
            handle.write(text)
    _repo.git(repo, "add", "-A")
    _repo.git(repo, "commit", "-q", "-m", message)
    return gitread.rev_parse(repo, "HEAD")


class TailBase(unittest.TestCase):
    task_id = "T-1"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = _repo.make_repo(self.tmp.name, pre_push=getattr(self, "pre_push", None))
        self.branch = gitwrite.task_branch_for(self.task_id)
        self.baseline = gitread.rev_parse(self.repo, "origin/main")
        self.gate_log = os.path.join(self.tmp.name, "gate-%s.log" % self.task_id)
        self.ops = RecordingOps()

    def tearDown(self):
        self.tmp.cleanup()

    def make_task_commit(self, files=None, message="task work"):
        return commit_on_branch(
            self.repo, self.branch, files or {"src/feature.py": "value = 1\n"}, message, base="main"
        )

    def run_tail(self, gate=("true",), **kwargs):
        return gitwrite.local_merge_tail(
            self.repo, self.task_id, "main", self.baseline, list(gate), self.gate_log,
            ops=self.ops, **kwargs
        )


class PassingTail(TailBase):
    def test_merge_and_push_land_the_commit_on_the_remote_default(self):
        head = self.make_task_commit()
        result = self.run_tail()
        self.assertTrue(result.ok, result.evidence)
        self.assertIsNone(result.halt_class)
        self.assertEqual(gitread.current_branch(self.repo), "main")
        self.assertIn(head, [line.split()[0] for line in _repo.git(self.repo, "log", "--format=%H", "origin/main").stdout.split()])
        self.assertEqual(gitread.rev_parse(self.repo, "main"), gitread.rev_parse(self.repo, "origin/main"))
        self.assertEqual(result.merge_sha, gitread.rev_parse(self.repo, "main"))

    def test_every_mutation_leaves_an_intent_entry_and_a_result_entry(self):
        self.make_task_commit()
        self.run_tail()
        phases = {}
        for entry in self.ops.entries:
            phases.setdefault(entry["op"], []).append(entry["phase"])
        self.assertTrue(phases, "no git ops were recorded")
        for op, seen in phases.items():
            self.assertIn("intent", seen, op)
            self.assertIn("result", seen, op)

    def test_delete_branch_removes_the_local_task_branch_after_a_full_verify(self):
        self.make_task_commit()
        self.run_tail()
        self.assertTrue(gitread.branch_exists(self.repo, self.branch))
        gitwrite.delete_branch(self.repo, self.branch, ops=self.ops, task_id=self.task_id)
        self.assertFalse(gitread.branch_exists(self.repo, self.branch))


class GateRefusal(TailBase):
    def test_a_gate_that_exits_nonzero_stops_before_the_merge(self):
        head = self.make_task_commit()
        result = self.run_tail(gate=["bash", "-c", "echo gate said no; exit 1"])
        self.assertFalse(result.ok)
        self.assertEqual(result.halt_class, contracts.HALT_GATE_REFUSED)
        self.assertEqual(gitread.rev_parse(self.repo, "origin/main"), self.baseline)
        self.assertEqual(gitread.rev_parse(self.repo, self.branch), head)
        with open(self.gate_log) as handle:
            self.assertIn("gate said no", handle.read())

    def test_a_gate_that_leaves_the_tree_dirty_is_an_unclean_exit_before_the_merge(self):
        self.make_task_commit()
        result = self.run_tail(gate=["bash", "-c", "echo build output > leftover.txt"])
        self.assertFalse(result.ok)
        self.assertEqual(result.halt_class, contracts.HALT_UNCLEAN_EXIT)
        self.assertEqual(gitread.rev_parse(self.repo, "origin/main"), self.baseline)


class PushRefusal(TailBase):
    pre_push = "#!/bin/bash\necho hook refused the push >&2\nexit 1\n"

    def test_a_refused_push_is_gate_refused_with_the_merge_left_in_place(self):
        self.make_task_commit()
        result = self.run_tail()
        self.assertFalse(result.ok)
        self.assertEqual(result.halt_class, contracts.HALT_GATE_REFUSED)
        self.assertEqual(gitread.rev_parse(self.repo, "origin/main"), self.baseline)
        self.assertNotEqual(gitread.rev_parse(self.repo, "main"), self.baseline)
        self.assertTrue(gitread.log_oneline(self.repo, "origin/main", "main"))
        self.assertIn("hook refused the push", result.evidence.get("push_output", ""))


class PathGateBackstop(TailBase):
    def test_a_branch_diff_touching_the_claude_dir_is_refused_before_the_gate(self):
        self.make_task_commit(files={".claude/settings.json": "{}\n"})
        result = self.run_tail(gate=["bash", "-c", "echo the gate must not run; exit 1"])
        self.assertFalse(result.ok)
        self.assertEqual(result.halt_class, contracts.HALT_PATH_GATE)
        self.assertIn(".claude/settings.json", result.evidence["paths"])
        self.assertFalse(os.path.exists(self.gate_log), "the gate ran despite the backstop")
        self.assertTrue(gitread.branch_exists(self.repo, self.branch))


class RemoteMoved(TailBase):
    def test_a_remote_past_the_baseline_halts_before_any_merge(self):
        self.make_task_commit()
        _repo.git(self.repo, "checkout", "-q", "main")
        commit_on_branch(self.repo, "main", {"other.txt": "someone else\n"}, "other work")
        _repo.git(self.repo, "push", "-q", "origin", "main")
        moved = gitread.rev_parse(self.repo, "origin/main")
        result = self.run_tail()
        self.assertFalse(result.ok)
        self.assertEqual(result.halt_class, contracts.HALT_REMOTE_ADVANCED)
        self.assertEqual(gitread.rev_parse(self.repo, "origin/main"), moved)
        self.assertEqual(result.evidence["remote_sha"], moved)
        self.assertEqual(result.evidence["baseline_sha"], self.baseline)

    def test_a_conflicting_merge_is_aborted_and_leaves_a_clean_tree_on_the_default(self):
        self.make_task_commit(files={"shared.txt": "from the task\n"})
        _repo.git(self.repo, "checkout", "-q", "main")
        commit_on_branch(self.repo, "main", {"shared.txt": "from the operator\n"}, "local work")
        result = self.run_tail()
        self.assertFalse(result.ok)
        self.assertEqual(result.halt_class, contracts.HALT_REMOTE_ADVANCED)
        self.assertFalse(gitread.merge_head_exists(self.repo))
        self.assertTrue(gitread.is_clean(self.repo))
        self.assertEqual(gitread.current_branch(self.repo), "main")
        self.assertTrue(gitread.branch_exists(self.repo, self.branch))


class PreFlight(TailBase):
    def test_a_stray_task_branch_fails_pre_flight_by_name(self):
        self.make_task_commit()
        _repo.git(self.repo, "checkout", "-q", "main")
        result = gitwrite.preflight(self.repo, "main", self.branch)
        self.assertFalse(result.ok)
        self.assertEqual(result.failed, "no_task_branch")

    def test_a_clean_repo_on_the_default_in_sync_passes_every_check(self):
        result = gitwrite.preflight(self.repo, "main", self.branch)
        self.assertTrue(result.ok, result.evidence)
        self.assertIsNone(result.failed)

    def test_a_dirty_tree_fails_on_tree_clean(self):
        with open(os.path.join(self.repo, "scratch.txt"), "w") as handle:
            handle.write("in progress\n")
        result = gitwrite.preflight(self.repo, "main", self.branch)
        self.assertEqual(result.failed, "tree_clean")

    def test_a_local_head_past_the_remote_fails_on_head_equals_remote(self):
        commit_on_branch(self.repo, "main", {"ahead.txt": "unpushed\n"}, "unpushed work")
        result = gitwrite.preflight(self.repo, "main", self.branch)
        self.assertEqual(result.failed, "head_equals_remote")


class BlockedAndTimeout(TailBase):
    def test_the_blocked_path_returns_to_the_default_and_records_the_stranded_branch(self):
        head = self.make_task_commit()
        record = gitwrite.blocked_path(self.repo, "main", self.branch, ops=self.ops, task_id=self.task_id)
        self.assertEqual(gitread.current_branch(self.repo), "main")
        self.assertTrue(gitread.branch_exists(self.repo, self.branch))
        self.assertEqual(record["branch"], self.branch)
        self.assertEqual(record["head"], head)

    def test_a_timeout_with_a_clean_tree_on_the_task_branch_takes_the_blocked_path(self):
        self.make_task_commit()
        disposition = gitwrite.timeout_disposition(self.repo, "main", self.branch)
        self.assertEqual(disposition.action, "blocked")
        self.assertEqual(disposition.tree, "clean")
        self.assertEqual(disposition.branch, self.branch)
        gitwrite.blocked_path(self.repo, "main", self.branch, ops=self.ops, task_id=self.task_id)
        self.assertEqual(gitread.current_branch(self.repo), "main")
        self.assertTrue(gitread.branch_exists(self.repo, self.branch))

    def test_a_timeout_with_a_dirty_tree_halts(self):
        self.make_task_commit()
        with open(os.path.join(self.repo, "half-written.py"), "w") as handle:
            handle.write("incomplete\n")
        disposition = gitwrite.timeout_disposition(self.repo, "main", self.branch)
        self.assertEqual(disposition.action, "halt")
        self.assertEqual(disposition.tree, "dirty")


class CloseoutScopeCheck(TailBase):
    def setUp(self):
        super().setUp()
        self.allowed = ["docs/", contracts.CONCEPTS_FILE, "tracker.md"]

    def test_a_commit_outside_the_allowed_paths_resets_the_branch_and_names_the_path(self):
        pre = gitread.rev_parse(self.repo, "main")
        commit_on_branch(self.repo, "main", {"src/x.py": "sneaky = True\n"}, "closeout overreach")
        result = gitwrite.closeout_scope_check(self.repo, pre, self.allowed, ops=self.ops, task_id=self.task_id)
        self.assertFalse(result.ok)
        self.assertEqual(result.halt_class, contracts.HALT_CLOSEOUT_OUT_OF_SCOPE)
        self.assertIn("src/x.py", result.offending)
        self.assertEqual(gitread.rev_parse(self.repo, "main"), pre)
        self.assertEqual(gitread.rev_parse(self.repo, "origin/main"), self.baseline)

    def test_a_concepts_file_and_a_solutions_doc_pass_the_scope_check(self):
        pre = gitread.rev_parse(self.repo, "main")
        commit_on_branch(
            self.repo, "main",
            {contracts.CONCEPTS_FILE: "# Concepts\n", "docs/solutions/x/y.md": "# learning\n"},
            "closeout docs",
        )
        result = gitwrite.closeout_scope_check(self.repo, pre, self.allowed, ops=self.ops, task_id=self.task_id)
        self.assertTrue(result.ok, result.offending)
        self.assertEqual(result.offending, [])
        push = gitwrite.push(self.repo, ["origin", "main"], ops=self.ops, task_id=self.task_id)
        self.assertTrue(push.ok, push.output)
        self.assertNotEqual(gitread.rev_parse(self.repo, "origin/main"), self.baseline)

    def test_a_closeout_that_committed_nothing_and_changed_nothing_passes(self):
        pre = gitread.rev_parse(self.repo, "main")
        result = gitwrite.closeout_scope_check(self.repo, pre, self.allowed)
        self.assertTrue(result.ok)
        self.assertEqual(result.offending, [])

    def test_an_untracked_file_outside_the_allowed_paths_is_offending(self):
        """The check read the commit diff, so a closeout that never committed passed it however
        much it had changed. An untracked file is the plainest version: nothing to diff."""
        pre = gitread.rev_parse(self.repo, "main")
        with open(os.path.join(self.repo, "leftover.py"), "w") as handle:
            handle.write("scratch = True\n")
        result = gitwrite.closeout_scope_check(self.repo, pre, self.allowed, ops=self.ops,
                                               task_id=self.task_id)
        self.assertFalse(result.ok)
        self.assertEqual(result.halt_class, contracts.HALT_CLOSEOUT_OUT_OF_SCOPE)
        self.assertIn("leftover.py", result.offending)
        self.assertIn("leftover.py", result.untracked,
                      "an untracked offender survives the reset and has to be named as such")

    def test_an_uncommitted_edit_outside_the_allowed_paths_is_offending_and_is_reset(self):
        pre = gitread.rev_parse(self.repo, "main")
        with open(os.path.join(self.repo, "README.md"), "w") as handle:
            handle.write("# rewritten by the closeout\n")
        result = gitwrite.closeout_scope_check(self.repo, pre, self.allowed, ops=self.ops,
                                               task_id=self.task_id)
        self.assertFalse(result.ok)
        self.assertEqual(result.halt_class, contracts.HALT_CLOSEOUT_OUT_OF_SCOPE)
        self.assertIn("README.md", result.offending)
        self.assertTrue(gitread.is_clean(self.repo), "the reset left the tree dirty")

    def test_a_dirty_tree_inside_the_allowed_paths_is_an_unclean_exit_not_an_overreach(self):
        """In scope but uncommitted is a different failure from out of scope, and it gets the
        class that names it. Left alone it refuses the next task at pre flight instead."""
        pre = gitread.rev_parse(self.repo, "main")
        os.makedirs(os.path.join(self.repo, "docs", "solutions", "x"), exist_ok=True)
        with open(os.path.join(self.repo, "docs", "solutions", "x", "y.md"), "w") as handle:
            handle.write("# a learning the closeout never committed\n")
        result = gitwrite.closeout_scope_check(self.repo, pre, self.allowed, ops=self.ops,
                                               task_id=self.task_id)
        self.assertFalse(result.ok)
        self.assertEqual(result.halt_class, contracts.HALT_UNCLEAN_EXIT)
        self.assertEqual(result.offending, [])
        self.assertIn("docs/solutions/x/y.md", result.untracked)

    def test_a_path_holding_a_space_arrives_whole(self):
        """`git status --porcelain` quotes such a path; `-z` does not. A quoted path would not
        match the allowed list and would be reported as an overreach that never happened."""
        pre = gitread.rev_parse(self.repo, "main")
        os.makedirs(os.path.join(self.repo, "docs", "plans"), exist_ok=True)
        with open(os.path.join(self.repo, "docs", "plans", "a plan.md"), "w") as handle:
            handle.write("# spaced\n")
        result = gitwrite.closeout_scope_check(self.repo, pre, self.allowed, ops=self.ops,
                                               task_id=self.task_id)
        self.assertEqual(result.offending, [], "a quoted path was read as being outside docs/")
        self.assertIn("docs/plans/a plan.md", result.untracked)


class MirrorPush(TailBase):
    def test_the_mirror_argument_list_names_its_remote_and_destination(self):
        self.assertEqual(gitwrite.mirror_target(["origin", "main:release"]), ("origin", "release"))
        self.assertEqual(gitwrite.mirror_target(["origin", "release"]), ("origin", "release"))
        self.assertIsNone(gitwrite.mirror_target([]))

    def test_a_mirror_push_moves_the_mirror_ref_to_head(self):
        commit_on_branch(self.repo, "main", {"more.txt": "content\n"}, "more work")
        _repo.git(self.repo, "push", "-q", "origin", "main")
        result = gitwrite.mirror_push(self.repo, ["origin", "main:release"], ops=self.ops, task_id=self.task_id)
        self.assertTrue(result.ok, result.output)
        self.assertEqual(
            gitread.rev_parse(self.repo, "origin/release"), gitread.rev_parse(self.repo, "main")
        )


class PushTimeout(TailBase):
    """A push can run the project's own gate inside a pre-push hook, so it cannot be bounded by
    gitread's read timeout. Round three of Relay on Relay halted here: the repository's pre-push
    hook ran a 216 second suite against a 120 second read bound, and the runner killed its own
    push and reported it as an unexpected error."""

    pre_push = "#!/bin/sh\nsleep 1\n"

    def test_the_push_bound_is_the_gate_bound_plus_the_network_margin(self):
        self.assertEqual(gitwrite.push_timeout_for(600),
                         600 + contracts.PUSH_NETWORK_MARGIN_SECONDS)
        self.assertEqual(gitwrite.push_timeout_for(),
                         contracts.DEFAULT_GATE_TIMEOUT_MINUTES * 60
                         + contracts.PUSH_NETWORK_MARGIN_SECONDS)
        self.assertGreater(gitwrite.push_timeout_for(), gitread.GIT_TIMEOUT_SECONDS)

    def test_a_read_bound_would_kill_a_push_whose_hook_runs_a_gate(self):
        commit_on_branch(self.repo, "main", {"more.txt": "content\n"}, "more work")
        with self.assertRaises(subprocess.TimeoutExpired):
            gitread.run(self.repo, ["push", "origin", "main:probe"], timeout=0.5)

    def test_a_push_survives_a_hook_slower_than_the_read_bound(self):
        head = commit_on_branch(self.repo, "main", {"more.txt": "content\n"}, "more work")
        # The argument is the gate bound, not the subprocess bound: passing 1 here must still
        # allow the push the network margin on top, so a hook slower than 1 second lands.
        result = gitwrite.push(self.repo, ["origin", "main"], ops=self.ops,
                               task_id=self.task_id, timeout=1)
        self.assertTrue(result.ok, result.output)
        self.assertEqual(gitread.rev_parse(self.repo, "origin/main"), head)


class DanglingMerge(TailBase):
    def test_a_dangling_merge_is_aborted_on_startup(self):
        self.make_task_commit(files={"shared.txt": "from the task\n"})
        _repo.git(self.repo, "checkout", "-q", "main")
        commit_on_branch(self.repo, "main", {"shared.txt": "from the operator\n"}, "local work")
        _repo.git(self.repo, "merge", "--no-ff", self.branch, check=False)
        self.assertTrue(gitread.merge_head_exists(self.repo))
        self.assertTrue(gitwrite.abort_dangling_merge(self.repo, ops=self.ops, task_id=self.task_id))
        self.assertFalse(gitread.merge_head_exists(self.repo))
        self.assertFalse(gitwrite.abort_dangling_merge(self.repo))


class FakeClock:
    """A monotonic clock that only moves when the code under test sleeps."""

    def __init__(self):
        self.t = 0.0
        self.slept = []

    def monotonic(self):
        return self.t

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.t += seconds


class PrMode(unittest.TestCase):
    branch = "relay/T-1"

    def test_find_pr_returns_the_open_pull_request_for_the_task_branch(self):
        run = FakeRun([(0, '[{"url": "https://example.invalid/pr/7", "number": 7}]', "")])
        found = gitwrite.find_pr(run, self.branch)
        self.assertEqual(found["number"], 7)
        self.assertEqual(found["url"], "https://example.invalid/pr/7")
        self.assertIn(self.branch, run.calls[0])

    def test_find_pr_returns_none_when_no_pull_request_exists(self):
        run = FakeRun([(0, "[]", "")])
        self.assertIsNone(gitwrite.find_pr(run, self.branch))

    def test_ci_that_never_decides_within_the_bound_is_ci_undecided(self):
        clock = FakeClock()
        run = FakeRun([(8, "pending\n", "")])
        result = gitwrite.poll_ci(run, self.branch, 2, interval_seconds=1,
                                  sleep=clock.sleep, monotonic=clock.monotonic)
        self.assertEqual(result.state, "undecided")
        self.assertEqual(result.halt_class, contracts.HALT_CI_UNDECIDED)
        self.assertGreaterEqual(len(run.calls), 2)
        self.assertEqual(clock.slept, [1, 1])

    def test_ci_that_goes_green_within_the_bound_passes(self):
        clock = FakeClock()
        run = FakeRun([(8, "pending\n", ""), (0, "all checks passed\n", "")])
        result = gitwrite.poll_ci(run, self.branch, 60, interval_seconds=1,
                                  sleep=clock.sleep, monotonic=clock.monotonic)
        self.assertEqual(result.state, "pass")
        self.assertIsNone(result.halt_class)

    def test_ci_that_fails_stops_polling(self):
        clock = FakeClock()
        run = FakeRun([(1, "1 failing check\n", "")])
        result = gitwrite.poll_ci(run, self.branch, 60, interval_seconds=1,
                                  sleep=clock.sleep, monotonic=clock.monotonic)
        self.assertEqual(result.state, "fail")
        self.assertEqual(len(run.calls), 1)


if __name__ == "__main__":
    unittest.main()


class GateProcessGroup(TailBase):
    """The gate builds, so it spawns compilers and test runners. Killing only the process the
    manifest names left those running into the next task (review finding #14)."""

    def test_a_gate_that_hangs_is_killed_with_its_whole_process_group(self):
        marker = os.path.join(self.tmp.name, "child.pid")
        script = (
            "import os, subprocess, sys, time\n"
            "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
            "open(%r, 'w').write(str(child.pid))\n"
            "time.sleep(120)\n" % marker
        )
        path = os.path.join(self.tmp.name, "slow_gate.py")
        with open(path, "w") as handle:
            handle.write(script)
        result = gitwrite.run_gate(self.repo, ["python3", path], self.gate_log, timeout_seconds=2)
        self.assertFalse(result.ok)
        self.assertTrue(result.timed_out)
        with open(marker) as handle:
            child_pid = int(handle.read().strip())
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            try:
                os.kill(child_pid, 0)
            except (ProcessLookupError, PermissionError):
                break
            time.sleep(0.1)
        else:
            self.fail("the gate's child outlived the gate timeout")

    def test_a_passing_gate_still_captures_its_output(self):
        result = gitwrite.run_gate(self.repo, ["bash", "-c", "echo gate ran fine"], self.gate_log)
        self.assertTrue(result.ok)
        self.assertEqual(result.returncode, 0)
        with open(self.gate_log) as handle:
            self.assertIn("gate ran fine", handle.read())
