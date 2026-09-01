"""U10: the run loop end to end, over the stub, with a real temp repo and a real tracker file.

This is the unit that proves the pieces fit. Six stub entries drive three tasks through the
fixed sequence of R50: two land and one blocks, the tracker file at the remote reflects all
three, and only the blocked task's branch is left behind.
"""
import json
import os
import tempfile
import textwrap
import time
import unittest
from dataclasses import replace

from types import SimpleNamespace

import _paths
import _repo
from relay import (backends, classify, closeout, contracts, gitread, gitwrite, launch,
                   manifest as mf, run as runner, state, verify)

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
git checkout -q -b %s main
mkdir -p src
echo "value = 1" > src/%s.py
git add -A
git commit -q -m "%s work"
"""


def task_branch_sh(task_id, prefix="relay/"):
    """The stub git.sh that creates the Task branch. First argument is the full name."""
    return TASK_BRANCH_SH % (prefix + task_id, task_id.lower().replace("-", "_"), task_id)

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

    def queue_entry(self, fixture, git_sh=None, exit_code=0, sleep=0, stream=None):
        """`stream` is the opt in stdout fixture (see the stub's queue protocol). Left None, the
        stub prints only system and result lines, which is what every case but the tail ones
        wants.

        `fixture` of None writes no transcript at all, which is how a process that left the
        runner nothing to read is staged (R20).
        """
        self.entry += 1
        entry_dir = os.path.join(self.queue, str(self.entry))
        os.makedirs(entry_dir)
        entry = {"exit": exit_code, "sleep": sleep}
        if fixture:
            entry["fixture"] = os.path.join(TRANSCRIPTS, fixture)
        if stream:
            entry["stream"] = stream
        with open(os.path.join(entry_dir, "entry.json"), "w") as handle:
            json.dump(entry, handle)
        if git_sh:
            with open(os.path.join(entry_dir, "git.sh"), "w") as handle:
                handle.write(git_sh)

    def task_success(self, task_id):
        self.queue_entry("success.jsonl", task_branch_sh(task_id, self.manifest.project.branch_prefix))

    def task_blocked(self, task_id):
        self.queue_entry("blocked.jsonl", task_branch_sh(task_id, self.manifest.project.branch_prefix))

    def closeout_landed(self, task_id):
        self.queue_entry("closeout_skipped.jsonl", CLOSE_SH % (task_id, task_id))

    def closeout_blocked(self, task_id):
        self.queue_entry("closeout_skipped.jsonl", COMMENT_SH % (task_id, task_id))

    def closeout_halted(self, task_id):
        """The halt-comment closeout (U3, relay task 50): fired after a task-scoped, non-run
        halt on a clean tree, appending a comment the same way `closeout_blocked` does under the
        markdown adapter."""
        self.queue_entry("closeout_skipped.jsonl", COMMENT_SH % (task_id, task_id))

    def store(self):
        return state.StateStore(self.manifest_path, self.repo, home=self.home)

    def seed_stale_lease(self, pid=999999, ttl_seconds=1):
        """A lease belonging to a different pid, self-recorded with a short ttl, the shape a
        crashed runner leaves behind. state.py's staleness check reads this ttl off the lease
        record itself, so a caller reclaiming it only needs its own clock to read past
        `ttl_seconds`, real or injected; the seed store's own clock does not matter afterward."""
        seed = state.StateStore(self.manifest_path, self.repo, home=self.home, pid=pid,
                                ttl_seconds=ttl_seconds)
        seed.acquire()
        return seed

    def seed_stale_reclaim_on_t1(self, pid=999999, ttl_seconds=1):
        """seed_stale_lease() plus T-1 marked running under it, simulating T-1's runner
        crashing mid task."""
        seed = self.seed_stale_lease(pid=pid, ttl_seconds=ttl_seconds)
        seed.upsert("T-1", status=contracts.STATUS_RUNNING)
        return seed

    def reclaiming_store(self, ttl_seconds=1):
        """A store whose clock already reads past a lease seeded with `ttl_seconds`, so
        `go(store=...)` reclaims it without a real sleep. The clock is frozen rather than
        `time.time`-relative so every timestamp this run's store writes stays consistent."""
        frozen = time.time() + ttl_seconds + 1
        return state.StateStore(self.manifest_path, self.repo, home=self.home, now=lambda: frozen)

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

    def test_classify_is_called_with_the_tasks_own_backend(self):
        """Backends U6: run.py's Task-classification call site is the one place that supplies
        classify.classify() the backend it dispatches on. A dropped, mistyped, or reordered
        `backend=` keyword here would fall through to classify's own "claude" default and
        silently misclassify a non-Claude Task, with no other test able to catch it. (The
        Closeout's own call site is separate and deliberately omits `backend=` until origin U9;
        this test only checks the Task call site, not every call recorded during the run.)"""
        from unittest import mock
        with mock.patch.object(runner.classify, "classify",
                               wraps=runner.classify.classify) as spy:
            self.go()
        task_calls = [call for call in spy.call_args_list if "backend" in call.kwargs]
        self.assertTrue(task_calls, "no call passed backend= at all")
        for call in task_calls:
            self.assertEqual(call.kwargs["backend"], "claude")

    def test_the_terminal_record_reads_completed_and_the_lease_is_released(self):
        self.go()
        store = self.store()
        self.assertEqual(store.terminal()["run_status"], contracts.RUN_COMPLETED)
        self.assertIsNone(store.lease())
        self.assertEqual(store.status_word(), contracts.RUN_COMPLETED)

    def test_the_terminal_record_carries_the_pinned_and_observed_cli_versions(self):
        """The observed field comes from a real `claude --version` call against the stub
        binary (not an injected fake), so a drift between it and CLI_VERSION_TESTED lands in
        state.json rather than staying silently invisible."""
        self.go()
        terminal = self.store().terminal()
        self.assertEqual(terminal["cli_version"], {"claude": contracts.CLI_VERSION_TESTED})
        self.assertEqual(terminal["cli_version_observed"], {"claude": contracts.CLI_VERSION_TESTED})

    def test_the_observed_cli_version_diverges_from_the_pinned_one_when_the_binary_reports_differently(self):
        env = self.base_env()
        env["RELAY_STUB_CLI_VERSION"] = "9.9.9 (Claude Code)"
        self.go(base_env=env)
        terminal = self.store().terminal()
        self.assertEqual(terminal["cli_version"], {"claude": contracts.CLI_VERSION_TESTED})
        self.assertEqual(terminal["cli_version_observed"], {"claude": "9.9.9"})
        self.assertNotEqual(terminal["cli_version"], terminal["cli_version_observed"])

    def test_terminal_version_maps_name_only_backends_that_launched(self):
        from unittest import mock
        store = self.store()
        with mock.patch.object(runner.launch, "cli_version", side_effect=lambda _env, backend: backend):
            runner._write_terminal(store, self.base_env(), contracts.RUN_COMPLETED,
                                   used_backends={"codex", "grok"})
        terminal = store.terminal()
        self.assertEqual(set(terminal["cli_version"]), {"codex", "grok"})
        self.assertEqual(terminal["cli_version_observed"], {"codex": "codex", "grok": "grok"})
        self.assertNotIn("claude", terminal["cli_version"])

    def test_a_launched_record_carries_its_backend_and_actual_command_evidence(self):
        self.go()
        record = self.store().get("T-1")
        self.assertEqual(record["backend"], "claude")
        self.assertTrue(record["binary_path"].endswith("/claude"))
        self.assertEqual(record["args"][0], "claude")

    def test_a_launch_error_does_not_add_an_unlaunched_backend_to_terminal_versions(self):
        def unavailable(*_args, **_kwargs):
            raise OSError("binary unavailable")

        self.go(launch_kwargs={"popen": unavailable, "sigkill_grace_seconds": 2,
                                "heartbeat_interval": 0})
        terminal = self.store().terminal()
        self.assertEqual(terminal["cli_version"], {})
        self.assertEqual(terminal["cli_version_observed"], {})

    def test_resume_uses_the_recorded_backend_after_a_manifest_edit(self):
        self.go()
        self.task_success("T-2")
        self.closeout_landed("T-2")
        self.manifest = replace(
            self.manifest,
            tasks=tuple(replace(task, backend="codex") if task.id == "T-2" else task
                        for task in self.manifest.tasks),
        )
        self.go(retry_blocked=True)
        record = self.store().get("T-2")
        self.assertEqual(record["backend"], "claude")
        self.assertEqual(record["args"][0], "claude")

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


class DivergedRemoteAfterPush(RunCase):
    """R1: the final verify must fetch, or a remote that moved after the runner's own push, a
    concurrent force push, a competing writer, reads as landed anyway. The check would only be
    proving the push succeeded, never that the remote still agrees."""

    def install_diverge_after_second_main_push_hook(self):
        """A post-receive hook on the bare origin that force-updates refs/heads/main to a
        pre-pushed divergent ref on the *second* push that updates refs/heads/main. The merge
        push (run.py:421) is the first, the closeout's push (run.py:539) is the second. Filters
        by updated ref name (post-receive fires on every push, not just main) so the setup-time
        rogue push and push order can never miscount invocations."""
        from test_gitwrite import commit_on_branch
        commit_on_branch(self.repo, "rogue", {"rogue.txt": "a third party's rogue commit\n"},
                         "rogue", base="main")
        _repo.git(self.repo, "push", "-q", "origin", "rogue:refs/heads/rogue")
        _repo.git(self.repo, "checkout", "-q", "main")
        _repo.git(self.repo, "branch", "-D", "rogue")
        self.rogue_sha = gitread.rev_parse(self.repo, "origin/rogue")

        bare = self.repo + ".git"
        hook = os.path.join(bare, "hooks", "post-receive")
        with open(hook, "w") as handle:
            handle.write(textwrap.dedent("""\
                #!/bin/sh
                set -e
                counter="$PWD/relay_test_main_push_count"
                while read oldrev newrev refname; do
                  if [ "$refname" = "refs/heads/main" ]; then
                    n=$(( $(cat "$counter" 2>/dev/null || echo 0) + 1 ))
                    echo "$n" > "$counter"
                    if [ "$n" -ge 2 ]; then
                      git update-ref refs/heads/main refs/heads/rogue
                    fi
                  fi
                done
                """))
        os.chmod(hook, 0o755)

    def test_a_remote_that_diverges_after_the_runners_own_pushes_is_not_landed(self):
        """This same fixture would report T-1 landed if `do_fetch` were `False` at run.py:449:
        the local tracking ref the check reads without a fetch still shows the sha the runner
        itself just pushed, not the rogue sha the bare origin now actually holds."""
        self.install_diverge_after_second_main_push_hook()
        self.task_success("T-1")
        self.closeout_landed("T-1")

        outcome = self.go()
        self.assertEqual(outcome.exit_code, runner.EXIT_HALTED, outcome.message)

        record = self.store().get("T-1")
        self.assertNotEqual(record["status"], contracts.STATUS_LANDED)
        check = record["verify"]["checks"]["head_equals_remote"]
        self.assertEqual(check["result"], verify.FAIL)
        self.assertEqual(check["evidence"]["remote_sha"], self.rogue_sha)


class BlockedByPathGate(RunCase):
    def test_a_path_gate_exit_blocks_that_task_and_the_run_continues(self):
        self.task_success("T-1")
        self.closeout_landed("T-1")
        self.queue_entry("path_gate.jsonl", task_branch_sh("T-2"))
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


class UnreadableEvidenceNeverRescues(RunCase):
    """R20, KTD5. The no envelope rescue route merges on commits plus a card in the in review
    status. Evidence the runner could not read used to arrive at that route wearing the
    no_envelope class, so a task nobody ever observed could merge code on the strength of a
    card someone moved by hand.

    `_routable` is exercised directly here because the markdown adapter reports only open or
    closed, so the end to end path in this module can never reach the rescue branch at all (see
    test_manifest's markdown warning). The direct call is the only place both sides of the
    narrowing, the refusal and the merge that must survive it, can be put beside each other.
    """

    def stage_branch_with_commits(self):
        """What the rescue route reads on the git side: a task branch carrying work that is not
        on main yet."""
        from test_gitwrite import commit_on_branch
        self.baseline_sha = gitread.rev_parse(self.repo, "main")
        commit_on_branch(self.repo, "relay/T-1", {"src/t_1.py": "value = 1\n"}, "T-1 work",
                         base="main")
        _repo.git(self.repo, "checkout", "-q", "main")

    def digest_for(self, fixture):
        """A real classify digest, so the test proves the seam between the two modules rather
        than a hand written dict that can agree with nothing."""
        digest = classify.classify(os.path.join(TRANSCRIPTS, fixture),
                                   SimpleNamespace(timed_out=False, exit_code=0))
        digest["task_id"] = "T-1"
        return digest

    def routable(self, digest, card_status="in review"):
        adapter = SimpleNamespace(status=lambda task_id: {"status": card_status})
        return runner._routable(self.manifest, adapter, digest, self.repo, "relay/T-1",
                                self.baseline_sha)

    def test_the_rescue_route_refuses_a_run_whose_evidence_could_not_be_read(self):
        self.stage_branch_with_commits()
        digest = self.digest_for("does-not-exist.jsonl")
        routable, note = self.routable(digest)
        self.assertFalse(routable, "unreadable evidence merged through the rescue route")
        self.assertIn("could not", note or "")

    def test_the_rescue_route_still_merges_a_readable_run_that_printed_no_envelope(self):
        """The narrowing is a narrowing, not a removal. This is the same branch, commits, and
        card as the case above, with evidence the runner actually opened."""
        self.stage_branch_with_commits()
        digest = self.digest_for("no_envelope.jsonl")
        self.assertEqual(digest["halt_class"], contracts.HALT_NO_ENVELOPE)
        routable, note = self.routable(digest)
        self.assertTrue(routable, note)
        self.assertIn("in review", note or "")

    def test_a_task_that_left_no_transcript_is_a_runner_fault_and_its_branch_is_not_merged(self):
        """End to end: the class on the record is the runner fault one, and the commits the
        process did leave behind stay on the stranded branch."""
        self.queue_entry(None, task_branch_sh("T-1"))
        self.closeout_blocked("T-1")
        self.task_success("T-2")
        self.closeout_landed("T-2")
        self.task_success("T-3")
        self.closeout_landed("T-3")

        outcome = self.go()
        self.assertEqual(outcome.exit_code, runner.EXIT_OK, outcome.message)
        record = self.store().get("T-1")
        self.assertEqual(record["halt_class"], contracts.HALT_UNEXPECTED_ERROR)
        self.assertIn("relay/T-1", self.relay_branches())
        self.assertNotIn("- [x] T-1 Add the brief renderer", self.tracker_at_remote())


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


class TerminalCard(RunCase):
    def test_a_card_that_is_already_terminal_is_excluded_instead_of_launched(self):
        """The first Cratekit run relaunched issue 62 after it had been closed by hand."""
        with open(os.path.join(self.repo, "tracker.md"), "w") as handle:
            handle.write(TRACKER_MD.replace("- [ ] T-1", "- [x] T-1"))
        _repo.git(self.repo, "add", "tracker.md")
        _repo.git(self.repo, "commit", "-q", "-m", "T-1 closed elsewhere")
        _repo.git(self.repo, "push", "-q", "origin", "main")
        self.task_success("T-2")
        self.closeout_landed("T-2")
        self.task_success("T-3")
        self.closeout_landed("T-3")

        outcome = self.go()
        self.assertEqual(outcome.exit_code, runner.EXIT_OK, outcome.message)
        record = self.store().get("T-1")
        self.assertEqual(record["status"], contracts.STATUS_EXCLUDED)
        self.assertIn("terminal", record["excluded_reason"])
        self.assertIsNone(record["session_id"], "a process was launched on a closed card")
        self.assertNotIn("relay/T-1", self.relay_branches())
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


class CustomPrefixEndToEnd(RunCase):
    def set_prefix(self, prefix):
        with open(self.manifest_path) as handle:
            text = handle.read()
        value = '""' if prefix == "" else '"%s"' % prefix
        lines = []
        replaced = False
        for line in text.splitlines(True):
            if line.startswith("branch_prefix ="):
                lines.append("branch_prefix = %s\n" % value)
                replaced = True
            else:
                lines.append(line)
        text = "".join(lines)
        if not replaced:
            text = text.replace("mirror = []", "mirror = []\nbranch_prefix = %s" % value)
        with open(self.manifest_path, "w") as handle:
            handle.write(text)
        self.manifest = mf.load(self.manifest_path)

    def test_a_custom_prefix_lands_and_strands_without_relay_slash(self):
        self.set_prefix("IW-")
        self.task_success("T-1")
        self.closeout_landed("T-1")
        self.task_blocked("T-2")
        self.closeout_blocked("T-2")
        self.task_success("T-3")
        self.closeout_landed("T-3")
        outcome = self.go()
        self.assertEqual(outcome.exit_code, runner.EXIT_OK, outcome.message)
        self.assertFalse(gitread.branch_exists(self.repo, "relay/T-1"))
        self.assertFalse(gitread.branch_exists(self.repo, "relay/T-2"))
        self.assertTrue(gitread.branch_exists(self.repo, "IW-T-2"))
        self.assertEqual(self.store().get("T-2")["branch"], "IW-T-2")

    def test_retry_blocked_names_the_recorded_prefix_branch(self):
        self.set_prefix("IW-")
        self.task_success("T-1")
        self.closeout_landed("T-1")
        self.task_blocked("T-2")
        self.closeout_blocked("T-2")
        self.task_success("T-3")
        self.closeout_landed("T-3")
        self.go()
        outcome = self.go(retry_blocked=True)
        self.assertEqual(outcome.exit_code, runner.EXIT_HALTED)
        self.assertIn("IW-T-2", outcome.message)
        self.assertNotIn("relay/T-2", outcome.message)

    def test_retry_still_sees_a_stranded_branch_after_a_prefix_edit(self):
        self.set_prefix("IW-")
        self.task_success("T-1")
        self.closeout_landed("T-1")
        self.task_blocked("T-2")
        self.closeout_blocked("T-2")
        self.task_success("T-3")
        self.closeout_landed("T-3")
        self.go()
        self.set_prefix("other/")
        outcome = self.go(retry_blocked=True)
        self.assertEqual(outcome.exit_code, runner.EXIT_HALTED)
        self.assertIn("IW-T-2", outcome.message)


class CustomPrefixRetry(RunCase):
    def test_retry_refuses_commits_on_the_manifest_prefix_branch(self):
        from test_gitwrite import commit_on_branch
        with open(self.manifest_path) as handle:
            text = handle.read().replace("mirror = []", 'mirror = []\nbranch_prefix = "IW-"')
        with open(self.manifest_path, "w") as handle:
            handle.write(text)
        self.manifest = mf.load(self.manifest_path)
        prefix = self.manifest.project.branch_prefix
        task_id = "T-1"
        branch = gitwrite.task_branch_for(task_id, prefix)
        baseline = gitread.rev_parse(self.repo, "main")
        commit_on_branch(self.repo, branch, {"src/t1.py": "x = 1\n"}, "work", base="main")
        _repo.git(self.repo, "checkout", "-q", "main")
        store = self.store()
        record = {"baseline_sha": baseline}
        with self.assertRaises(runner._Halt) as raised:
            runner._clear_blocked_branch(store, self.manifest.tasks[0], self.repo, record, None,
                                         branch)
        self.assertIn(branch, str(raised.exception))
        self.assertNotIn("relay/T-1", str(raised.exception))


if __name__ == "__main__":
    unittest.main()


class UnexpectedFailures(RunCase):
    """Every stop is supposed to be a named class carrying evidence. Before these fixes an
    unexpected exception, a git failure, or an unreadable card produced a traceback, a stuck
    record, or a launched task with no task text in its brief (review findings #4, #13, #8)."""

    def test_an_unexpected_exception_becomes_a_named_halt_not_a_traceback(self):
        class Exploding:
            def __getattr__(self, name):
                raise RuntimeError("the adapter blew up on %s" % name)

        outcome = self.go(adapter=Exploding())
        self.assertEqual(outcome.exit_code, runner.EXIT_HALTED)
        self.assertEqual(outcome.halt_class, contracts.HALT_UNEXPECTED_ERROR)
        self.assertIn("RuntimeError", outcome.message)
        record = self.store().get("T-1")
        self.assertEqual(record["status"], contracts.STATUS_HALTED)
        self.assertEqual(record["halt_evidence"]["error_type"], "RuntimeError")
        self.assertEqual(self.store().terminal()["run_status"], contracts.RUN_HALTED)
        self.assertIsNone(self.store().lease())

    def test_a_git_failure_becomes_a_named_halt_carrying_the_git_evidence(self):
        original = gitread.rev_parse

        def explode(repo, ref):
            if ref == "main":
                raise gitread.GitError(["git", "rev-parse", "main"], 128, "bad object main")
            return original(repo, ref)

        gitread.rev_parse = explode
        try:
            outcome = self.go()
        finally:
            gitread.rev_parse = original
        self.assertEqual(outcome.exit_code, runner.EXIT_HALTED)
        self.assertEqual(outcome.halt_class, contracts.HALT_UNCLEAN_EXIT)
        self.assertIn("bad object main", self.store().get("T-1")["halt_evidence"]["stderr"])

    def test_an_unreadable_tracker_card_excludes_the_task_instead_of_launching_it(self):
        class Unreadable:
            def __init__(self, real): self.real = real
            def read(self, task_id):
                if task_id == "T-1":
                    return {"id": task_id, "title": "", "description": "", "status": None,
                            "skipped": "gh exited 1: not authenticated"}
                return self.real.read(task_id)
            def __getattr__(self, name): return getattr(self.real, name)

        from relay import adapters
        self.task_success("T-2")
        self.closeout_landed("T-2")
        self.task_success("T-3")
        self.closeout_landed("T-3")
        outcome = self.go(adapter=Unreadable(adapters.build(self.manifest)))
        self.assertEqual(outcome.exit_code, runner.EXIT_OK, outcome.message)
        record = self.store().get("T-1")
        self.assertEqual(record["status"], contracts.STATUS_EXCLUDED)
        self.assertIn("not authenticated", record["excluded_reason"])
        self.assertIsNone(record["session_id"], "a process was launched on an unreadable card")
        self.assertEqual(self.store().get("T-2")["status"], contracts.STATUS_LANDED)


class LeaseOwnership(RunCase):
    """Two runners must never merge the same repo. The gate can outlast the lease TTL, so the
    tail carries its own heartbeat and refuses once the lease is gone (review findings #6, #1)."""

    def test_a_lease_lost_during_the_tail_refuses_to_merge(self):
        self.task_success("T-1")
        store = self.store()
        store.acquire()
        # Another runner takes the repo lease while this one is between steps.
        other = state.StateStore(os.path.join(self.tmp.name, "other.toml"), self.repo,
                                 home=self.home, pid=424242)
        with open(os.path.join(self.tmp.name, "other.toml"), "w") as handle:
            handle.write("# a second manifest naming the same repo\n")
        store.release()
        self.assertTrue(other.acquire().ok)
        outcome = self.go()
        self.assertEqual(outcome.exit_code, runner.EXIT_LEASE)
        self.assertEqual(gitread.rev_parse(self.repo, "origin/main"),
                         gitread.rev_parse(self.repo, "main"),
                         "a merge reached the remote while another runner held the repo lease")
        other.release()

    def test_the_tail_refuses_to_merge_once_the_heartbeat_reports_the_lease_is_gone(self):
        from relay import gitwrite as gw
        from test_gitwrite import commit_on_branch

        commit_on_branch(self.repo, "relay/T-1", {"src/t1.py": "value = 1\n"}, "T-1", base="main")
        _repo.git(self.repo, "checkout", "-q", "main")
        result = gw.local_merge_tail(
            self.repo, "T-1", "main", gitread.rev_parse(self.repo, "origin/main"),
            ["true"], os.path.join(self.tmp.name, "gate.log"), still_ours=lambda: False,
            branch=gitwrite.task_branch_for("T-1"))
        self.assertFalse(result.ok)
        self.assertEqual(result.halt_class, contracts.HALT_RUNNER_CRASHED)
        self.assertEqual(gitread.rev_parse(self.repo, "origin/main"),
                         gitread.rev_parse(self.repo, "main"))


class NoteHalt(RunCase):
    """U3, relay task 50: `_note_halt`'s four-check gate, exercised directly against a
    constructed `_Context` so each check is provable without driving a full task process
    through the stub. `_run_closeout` is replaced with a recorder rather than actually
    launched, since these tests are about whether it is called and with what, not about the
    closeout brief itself (covered in test_closeout.py)."""

    def ctx(self, branch="relay/T-1"):
        env = launch.child_env(self.manifest, self.base_env(), self.home)
        allowed = tuple(mf.completed_allowed_paths(self.manifest, mf.docs_root_for(self.repo)))
        store = self.store()
        store.acquire()
        task = self.manifest.tasks[0]
        card = {"id": "T-1", "title": "t", "description": "d"}
        launched = SimpleNamespace(wall_seconds=1, active_seconds=1)
        cfg = runner._Run(self.manifest, None, store, self.repo, "main", env, self.base_env(),
                          self.home, None, False, {}, {}, time.time, allowed)
        return runner._Context(task=task, card=card, branch=branch, baseline_sha=None,
                               baseline_comment_id=None, digest={}, launched=launched,
                               findings=[], **vars(cfg))

    def recorder(self):
        calls = []

        def fake(ctx, outcome, **kwargs):
            calls.append((outcome, kwargs))
        return calls, fake

    def test_a_run_scoped_class_never_launches_the_closeout(self):
        ctx = self.ctx()
        calls, fake = self.recorder()
        original = runner._run_closeout
        runner._run_closeout = fake
        try:
            runner._note_halt(ctx, runner._Halt("T-1", contracts.HALT_RUNNER_CRASHED, "m", {}))
        finally:
            runner._run_closeout = original
        self.assertEqual(calls, [])

    def test_a_closeout_misbehaved_class_never_launches_the_closeout(self):
        ctx = self.ctx()
        calls, fake = self.recorder()
        original = runner._run_closeout
        runner._run_closeout = fake
        try:
            runner._note_halt(
                ctx, runner._Halt("T-1", contracts.HALT_CLOSEOUT_OUT_OF_SCOPE, "m", {}))
        finally:
            runner._run_closeout = original
        self.assertEqual(calls, [])

    def test_a_dirty_tree_never_launches_the_closeout(self):
        ctx = self.ctx()
        with open(os.path.join(self.repo, "operator-wip.txt"), "w") as handle:
            handle.write("wip\n")
        calls, fake = self.recorder()
        original = runner._run_closeout
        runner._run_closeout = fake
        try:
            runner._note_halt(ctx, runner._Halt("T-1", contracts.HALT_GATE_REFUSED, "m", {}))
        finally:
            runner._run_closeout = original
        self.assertEqual(calls, [])
        os.remove(os.path.join(self.repo, "operator-wip.txt"))

    def test_a_local_default_ahead_of_origin_never_launches_the_closeout(self):
        """R6a: a merge already applied locally but never confirmed pushed."""
        from test_gitwrite import commit_on_branch

        commit_on_branch(self.repo, "main", {"src/unpushed.py": "value = 1\n"}, "unpushed",
                         base="main")
        ctx = self.ctx()
        calls, fake = self.recorder()
        original = runner._run_closeout
        runner._run_closeout = fake
        try:
            runner._note_halt(ctx, runner._Halt("T-1", contracts.HALT_GATE_REFUSED, "m", {}))
        finally:
            runner._run_closeout = original
        self.assertEqual(calls, [])

    def test_a_lost_lease_never_launches_the_closeout(self):
        """R6b: the same freshness check `_continue_past` already applies."""
        ctx = self.ctx()
        ctx.store.heartbeat = lambda: False
        calls, fake = self.recorder()
        original = runner._run_closeout
        runner._run_closeout = fake
        try:
            runner._note_halt(ctx, runner._Halt("T-1", contracts.HALT_GATE_REFUSED, "m", {}))
        finally:
            runner._run_closeout = original
        self.assertEqual(calls, [])

    def test_a_clean_task_scoped_halt_launches_the_closeout_with_class_and_cause(self):
        ctx = self.ctx()
        calls, fake = self.recorder()
        original = runner._run_closeout
        runner._run_closeout = fake
        try:
            runner._note_halt(
                ctx, runner._Halt("T-1", contracts.HALT_GATE_REFUSED, "gate refused relay/T-1", {}))
        finally:
            runner._run_closeout = original
        self.assertEqual(len(calls), 1)
        outcome, kwargs = calls[0]
        self.assertEqual(outcome, closeout.OUTCOME_HALTED)
        self.assertEqual(kwargs["halt_class"], contracts.HALT_GATE_REFUSED)
        self.assertEqual(kwargs["cause_line"], "gate refused relay/T-1")
        # R7: the repo returned to the default branch, stranding the task branch rather than
        # deleting it.
        self.assertEqual(gitread.current_branch(self.repo), "main")

    def test_a_failure_inside_the_halt_comment_never_escapes_or_replaces_the_halt(self):
        """R8: a second failure while commenting must not shadow the halt already raised."""
        ctx = self.ctx()
        original = runner._run_closeout

        def explode(ctx, outcome, **kwargs):
            raise RuntimeError("the closeout blew up")
        runner._run_closeout = explode
        try:
            halt = runner._Halt("T-1", contracts.HALT_GATE_REFUSED, "gate refused", {})
            runner._note_halt(ctx, halt)  # must not raise
            self.assertEqual(halt.halt_class, contracts.HALT_GATE_REFUSED)
            self.assertEqual(halt.message, "gate refused")
        finally:
            runner._run_closeout = original

    def test_a_check_that_raises_never_escapes_or_replaces_the_halt(self):
        """Code review finding (reliability): the four checks themselves, not only the closeout
        launch, must be inside the best-effort guard, or a git failure while checking would
        escape _note_halt and reach run()'s own except-Exception handler, which fabricates a
        new halt from that failure and masks the real one (R8, KTD4)."""
        ctx = self.ctx()
        original = gitread.is_clean
        gitread.is_clean = lambda repo: (_ for _ in ()).throw(RuntimeError("git blew up"))
        try:
            halt = runner._Halt("T-1", contracts.HALT_GATE_REFUSED, "gate refused", {})
            runner._note_halt(ctx, halt)  # must not raise
            self.assertEqual(halt.halt_class, contracts.HALT_GATE_REFUSED)
            self.assertEqual(halt.message, "gate refused")
        finally:
            gitread.is_clean = original

    def test_a_closeout_scope_reset_never_launches_a_second_closeout(self):
        """Code review finding (correctness): gitwrite.closeout_scope_check's own
        HALT_UNCLEAN_EXIT raise resets the tree before raising, so the tree-clean check alone
        would not catch it -- the evidence's reset_to key is the signal that this halt is the
        Closeout mechanism itself misbehaving, not an ordinary unclean exit."""
        ctx = self.ctx()
        calls, fake = self.recorder()
        original = runner._run_closeout
        runner._run_closeout = fake
        try:
            runner._note_halt(
                ctx, runner._Halt("T-1", contracts.HALT_UNCLEAN_EXIT, "closeout left work uncommitted",
                                  {"reset_to": "abc1234"}))
        finally:
            runner._run_closeout = original
        self.assertEqual(calls, [])

    def test_an_ordinary_unclean_exit_without_reset_to_still_launches_the_closeout(self):
        """The reset_to exclusion is narrow: an unrelated HALT_UNCLEAN_EXIT raise (no reset_to
        in its evidence) still gets the comment, since only the closeout-scope-check raise site
        carries that key."""
        ctx = self.ctx()
        calls, fake = self.recorder()
        original = runner._run_closeout
        runner._run_closeout = fake
        try:
            runner._note_halt(
                ctx, runner._Halt("T-1", contracts.HALT_UNCLEAN_EXIT, "left the tree dirty", {}))
        finally:
            runner._run_closeout = original
        self.assertEqual(len(calls), 1)

    def test_a_halt_after_landing_passes_the_landing_ref_through(self):
        """Code review finding (adversarial, agent-native): a halt raised after this task's own
        landed closeout already ran (a mirror push refusal or a failing final verify) must carry
        the landing reference through, so the comment reads as a post-landing failure rather than
        an undifferentiated halt on a card the runner already moved to a terminal status."""
        ctx = self.ctx()
        ctx.store.upsert("T-1", landing_ref="abc1234def")
        calls, fake = self.recorder()
        original = runner._run_closeout
        runner._run_closeout = fake
        try:
            runner._note_halt(
                ctx, runner._Halt("T-1", contracts.HALT_GATE_REFUSED,
                                  "the mirror push was refused for T-1", {}))
        finally:
            runner._run_closeout = original
        self.assertEqual(len(calls), 1)
        outcome, kwargs = calls[0]
        self.assertEqual(kwargs["landing_ref"], "abc1234def")

    def test_a_halt_before_landing_passes_no_landing_ref(self):
        ctx = self.ctx()
        calls, fake = self.recorder()
        original = runner._run_closeout
        runner._run_closeout = fake
        try:
            runner._note_halt(
                ctx, runner._Halt("T-1", contracts.HALT_GATE_REFUSED, "gate refused", {}))
        finally:
            runner._run_closeout = original
        self.assertEqual(len(calls), 1)
        outcome, kwargs = calls[0]
        self.assertIsNone(kwargs.get("landing_ref"))


GATE_REFUSES_SH = "test ! -e %s"

PRE_PUSH_REFUSES_TASK = """#!/bin/bash
# Refuse a push whose new commits mention the named task, and only that one.
while read local_ref local_sha remote_ref remote_sha; do
  if git log --format=%%s "$remote_sha..$local_sha" | grep -q "%s"; then
    echo "hook refused the push of %s" >&2
    exit 1
  fi
done
exit 0
"""


class ContinuePastHalt(RunCase):
    """Issue #15. With on_halt.continue_past_task_halt set, a halt contained to one task pauses
    that task and the run goes on; a run scoped halt, or a repo the next task could not start
    from, still stops the run exactly as before."""

    def opt_in(self, gate=None):
        text = MANIFEST.replace("__REPO__", self.repo)
        text += "\n[on_halt]\ncontinue_past_task_halt = true\n"
        if gate is not None:
            text = text.replace('command = ["true"]', "command = %s" % json.dumps(list(gate)))
        with open(self.manifest_path, "w") as handle:
            handle.write(text)
        self.manifest = mf.load(self.manifest_path)

    def test_a_refused_gate_pauses_the_task_and_the_later_tasks_land(self):
        self.opt_in(gate=["bash", "-c", GATE_REFUSES_SH % "src/t_2.py"])
        self.task_success("T-1")
        self.closeout_landed("T-1")
        self.task_success("T-2")
        self.closeout_halted("T-2")
        self.task_success("T-3")
        self.closeout_landed("T-3")

        outcome = self.go()
        self.assertEqual(outcome.exit_code, runner.EXIT_OK, outcome.message)
        records = self.store().records()
        self.assertEqual(records["T-1"]["status"], contracts.STATUS_LANDED)
        self.assertEqual(records["T-2"]["status"], contracts.STATUS_HALTED)
        self.assertEqual(records["T-2"]["halt_class"], contracts.HALT_GATE_REFUSED)
        self.assertTrue(records["T-2"]["continued_past"])
        self.assertEqual(records["T-3"]["status"], contracts.STATUS_LANDED)
        terminal = self.store().terminal()
        self.assertEqual(terminal["run_status"], contracts.RUN_COMPLETED)
        self.assertIsNone(terminal["halt_task"])
        self.assertEqual(gitread.current_branch(self.repo), "main")
        self.assertIn("relay/T-2", self.relay_branches(), "the paused task's branch was removed")

    def test_a_reclaim_then_a_real_continue_past_halt_leaves_one_true_terminal_record(self):
        """R1 at the integration level: the stale lease reclaim (U1) must not leave a phantom
        terminal record behind. T-1's stale lease is reclaimed (marking it halted/runner_crashed
        internally), then T-1 is retried fresh and halts for real on a refused gate, and since
        continue_past_task_halt is opted in, the run continues past it and completes. The run's
        own terminal record must be the real completion, never the reclaim's crash marking."""
        self.opt_in(gate=["bash", "-c", GATE_REFUSES_SH % "src/t_1.py"])
        self.seed_stale_reclaim_on_t1()
        self.task_success("T-1")
        self.closeout_halted("T-1")
        self.task_success("T-2")
        self.closeout_landed("T-2")
        self.task_success("T-3")
        self.closeout_landed("T-3")

        store = self.reclaiming_store()
        result = store.acquire()
        self.assertEqual(result.code, state.STALE_RECLAIMED)
        self.assertIsNone(store.terminal(), "the reclaim itself must not write a run-level "
                          "terminal record before the run decides its own ending (R1)")

        outcome = self.go(store=store)
        self.assertEqual(outcome.exit_code, runner.EXIT_OK, outcome.message)
        self.assertEqual(self.store().get("T-1")["halt_class"], contracts.HALT_GATE_REFUSED)
        self.assertTrue(self.store().get("T-1")["continued_past"])
        terminal = self.store().terminal()
        self.assertEqual(terminal["run_status"], contracts.RUN_COMPLETED)
        self.assertIsNone(terminal["halt_task"])

    def test_a_reclaim_then_a_real_full_stop_halt_names_the_real_halt_not_the_reclaim(self):
        """Same seeded reclaim and gate as above, but continue_past_task_halt is not opted in.
        The run halts for real on T-1's gate refusal, and the terminal record must name that
        real halt, not the reclaim's phantom runner_crashed marking. This proves the fix does
        not change whether the run continues (issue #15's separate logic), only that the
        transient reclaim marking never leaks into the run-level terminal record either way."""
        text = MANIFEST.replace("__REPO__", self.repo).replace(
            'command = ["true"]', "command = %s" % json.dumps(["bash", "-c", GATE_REFUSES_SH % "src/t_1.py"]))
        with open(self.manifest_path, "w") as handle:
            handle.write(text)
        self.manifest = mf.load(self.manifest_path)
        self.seed_stale_reclaim_on_t1()
        self.task_success("T-1")
        self.closeout_halted("T-1")

        store = self.reclaiming_store()
        result = store.acquire()
        self.assertEqual(result.code, state.STALE_RECLAIMED)
        self.assertIsNone(store.terminal(), "the reclaim itself must not write a run-level "
                          "terminal record before the run decides its own ending (R1)")

        outcome = self.go(store=store)
        self.assertEqual(outcome.exit_code, runner.EXIT_HALTED, outcome.message)
        self.assertEqual(outcome.halt_task, "T-1")
        terminal = self.store().terminal()
        self.assertEqual(terminal["halt_task"], "T-1")
        self.assertEqual(terminal["halt_class"], contracts.HALT_GATE_REFUSED)

    def test_a_dirty_timeout_still_stops_and_names_the_refused_check(self):
        self.opt_in()
        self.task_success("T-1")
        self.closeout_landed("T-1")
        self.queue_entry("success.jsonl", DIRTY_AND_HANG_SH)
        self.task_success("T-3")

        outcome = self.go(timeout_overrides={"task_seconds": 2})
        self.assertEqual(outcome.exit_code, runner.EXIT_HALTED, outcome.message)
        self.assertEqual(outcome.halt_task, "T-2")
        record = self.store().get("T-2")
        self.assertEqual(record["halt_class"], contracts.HALT_TIMEOUT)
        self.assertFalse(record.get("continued_past"))
        self.assertEqual(record["halt_evidence"]["resume"]["check"], "tree_clean")
        self.assertIsNone(self.store().get("T-3"))

    def test_opted_out_a_halt_records_no_disposition(self):
        self.task_success("T-1")
        self.closeout_landed("T-1")
        self.queue_entry("success.jsonl", DIRTY_AND_HANG_SH)
        outcome = self.go(timeout_overrides={"task_seconds": 2})
        self.assertEqual(outcome.exit_code, runner.EXIT_HALTED)
        record = self.store().get("T-2")
        self.assertNotIn("resume", record["halt_evidence"])
        self.assertFalse(record.get("continued_past"))

    def test_a_run_scoped_halt_stops_without_consulting_the_repo(self):
        class Exploding:
            def __getattr__(self, name):
                raise RuntimeError("the adapter blew up on %s" % name)

        self.opt_in()
        outcome = self.go(adapter=Exploding())
        self.assertEqual(outcome.exit_code, runner.EXIT_HALTED)
        self.assertEqual(outcome.halt_class, contracts.HALT_UNEXPECTED_ERROR)
        record = self.store().get("T-1")
        self.assertNotIn("resume", record["halt_evidence"])
        self.assertFalse(record.get("continued_past"))

    def test_two_paused_tasks_around_a_landed_one_still_complete_the_run(self):
        self.opt_in(gate=["bash", "-c", "test ! -e src/t_1.py -a ! -e src/t_3.py"])
        self.task_success("T-1")
        self.closeout_halted("T-1")
        self.task_success("T-2")
        self.closeout_landed("T-2")
        self.task_success("T-3")
        self.closeout_halted("T-3")

        outcome = self.go()
        self.assertEqual(outcome.exit_code, runner.EXIT_OK, outcome.message)
        records = self.store().records()
        for task_id in ("T-1", "T-3"):
            self.assertEqual(records[task_id]["status"], contracts.STATUS_HALTED)
            self.assertTrue(records[task_id]["continued_past"])
        self.assertEqual(records["T-2"]["status"], contracts.STATUS_LANDED)
        self.assertEqual(self.store().terminal()["run_status"], contracts.RUN_COMPLETED)

    def test_a_paused_task_is_retried_on_the_next_run_with_no_new_flag(self):
        self.opt_in(gate=["bash", "-c", GATE_REFUSES_SH % "src/t_2.py"])
        self.task_success("T-1")
        self.closeout_landed("T-1")
        self.task_success("T-2")
        self.closeout_halted("T-2")
        self.task_success("T-3")
        self.closeout_landed("T-3")
        first = self.go()
        self.assertEqual(first.exit_code, runner.EXIT_OK, first.message)

        # The operator repairs the gate and discards the stranded branch, as for any halt.
        _repo.git(self.repo, "branch", "-D", "relay/T-2")
        self.opt_in()
        self.task_success("T-2")
        self.closeout_landed("T-2")
        second = self.go()

        self.assertEqual(second.exit_code, runner.EXIT_OK, second.message)
        record = self.store().get("T-2")
        self.assertEqual(record["status"], contracts.STATUS_LANDED)
        self.assertFalse(record.get("continued_past"))

    def test_a_push_refused_after_the_merge_stops_because_the_default_is_ahead(self):
        self.opt_in()
        hook = os.path.join(self.repo, ".git", "hooks", "pre-push")
        with open(hook, "w") as handle:
            handle.write(PRE_PUSH_REFUSES_TASK % ("T-2", "T-2"))
        os.chmod(hook, 0o755)
        self.task_success("T-1")
        self.closeout_landed("T-1")
        self.task_success("T-2")
        self.task_success("T-3")

        outcome = self.go()
        self.assertEqual(outcome.exit_code, runner.EXIT_HALTED, outcome.message)
        self.assertEqual(outcome.halt_task, "T-2")
        record = self.store().get("T-2")
        self.assertEqual(record["halt_class"], contracts.HALT_GATE_REFUSED)
        self.assertEqual(record["halt_evidence"]["resume"]["check"], "head_equals_remote")
        self.assertFalse(record.get("continued_past"))
        self.assertIsNone(self.store().get("T-3"))


class ContinuePastGuards(RunCase):
    """_continue_past directly: the two refusals that never reach the disposition, and the two
    exception paths that must halt safely rather than escape the loop. Review findings: a
    stranded task branch from an earlier continued-past halt must not be stepped over again
    (silently repeating "completed" forever), and the checkout inside the disposition must not
    fire once the lease is gone."""

    def cfg(self, store=None):
        env = launch.child_env(self.manifest, self.base_env(), self.home)
        allowed = tuple(mf.completed_allowed_paths(self.manifest, mf.docs_root_for(self.repo)))
        return runner._Run(self.manifest, None, store or self.store(), self.repo, "main", env,
                           self.base_env(), self.home, None, False, {}, {}, time.time, allowed)

    def halt(self, evidence=None):
        return runner._Halt("T-1", contracts.HALT_UNCLEAN_EXIT, "pre flight refused",
                            evidence or {})

    def opt_in(self):
        text = MANIFEST.replace("__REPO__", self.repo) + "\n[on_halt]\ncontinue_past_task_halt = true\n"
        with open(self.manifest_path, "w") as handle:
            handle.write(text)
        self.manifest = mf.load(self.manifest_path)

    def test_a_no_task_branch_refusal_never_reaches_the_disposition(self):
        self.opt_in()
        store = self.store()
        store.acquire()
        h = self.halt({"branch": "relay/T-1", "check": "no_task_branch"})
        self.assertFalse(runner._continue_past(self.cfg(store), h))
        self.assertEqual(h.evidence["resume"], {"check": "no_task_branch"})

    def test_a_lost_lease_refuses_before_the_checkout(self):
        self.opt_in()
        store = self.store()
        store.acquire()
        other = state.StateStore(os.path.join(self.tmp.name, "other.toml"), self.repo,
                                 home=self.home, pid=424242)
        with open(os.path.join(self.tmp.name, "other.toml"), "w") as handle:
            handle.write("# a second manifest naming the same repo\n")
        store.release()
        self.assertTrue(other.acquire().ok)
        h = self.halt({"branch": "relay/T-1", "check": "tree_clean"})
        self.assertFalse(runner._continue_past(self.cfg(store), h))
        self.assertEqual(h.evidence["resume"], {"check": "lease_lost"})
        other.release()

    def test_a_git_error_from_the_disposition_halts_rather_than_escaping(self):
        self.opt_in()
        store = self.store()
        store.acquire()

        def explode(repo, default_branch, ops=None, task_id=None, env=None):
            raise gitread.GitError(["git", "checkout", "main"], 128, "bad ref main")

        original = gitwrite.resume_disposition
        gitwrite.resume_disposition = explode
        try:
            h = self.halt({"branch": "relay/T-1", "check": "tree_clean"})
            self.assertFalse(runner._continue_past(self.cfg(store), h))
        finally:
            gitwrite.resume_disposition = original
        self.assertEqual(h.evidence["resume"]["check"], "git_error")
        self.assertIn("bad ref main", h.evidence["resume"]["stderr"])

    def test_an_unexpected_exception_from_the_disposition_halts_rather_than_escaping(self):
        self.opt_in()
        store = self.store()
        store.acquire()

        def explode(repo, default_branch, ops=None, task_id=None, env=None):
            raise RuntimeError("the disposition blew up")

        original = gitwrite.resume_disposition
        gitwrite.resume_disposition = explode
        try:
            h = self.halt({"branch": "relay/T-1", "check": "tree_clean"})
            self.assertFalse(runner._continue_past(self.cfg(store), h))
        finally:
            gitwrite.resume_disposition = original
        self.assertEqual(h.evidence["resume"]["check"], "unexpected_error")
        self.assertEqual(h.evidence["resume"]["error_type"], "RuntimeError")


class ContinuePastWithoutRepair(RunCase):
    """The end-to-end regression for the bug the adversarial and correctness reviews both
    found: a continued-past task's own branch is never deleted by this feature, so unless the
    operator repairs it by hand (as they already must for any halt), the next run's pre flight
    refuses it on no_task_branch. That refusal must stop the run, not repeat "continued past"
    forever while reporting completed."""

    def test_a_second_run_with_the_branch_still_in_place_halts_instead_of_looping_green(self):
        text = MANIFEST.replace("__REPO__", self.repo).replace(
            'command = ["true"]', 'command = %s' % json.dumps(["bash", "-c", GATE_REFUSES_SH % "src/t_2.py"]))
        text += "\n[on_halt]\ncontinue_past_task_halt = true\n"
        with open(self.manifest_path, "w") as handle:
            handle.write(text)
        self.manifest = mf.load(self.manifest_path)

        self.task_success("T-1")
        self.closeout_landed("T-1")
        self.task_success("T-2")
        self.closeout_halted("T-2")
        self.task_success("T-3")
        self.closeout_landed("T-3")
        first = self.go()
        self.assertEqual(first.exit_code, runner.EXIT_OK, first.message)
        self.assertTrue(self.store().get("T-2")["continued_past"])
        self.assertTrue(gitread.branch_exists(self.repo, "relay/T-2"))

        second = self.go()
        self.assertEqual(second.exit_code, runner.EXIT_HALTED, second.message)
        self.assertEqual(second.halt_task, "T-2")
        record = self.store().get("T-2")
        self.assertEqual(record["halt_evidence"]["resume"], {"check": "no_task_branch"})
        # Unchanged from before this feature existed: the operator's repair is to delete the
        # stranded branch and fix what refused the gate, exactly as ResumeAfterHalt already
        # proves for a full-stop halt.
        _repo.git(self.repo, "branch", "-D", "relay/T-2")
        with open(self.manifest_path, "w") as handle:
            handle.write(MANIFEST.replace("__REPO__", self.repo)
                        + "\n[on_halt]\ncontinue_past_task_halt = true\n")
        self.manifest = mf.load(self.manifest_path)
        self.task_success("T-2")
        self.closeout_landed("T-2")
        third = self.go()
        self.assertEqual(third.exit_code, runner.EXIT_OK, third.message)
        self.assertEqual(self.store().get("T-2")["status"], contracts.STATUS_LANDED)


CODEX_LAST_MESSAGE = os.path.join(_paths.FIXTURES_DIR, "backends", "codex",
                                  "last-message-complete.txt")


class UnenforcedRun(RunCase):
    """Backends U10: record, bound, and audit on a Codex Task."""

    def load_codex(self, bound=None):
        bound = bound or ["src/"]
        text = MANIFEST.replace("__REPO__", self.repo)
        text = text.replace("[permissions]",
                            "[permissions]\n"
                            "unenforced_acceptance = \"fixture: operator accepts unenforced Codex\"\n"
                            "task_allowed_paths = %s" % json.dumps(bound), 1)
        text = text.replace('id = "T-1"', 'id = "T-1"\nbackend = "codex"', 1)
        # One Task: drop T-2 and T-3 so the queue is a single Task plus its Closeout.
        text = text.split("[[tasks]]")[0] + "[[tasks]]" + text.split("[[tasks]]")[1]
        with open(self.manifest_path, "w") as handle:
            handle.write(text)
        self.manifest = mf.load(self.manifest_path)

    def stream(self, command):
        path = os.path.join(self.tmp.name, "codex-stream.jsonl")
        event = {"type": "item.completed", "item": {
            "id": "item_1", "type": "command_execution", "command": command,
            "aggregated_output": "ok\n", "exit_code": 0, "status": "completed"}}
        with open(path, "w") as handle:
            handle.write(json.dumps(event) + "\n")
        return path

    def queue_codex(self, command="/bin/zsh -lc 'pwd'", git_sh=None):
        self.queue_entry(CODEX_LAST_MESSAGE, git_sh or task_branch_sh("T-1"),
                         stream=self.stream(command))

    def test_a_claude_task_is_unaffected_by_the_bound_and_the_audit(self):
        text = MANIFEST.replace("__REPO__", self.repo)
        text = text.replace("[permissions]",
                            "[permissions]\n"
                            "unenforced_acceptance = \"fixture: operator accepts unenforced Codex\"\n"
                            "task_allowed_paths = [\"docs/\"]", 1)
        text = text.split("[[tasks]]")[0] + "[[tasks]]" + text.split("[[tasks]]")[1]
        with open(self.manifest_path, "w") as handle:
            handle.write(text)
        self.manifest = mf.load(self.manifest_path)
        self.task_success("T-1")
        self.closeout_landed("T-1")
        outcome = self.go()
        self.assertEqual(outcome.exit_code, runner.EXIT_OK, outcome.message)
        record = self.store().get("T-1")
        self.assertEqual(record["status"], contracts.STATUS_LANDED)
        self.assertNotIn("unenforced_restrictions", record)
        self.assertNotIn(contracts.UNENFORCED_DISALLOWED,
                         [f["class"] for f in record.get("findings") or []])

    def test_a_commit_outside_the_bound_halts_and_the_branch_survives(self):
        self.load_codex(bound=["docs/"])
        self.queue_codex()
        origin = gitread.rev_parse(self.repo, "origin/main")
        outcome = self.go()
        self.assertEqual(outcome.exit_code, runner.EXIT_HALTED, outcome.message)
        record = self.store().get("T-1")
        self.assertEqual(record["halt_class"], contracts.HALT_PATH_GATE)
        self.assertIn("src/t_1.py", record["halt_evidence"]["detail"])
        self.assertIn("relay/T-1", self.relay_branches())
        self.assertEqual(gitread.rev_parse(self.repo, "origin/main"), origin)
        self.assertIsInstance(record.get("unenforced_restrictions"), str)

    def test_a_non_destructive_disallowed_call_lands_with_a_finding(self):
        self.load_codex(bound=["src/"])
        self.queue_codex("/bin/zsh -lc 'pwd && git clean -fd'")
        self.closeout_landed("T-1")
        outcome = self.go()
        self.assertEqual(outcome.exit_code, runner.EXIT_OK, outcome.message)
        record = self.store().get("T-1")
        self.assertEqual(record["status"], contracts.STATUS_LANDED)
        hits = [f for f in record["findings"] if f["class"] == contracts.UNENFORCED_DISALLOWED]
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["pattern"], "Bash(git clean*)")
        self.assertIn("git clean", record["unenforced_restrictions"])

    def test_a_clean_audit_still_records_the_detection_bound(self):
        """Round eight #54, from relay proof T-65. The empty findings list is the case the
        caveat exists for: T-65 ran a disallowed operation by an unmatched spelling and the
        audit correctly found nothing, which read as a clean run. The scalar is written off
        `enforces_at_launch`, not off a finding, so it is present here too, and the summary
        carries it beside the same empty list."""
        self.load_codex(bound=["src/"])
        self.queue_codex()
        self.closeout_landed("T-1")
        outcome = self.go()
        self.assertEqual(outcome.exit_code, runner.EXIT_OK, outcome.message)
        record = self.store().get("T-1")
        self.assertEqual(record["status"], contracts.STATUS_LANDED)
        self.assertEqual([f for f in record["findings"]
                          if f["class"] == contracts.UNENFORCED_DISALLOWED], [])
        scalar = record["unenforced_restrictions"]
        self.assertIn("git clean", scalar)
        # Both halves of the claim, not just the conclusion. Pinning only the conclusion would
        # let the sentences that say what was actually checked be dropped while the suite stays
        # green, and those are what an operator acts on.
        self.assertIn("matches command spellings", scalar)
        self.assertIn("does not prove the operation was avoided", scalar)
        self.assertNotIn("\n", scalar)

    def test_the_scalar_names_the_sandbox_network_grant(self):
        """Issue #51. SKILL.md discloses the grant when a manifest is authored, which never
        reaches an operator running a manifest written before the grant existed, and `validate`
        only checks that the acceptance sentence is non empty. The record is what every run
        writes, so the condition has to be stated here too."""
        self.load_codex(bound=["src/"])
        self.queue_codex()
        self.closeout_landed("T-1")
        outcome = self.go()
        self.assertEqual(outcome.exit_code, runner.EXIT_OK, outcome.message)
        scalar = self.store().get("T-1")["unenforced_restrictions"]
        self.assertIn("network", scalar)
        self.assertIn("not only the tracker", scalar)
        # Host scope alone understates it. The reach is held with the operator's own account
        # scoped credential, which is the half an acceptance sentence has to cover.
        self.assertIn("gh login", scalar)
        # Still one line: summary.line_fields hoists this into the namespace cause_line formats
        # halt templates against.
        self.assertNotIn("\n", scalar)

    def test_an_unenforced_backend_without_a_network_grant_gets_no_network_clause(self):
        """The clause is written off the capability's own `grants_network`, not off
        `enforces_at_launch`, so a future unenforced backend that reaches no network does not
        inherit a sentence that is false for it."""
        text = MANIFEST.replace("__REPO__", self.repo)
        text = text.replace("[permissions]",
                            "[permissions]\n"
                            "unenforced_acceptance = \"fixture\"\n"
                            "task_allowed_paths = [\"src/\"]", 1)
        with open(self.manifest_path, "w") as handle:
            handle.write(text)
        manifest = mf.load(self.manifest_path)
        fenced = replace(backends.build("codex").CAPABILITY, grants_network=False)
        scalar = runner._unenforced_scalar(manifest, fenced)
        self.assertIn("disallowed tools not enforced at launch", scalar)
        self.assertNotIn("network", scalar)

    def test_a_destructive_call_refuses_the_landing(self):
        self.load_codex(bound=["src/"])
        self.queue_codex("/bin/zsh -lc 'ls && rm -rf /tmp/x'")
        origin = gitread.rev_parse(self.repo, "origin/main")
        outcome = self.go()
        self.assertEqual(outcome.exit_code, runner.EXIT_HALTED, outcome.message)
        record = self.store().get("T-1")
        self.assertEqual(record["halt_class"], contracts.HALT_UNEXPECTED_ERROR)
        self.assertEqual(record["halt_evidence"]["error_type"], "destructive_call")
        self.assertIn("relay/T-1", self.relay_branches())
        self.assertEqual(gitread.rev_parse(self.repo, "origin/main"), origin)


class PhaseEvents(RunCase):
    """Issue #44, U2: the Runner's own phase events, and their isolation from the run.

    A run launched bare with `--detach` reaches the operator through nothing today. These cases
    pin what the Runner announces and, more importantly, that announcing it cannot change what
    the run decides.
    """

    def setUp(self):
        super().setUp()
        self.task_success("T-1")
        self.closeout_landed("T-1")
        self.task_blocked("T-2")
        self.closeout_blocked("T-2")
        self.task_success("T-3")
        self.closeout_landed("T-3")
        self.seen = []

    def notifier(self, body):
        self.seen.append(body)

    def test_every_status_move_is_announced_in_order(self):
        outcome = self.go(notifier=self.notifier)
        self.assertEqual(outcome.exit_code, runner.EXIT_OK, outcome.message)
        moves = [line for line in self.seen if " is now " in line]
        self.assertEqual(moves[0], "T-1 is now running")
        self.assertIn("T-1 is now landed", moves)
        self.assertIn("T-2 is now blocked", moves)
        self.assertIn("T-3 is now landed", moves)
        # Per task, in the order the runner wrote them, never interleaved: the runner is serial.
        for task_id in ("T-1", "T-2", "T-3"):
            mine = [line for line in moves if line.startswith(task_id)]
            self.assertEqual(mine[0], "%s is now running" % task_id)

    def test_the_last_announcement_names_the_run_status_and_its_counts(self):
        self.go(notifier=self.notifier)
        last = self.seen[-1]
        self.assertIn(contracts.RUN_COMPLETED, last)
        self.assertIn("2 landed", last)
        self.assertIn("1 blocked", last)

    def test_the_phase_lines_reach_the_stream_with_no_notifier_at_all(self):
        lines = []
        outcome = self.go(stream=lines.append)
        self.assertEqual(outcome.exit_code, runner.EXIT_OK, outcome.message)
        self.assertIn("T-1 is now running", lines)
        self.assertIn("T-3 is now landed", lines)

    def test_a_notifier_that_always_raises_changes_nothing_about_the_run(self):
        """KTD5's first half. The runner is unattended: a desktop that refuses a notification
        must not be able to halt a run or lose a landing."""
        def boom(_body):
            raise RuntimeError("the desktop said no")

        outcome = self.go(notifier=boom)
        self.assertEqual(outcome.exit_code, runner.EXIT_OK, outcome.message)
        records = self.store().records()
        self.assertEqual(records["T-1"]["status"], contracts.STATUS_LANDED)
        self.assertEqual(records["T-2"]["status"], contracts.STATUS_BLOCKED)
        self.assertEqual(records["T-3"]["status"], contracts.STATUS_LANDED)
        self.assertEqual(self.store().terminal()["run_status"], contracts.RUN_COMPLETED)

    def test_a_stream_that_raises_on_a_phase_line_changes_nothing_about_the_run(self):
        """Scoped to the phase lines this unit adds. `launch.launch` writes the task's own
        stream json through the same callable and has never been guarded, so a stream that
        raises on everything is a pre-existing failure, not one to pin here."""
        def boom(line):
            if " is now " in line or line.startswith("run "):
                raise RuntimeError("the pipe closed")

        outcome = self.go(stream=boom, notifier=self.notifier)
        self.assertEqual(outcome.exit_code, runner.EXIT_OK, outcome.message)
        self.assertEqual(self.store().get("T-3")["status"], contracts.STATUS_LANDED)
        # The stream failing does not cost the notification.
        self.assertIn("T-1 is now running", self.seen)

    def test_a_none_stream_still_notifies_and_raises_nothing(self):
        """`run.run` guards `stream is not None` at six call sites, so None is a supported
        caller. The announce path has to tolerate it rather than lean on the exception guard,
        which would swallow the notification along with the TypeError."""
        outcome = self.go(stream=None, notifier=self.notifier)
        self.assertEqual(outcome.exit_code, runner.EXIT_OK, outcome.message)
        self.assertIn("T-1 is now running", self.seen)

    def test_a_caller_supplied_store_still_emits(self):
        store = self.store()
        self.go(store=store, notifier=self.notifier)
        self.assertIn("T-1 is now running", self.seen)

    def test_no_notifier_fires_nothing(self):
        self.go()
        self.assertEqual(self.seen, [])


class PhaseEventsOnReclaim(RunCase):
    """The reclaim is the strongest signal an unattended operator can get, and it is written by
    `_mark_crashed` rather than by the run loop, so it only reaches the operator if the observer
    is attached before `acquire()`."""

    def test_a_stale_lease_reclaim_is_announced(self):
        self.seed_stale_reclaim_on_t1()
        self.task_success("T-1")
        self.closeout_landed("T-1")
        self.task_blocked("T-2")
        self.closeout_blocked("T-2")
        self.task_success("T-3")
        self.closeout_landed("T-3")
        seen = []
        self.go(store=self.reclaiming_store(), notifier=seen.append)
        self.assertIn("T-1 is now halted", seen)


class PhaseEventsOnHalt(RunCase):
    """The ending an operator most needs on their desktop. A dirty-tree timeout is run scoped,
    so the run stops rather than stepping past it, and the last announcement is the one thing
    that says so to somebody who is not watching."""

    def test_a_halted_run_names_the_halt_and_its_counts(self):
        self.task_success("T-1")
        self.closeout_landed("T-1")
        self.queue_entry("success.jsonl", DIRTY_AND_HANG_SH)
        seen = []
        outcome = self.go(timeout_overrides={"task_seconds": 2}, notifier=seen.append)
        self.assertEqual(outcome.exit_code, runner.EXIT_HALTED, outcome.message)
        last = seen[-1]
        self.assertIn(contracts.RUN_HALTED, last)
        self.assertIn("1 landed", last)
        self.assertIn("halted on T-2", last)
        self.assertIn(contracts.HALT_TIMEOUT, last)
