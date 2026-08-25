"""U10: the run loop end to end, over the stub, with a real temp repo and a real tracker file.

This is the unit that proves the pieces fit. Six stub entries drive three tasks through the
fixed sequence of R50: two land and one blocks, the tracker file at the remote reflects all
three, and only the blocked task's branch is left behind.
"""
import json
import os
import tempfile
import textwrap
import unittest

import _paths
import _repo
from relay import contracts, gitread, manifest as mf, run as runner, state

TRANSCRIPTS = os.path.join(_paths.FIXTURES_DIR, "transcripts")

TRACKER_MD = """# Tasks

- [ ] T-1 Add the brief renderer
- [ ] T-2 Wire the run loop
- [ ] T-3 Write the summary
"""

MANIFEST = """
[project]
repo = "__REPO__"
default_branch = "main"
mirror = []

[tracker]
adapter = "markdown"
file = "tracker.md"
done_statuses = ["closed"]
in_review_status = "in review"

[shipping]
mode = "local_merge"

[permissions]
allowed = ["Bash", "Read", "Edit", "Write", "Grep", "Glob", "Skill"]
disallowed = []

[timeouts]
task_minutes = 11
closeout_minutes = 11
ci_poll_minutes = 11

[closeout]
allowed_paths = []

[gate]
command = ["true"]
description = "the test suite, run locally before merge and again by the pre push hook"

[qualifying]
gate = "A pre push hook runs the full test suite and refuses the push on failure."
durable_state = "Merge commits on main and the tracker.md lines are the only state between tasks."
independence = "Each listed task touches a separate module."
editors = "Only the operator's own account edits tracker.md."

[on_blocked]
merge_partial = true
open_followup = false

[[tasks]]
id = "T-1"
model = "sonnet"
effort = "low"

[[tasks]]
id = "T-2"
model = "sonnet"
effort = "low"

[[tasks]]
id = "T-3"
model = "sonnet"
effort = "low"
"""

HELPER = '''
import subprocess
import sys


def sha():
    proc = subprocess.run(["git", "rev-parse", "origin/main"], capture_output=True, text=True)
    return proc.stdout.strip()


def lines():
    with open("tracker.md") as handle:
        return handle.read().splitlines()


def save(rows):
    with open("tracker.md", "w") as handle:
        handle.write("\\n".join(rows) + "\\n")


command, task = sys.argv[1], sys.argv[2]
out = []
for line in lines():
    if command == "close" and line.startswith("- [ ] %s " % task):
        out.append("- [x] " + line[len("- [ ] "):] + " (%s)" % sha()[:7])
        continue
    out.append(line)
    if command == "comment" and line.startswith("- [ ] %s " % task):
        out.append("  - 2026-08-25 " + sys.argv[3])
save(out)
'''

TASK_BRANCH_SH = """set -e
git checkout -q -b relay/%s main
mkdir -p src
echo "value = 1" > src/%s.py
git add -A
git commit -q -m "%s work"
"""

CLOSE_SH = """set -e
git checkout -q main
python3 "$RELAY_HELPER" close %s
git add tracker.md
git commit -q -m "close %s"
"""

COMMENT_SH = """set -e
git checkout -q main
python3 "$RELAY_HELPER" comment %s "blocked on the design question"
git add tracker.md
git commit -q -m "comment %s"
"""

DIRTY_AND_HANG_SH = """set -e
echo "half written" > src_half.py
sleep 30
"""


class RunCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = _repo.make_repo(self.tmp.name, files={"tracker.md": TRACKER_MD,
                                                          "README.md": "# fixture\n"})
        self.home = os.path.join(self.tmp.name, "home")
        self.queue = os.path.join(self.tmp.name, "queue")
        os.makedirs(self.home)
        os.makedirs(self.queue)
        self.helper = os.path.join(self.tmp.name, "helper.py")
        with open(self.helper, "w") as handle:
            handle.write(textwrap.dedent(HELPER))
        self.manifest_path = os.path.join(self.tmp.name, "manifest.toml")
        with open(self.manifest_path, "w") as handle:
            handle.write(MANIFEST.replace("__REPO__", self.repo))
        self.manifest = mf.load(self.manifest_path)
        self.entry = 0

    def tearDown(self):
        self.tmp.cleanup()

    def base_env(self):
        return dict(os.environ, HOME=self.home, RELAY_STUB_QUEUE=self.queue,
                    RELAY_HELPER=self.helper,
                    PATH=_paths.STUB_DIR + os.pathsep + os.environ.get("PATH", ""))

    def queue_entry(self, fixture, git_sh=None, exit_code=0, sleep=0):
        self.entry += 1
        entry_dir = os.path.join(self.queue, str(self.entry))
        os.makedirs(entry_dir)
        with open(os.path.join(entry_dir, "entry.json"), "w") as handle:
            json.dump({"fixture": os.path.join(TRANSCRIPTS, fixture), "exit": exit_code,
                       "sleep": sleep}, handle)
        if git_sh:
            with open(os.path.join(entry_dir, "git.sh"), "w") as handle:
                handle.write(git_sh)

    def task_success(self, task_id):
        self.queue_entry("success.jsonl", TASK_BRANCH_SH % (task_id, task_id.lower().replace("-", "_"), task_id))

    def task_blocked(self, task_id):
        self.queue_entry("blocked.jsonl", TASK_BRANCH_SH % (task_id, task_id.lower().replace("-", "_"), task_id))

    def closeout_landed(self, task_id):
        self.queue_entry("closeout_skipped.jsonl", CLOSE_SH % (task_id, task_id))

    def closeout_blocked(self, task_id):
        self.queue_entry("closeout_skipped.jsonl", COMMENT_SH % (task_id, task_id))

    def store(self):
        return state.StateStore(self.manifest_path, self.repo, home=self.home)

    def go(self, **kwargs):
        kwargs.setdefault("base_env", self.base_env())
        kwargs.setdefault("home", self.home)
        kwargs.setdefault("stream", lambda line: None)
        kwargs.setdefault("launch_kwargs", {"sigkill_grace_seconds": 2, "heartbeat_interval": 0})
        return runner.run(self.manifest, **kwargs)

    def tracker_at_remote(self):
        return gitread.show(self.repo, "origin/main", "tracker.md") or ""

    def relay_branches(self):
        text = _repo.git(self.repo, "branch", "--list", "relay/*").stdout
        return sorted(line.strip("* ").strip() for line in text.splitlines() if line.strip())


class EndToEnd(RunCase):
    def setUp(self):
        super().setUp()
        self.task_success("T-1")
        self.closeout_landed("T-1")
        self.task_blocked("T-2")
        self.closeout_blocked("T-2")
        self.task_success("T-3")
        self.closeout_landed("T-3")

    def test_three_tasks_land_block_and_land_with_the_tracker_and_branches_to_match(self):
        outcome = self.go()
        self.assertEqual(outcome.exit_code, runner.EXIT_OK, outcome.message)

        tracker = self.tracker_at_remote()
        self.assertIn("- [x] T-1 Add the brief renderer (", tracker)
        self.assertIn("- [ ] T-2 Wire the run loop", tracker)
        self.assertIn("  - 2026-08-25 blocked on the design question", tracker)
        self.assertIn("- [x] T-3 Write the summary (", tracker)

        records = self.store().records()
        self.assertEqual(records["T-1"]["status"], contracts.STATUS_LANDED)
        self.assertEqual(records["T-2"]["status"], contracts.STATUS_BLOCKED)
        self.assertEqual(records["T-2"]["halt_class"], contracts.HALT_BLOCKED_ENVELOPE)
        self.assertEqual(records["T-3"]["status"], contracts.STATUS_LANDED)

        self.assertEqual(self.relay_branches(), ["relay/T-2"])
        self.assertTrue(gitread.is_clean(self.repo))
        self.assertEqual(gitread.current_branch(self.repo), "main")

    def test_the_terminal_record_reads_completed_and_the_lease_is_released(self):
        self.go()
        store = self.store()
        self.assertEqual(store.terminal()["run_status"], contracts.RUN_COMPLETED)
        self.assertIsNone(store.lease())
        self.assertEqual(store.status_word(), contracts.RUN_COMPLETED)

    def test_each_landed_record_carries_its_landing_reference_and_verify_timestamp(self):
        self.go()
        for task_id in ("T-1", "T-3"):
            record = self.store().get(task_id)
            self.assertTrue(record["landing_ref"], task_id)
            self.assertTrue(record["verify"]["at"], task_id)
            self.assertEqual(record["verify"]["scope"], "full")
            self.assertTrue(record["verify"]["landed"], task_id)

    def test_the_run_leaves_a_brief_a_log_and_a_digest_for_every_task(self):
        self.go()
        store = self.store()
        for task_id in ("T-1", "T-2", "T-3"):
            self.assertTrue(os.path.exists(store.path("briefs", task_id + ".md")), task_id)
            self.assertTrue(os.path.exists(store.path("logs", task_id + ".stdout.log")), task_id)
            self.assertTrue(os.path.exists(store.path("digests", task_id + ".json")), task_id)

    def test_no_task_carries_another_task_data(self):
        """R15: nothing crosses between tasks except the manifest, git, and the tracker."""
        self.go()
        store = self.store()
        for task_id, other in (("T-1", "T-3"), ("T-3", "T-1")):
            with open(store.path("briefs", task_id + ".md")) as handle:
                self.assertNotIn(other, handle.read())


class BlockedByPathGate(RunCase):
    def test_a_path_gate_exit_blocks_that_task_and_the_run_continues(self):
        self.task_success("T-1")
        self.closeout_landed("T-1")
        self.queue_entry("path_gate.jsonl", TASK_BRANCH_SH % ("T-2", "t_2", "T-2"))
        self.closeout_blocked("T-2")
        self.task_success("T-3")
        self.closeout_landed("T-3")

        outcome = self.go()
        self.assertEqual(outcome.exit_code, runner.EXIT_OK, outcome.message)
        records = self.store().records()
        self.assertEqual(records["T-2"]["status"], contracts.STATUS_BLOCKED)
        self.assertEqual(records["T-2"]["halt_class"], contracts.HALT_PATH_GATE)
        self.assertIn(contracts.HALT_PATH_GATE, [f["class"] for f in records["T-2"]["findings"]])
        self.assertEqual(records["T-3"]["status"], contracts.STATUS_LANDED)


class ExcludedByScan(RunCase):
    def test_a_task_whose_text_names_a_claude_path_is_excluded_before_any_launch(self):
        with open(os.path.join(self.repo, "tracker.md"), "w") as handle:
            handle.write(TRACKER_MD.replace("- [ ] T-1 Add the brief renderer",
                                            "- [ ] T-1 Update .claude/skills/x/SKILL.md"))
        _repo.git(self.repo, "add", "tracker.md")
        _repo.git(self.repo, "commit", "-q", "-m", "point T-1 at a claude path")
        _repo.git(self.repo, "push", "-q", "origin", "main")
        self.task_success("T-2")
        self.closeout_landed("T-2")
        self.task_success("T-3")
        self.closeout_landed("T-3")

        outcome = self.go()
        self.assertEqual(outcome.exit_code, runner.EXIT_OK, outcome.message)
        record = self.store().get("T-1")
        self.assertEqual(record["status"], contracts.STATUS_EXCLUDED)
        self.assertIn(".claude/skills/x/SKILL.md", record["excluded_reason"])
        self.assertEqual(self.store().get("T-2")["status"], contracts.STATUS_LANDED)


class ManifestExclusion(RunCase):
    def test_a_task_the_manifest_excluded_is_never_launched(self):
        text = MANIFEST.replace("__REPO__", self.repo).replace(
            'id = "T-2"\nmodel = "sonnet"\neffort = "low"',
            'id = "T-2"\nmodel = "sonnet"\neffort = "low"\nexcluded = true\nreason = "needs an operator decision"')
        with open(self.manifest_path, "w") as handle:
            handle.write(text)
        self.manifest = mf.load(self.manifest_path)
        self.task_success("T-1")
        self.closeout_landed("T-1")
        self.task_success("T-3")
        self.closeout_landed("T-3")

        outcome = self.go()
        self.assertEqual(outcome.exit_code, runner.EXIT_OK, outcome.message)
        record = self.store().get("T-2")
        self.assertEqual(record["status"], contracts.STATUS_EXCLUDED)
        self.assertIn("operator decision", record["excluded_reason"])
        self.assertEqual(self.store().get("T-3")["status"], contracts.STATUS_LANDED)


class TimeoutHalts(RunCase):
    def test_a_timeout_that_left_a_dirty_tree_halts_and_leaves_the_next_task_untouched(self):
        self.task_success("T-1")
        self.closeout_landed("T-1")
        self.queue_entry("success.jsonl", DIRTY_AND_HANG_SH)
        self.task_success("T-3")
        self.closeout_landed("T-3")

        outcome = self.go(timeout_overrides={"task_seconds": 2})
        self.assertEqual(outcome.exit_code, runner.EXIT_HALTED, outcome.message)
        self.assertEqual(outcome.halt_task, "T-2")
        self.assertEqual(outcome.halt_class, contracts.HALT_TIMEOUT)

        store = self.store()
        self.assertEqual(store.get("T-2")["status"], contracts.STATUS_HALTED)
        self.assertIsNone(store.get("T-3"))
        terminal = store.terminal()
        self.assertEqual(terminal["run_status"], contracts.RUN_HALTED)
        self.assertEqual(terminal["halt_task"], "T-2")
        self.assertEqual(terminal["halt_class"], contracts.HALT_TIMEOUT)
        self.assertIsNone(store.lease(), "the lease was not released on halt")


class TimeoutContinues(RunCase):
    def test_a_timeout_that_left_a_clean_tree_blocks_that_task_and_the_run_continues(self):
        self.task_success("T-1")
        self.closeout_landed("T-1")
        self.queue_entry("success.jsonl", None, sleep=20)
        self.closeout_blocked("T-2")
        self.task_success("T-3")
        self.closeout_landed("T-3")

        outcome = self.go(timeout_overrides={"task_seconds": 2})
        self.assertEqual(outcome.exit_code, runner.EXIT_OK, outcome.message)
        record = self.store().get("T-2")
        self.assertEqual(record["status"], contracts.STATUS_BLOCKED)
        self.assertEqual(record["halt_class"], contracts.HALT_TIMEOUT)
        self.assertEqual(self.store().get("T-3")["status"], contracts.STATUS_LANDED)


class ResumeAfterHalt(RunCase):
    def test_a_repaired_repo_resumes_at_the_first_task_that_did_not_land(self):
        self.task_success("T-1")
        self.closeout_landed("T-1")
        self.queue_entry("success.jsonl", DIRTY_AND_HANG_SH)
        first = self.go(timeout_overrides={"task_seconds": 2})
        self.assertEqual(first.exit_code, runner.EXIT_HALTED)

        # The operator repairs by hand, which is the only repair Relay supports.
        os.remove(os.path.join(self.repo, "src_half.py"))
        if gitread.branch_exists(self.repo, "relay/T-2"):
            _repo.git(self.repo, "branch", "-D", "relay/T-2")
        self.assertTrue(gitread.is_clean(self.repo))

        self.task_success("T-2")
        self.closeout_landed("T-2")
        self.task_success("T-3")
        self.closeout_landed("T-3")
        second = self.go()

        self.assertEqual(second.exit_code, runner.EXIT_OK, second.message)
        records = self.store().records()
        self.assertEqual(records["T-1"]["status"], contracts.STATUS_LANDED)
        self.assertEqual(records["T-2"]["status"], contracts.STATUS_LANDED)
        self.assertEqual(records["T-3"]["status"], contracts.STATUS_LANDED)

    def test_a_landed_task_is_never_run_again(self):
        self.task_success("T-1")
        self.closeout_landed("T-1")
        self.queue_entry("success.jsonl", DIRTY_AND_HANG_SH)
        self.go(timeout_overrides={"task_seconds": 2})
        landed_at = self.store().get("T-1")["landing_ref"]

        os.remove(os.path.join(self.repo, "src_half.py"))
        if gitread.branch_exists(self.repo, "relay/T-2"):
            _repo.git(self.repo, "branch", "-D", "relay/T-2")
        self.task_success("T-2")
        self.closeout_landed("T-2")
        self.task_success("T-3")
        self.closeout_landed("T-3")
        self.go()

        self.assertEqual(self.store().get("T-1")["landing_ref"], landed_at)
        self.assertFalse(gitread.branch_exists(self.repo, "relay/T-1"))


class StartupValidate(RunCase):
    def test_a_landed_record_with_no_verify_timestamp_is_downgraded_and_run_again(self):
        """AE3: the ce-sweep rule. A landed record has to carry its evidence."""
        store = self.store()
        store.upsert("T-1", status=contracts.STATUS_LANDED, landing_ref=None, verify=None)
        self.task_success("T-1")
        self.closeout_landed("T-1")
        self.task_blocked("T-2")
        self.closeout_blocked("T-2")
        self.task_success("T-3")
        self.closeout_landed("T-3")

        outcome = self.go()
        self.assertEqual(outcome.exit_code, runner.EXIT_OK, outcome.message)
        self.assertEqual(self.store().get("T-1")["status"], contracts.STATUS_LANDED)
        self.assertTrue(self.store().get("T-1")["landing_ref"])


class LeaseRefusal(RunCase):
    def test_a_live_lease_refuses_the_second_run_and_changes_no_state(self):
        holder = self.store()
        self.assertTrue(holder.acquire().ok)
        before = json.dumps(holder.read(), sort_keys=True)

        other = state.StateStore(self.manifest_path, self.repo, home=self.home, pid=999999)
        outcome = self.go(store=other)

        self.assertEqual(outcome.exit_code, runner.EXIT_LEASE)
        # R31 and AE12: the refusal names the holder, not the runner that was refused, because
        # the holder is what the operator has to go and look at.
        self.assertIn(str(holder.pid), outcome.message or "")
        self.assertIn(self.manifest_path, outcome.message or "")
        self.assertEqual(json.dumps(holder.read(), sort_keys=True), before)
        holder.release()


class PreFlightRefusal(RunCase):
    def test_a_dirty_tree_at_the_start_halts_before_any_task_launches(self):
        with open(os.path.join(self.repo, "operator-wip.txt"), "w") as handle:
            handle.write("in progress\n")
        outcome = self.go()
        self.assertEqual(outcome.exit_code, runner.EXIT_HALTED)
        self.assertEqual(outcome.halt_task, "T-1")
        self.assertIn("tree_clean", outcome.message)
        self.assertEqual(self.store().get("T-1")["status"], contracts.STATUS_HALTED)


class RetryBlocked(RunCase):
    def test_a_blocked_task_is_skipped_by_default_and_retried_on_request(self):
        self.task_success("T-1")
        self.closeout_landed("T-1")
        self.task_blocked("T-2")
        self.closeout_blocked("T-2")
        self.task_success("T-3")
        self.closeout_landed("T-3")
        self.go()
        self.assertEqual(self.store().get("T-2")["status"], contracts.STATUS_BLOCKED)

        # Without the flag the blocked record is left alone and the queue is untouched.
        self.assertEqual(self.go().exit_code, runner.EXIT_OK)
        self.assertEqual(self.store().get("T-2")["status"], contracts.STATUS_BLOCKED)

        # With the flag, the stranded branch carries commits past the baseline, so R48 halts
        # naming it rather than deleting work.
        outcome = self.go(retry_blocked=True)
        self.assertEqual(outcome.exit_code, runner.EXIT_HALTED)
        self.assertIn("relay/T-2", outcome.message)


if __name__ == "__main__":
    unittest.main()
