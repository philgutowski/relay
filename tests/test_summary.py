"""U10: the cause line, which is the one sentence an operator reads when a task did not land.

A cause line is a template in `contracts.HALT_LINES` filled from the evidence its raiser
recorded. Nothing at the raising site checks that the evidence carries the keys the template
names, and `summary.cause_line` swallows the mismatch on purpose: a missing key renders as `?`
rather than raising, so a half filled record still produces a readable line. That safety net is
also how several classes shipped rendering a placeholder where the evidence should have been.

This module is the table that closes the gap. One row per class in `HALT_LINES`, carrying the
evidence its production raiser records, cited by file and function. Rendering every row through
the real summary and refusing a surviving `?` fails the moment a template names a key no raiser
supplies, or a raiser renames a key a template still names.

Keep the rows honest. A row is a copy of what the cited raiser passes, not what would make the
line read nicely. If you change a raiser's evidence keys, change its row in the same commit.
"""
import json
import os
import tempfile
import unittest

import _paths
from relay import contracts, state, summary
from test_run import RunCase


class _Task:
    def __init__(self, task_id):
        self.id = task_id


class _Project:
    def __init__(self, repo):
        self.repo = repo


class _Manifest:
    """The three attributes `summary.build` reads. A real manifest needs a real repo on disk,
    which these rows deliberately do not have: the point is the record, not the repository."""

    def __init__(self, path, repo, task_ids):
        self.path = path
        self.project = _Project(repo)
        self.tasks = [_Task(task_id) for task_id in task_ids]


# Evidence a raiser records for a class that becomes the record's own `halt_class`. Each row is
# `class: (evidence, extra record fields, where it is raised)`.
RECORD_ROWS = {
    contracts.HALT_LANDED: (
        {},
        {"status": contracts.STATUS_LANDED, "landing_ref": "b" * 40, "branch": None},
        "run._merge_route, the landing upsert",
    ),
    contracts.HALT_BLOCKED_ENVELOPE: (
        {"stranded_head": "c" * 40, "blocker": "the API contract is undecided"},
        {"status": contracts.STATUS_BLOCKED, "branch": "relay/T-1"},
        "run._blocked_route",
    ),
    contracts.HALT_NO_ENVELOPE: (
        {"stranded_head": "c" * 40, "blocker": "no blocker text in the envelope",
         "last_message": "I have stopped rather than working around the denial."},
        {"status": contracts.STATUS_BLOCKED, "branch": "relay/T-1"},
        "run._blocked_route, class from the digest",
    ),
    contracts.HALT_PATH_GATE: (
        {"paths": [".claude/skills/x/SKILL.md"], "branch": "relay/T-1"},
        {"status": contracts.STATUS_BLOCKED, "branch": "relay/T-1"},
        "gitwrite.local_merge_tail, the backstop refusal",
    ),
    contracts.HALT_REMOTE_ADVANCED: (
        {"remote_sha": "d" * 40, "baseline_sha": "e" * 40, "sha": "d" * 40,
         "branch": "relay/T-1"},
        {"status": contracts.STATUS_HALTED, "branch": "relay/T-1"},
        "gitwrite.local_merge_tail, the fetch and merge refusals",
    ),
    contracts.HALT_CLOSEOUT_OUT_OF_SCOPE: (
        {"path": "src/unrelated.py", "allowed": "docs/solutions",
         "offending": ["src/unrelated.py"], "reset_to": "f" * 40},
        {"status": contracts.STATUS_HALTED, "branch": "relay/T-1"},
        "run._run_closeout, the scope refusal",
    ),
    contracts.HALT_RUNNER_CRASHED: (
        {"status_before": contracts.STATUS_MERGING, "previous_holder": {"holder_pid": 4242},
         "last_git_op": None},
        {"status": contracts.STATUS_HALTED, "branch": "relay/T-1"},
        "state._mark_crashed, and the lease loss branches in run and gitwrite",
    ),
    contracts.HALT_GATE_REFUSED: (
        {"branch": "relay/T-1", "sha": "a" * 40, "log": "/state/gate/T-1.log", "returncode": 1},
        {"status": contracts.STATUS_HALTED, "branch": "relay/T-1"},
        "gitwrite.local_merge_tail, the gate and push refusals",
    ),
    contracts.HALT_PARTIAL_LANDING: (
        {"sha": "a" * 40, "card_status": "in progress", "checks": {"card_terminal": {}}},
        {"status": contracts.STATUS_HALTED, "landing_ref": "a" * 40},
        "run._merge_route, the full scope verdict",
    ),
    contracts.HALT_TIMEOUT: (
        {"tree": "dirty", "branch": "relay/T-1", "active_seconds": 3600.0,
         "wall_seconds": 3720.0, "active_minutes": 60, "wall_minutes": 62},
        {"status": contracts.STATUS_HALTED, "branch": "relay/T-1"},
        "run._timeout_route",
    ),
    contracts.HALT_UNCLEAN_EXIT: (
        {"branch": "relay/T-1", "baseline_sha": "e" * 40},
        {"status": contracts.STATUS_HALTED, "branch": "relay/T-1"},
        "run._one_task pre flight, and gitwrite.local_merge_tail",
    ),
    contracts.HALT_CI_UNDECIDED: (
        {"url": "https://example.invalid/pull/7", "minutes": 30},
        {"status": contracts.STATUS_HALTED, "branch": "relay/T-1"},
        "the pr_terminal route, which is not wired into the run loop yet",
    ),
    contracts.HALT_UNEXPECTED_ERROR: (
        {"task": "T-1", "error_type": "KeyError", "error": "'sha'"},
        {"status": contracts.STATUS_HALTED},
        "run.run, the catch all handler",
    ),
}

# Evidence a raiser records for a class that attaches to a record as a finding rather than
# becoming its class. Findings render from the finding dict alone, with no record behind them.
FINDING_ROWS = {
    contracts.HALT_DENIED_TOOL: (
        {"tool": "Edit", "target": "src/thing.py", "line": 91, "tool_use_line": 88},
        "classify.classify, the denial scan",
    ),
    contracts.HALT_TRACKER_WRITE_DENIED: (
        {"tool": "mcp__atlassian__transitionJiraIssue", "target": "T-1", "line": 91,
         "tool_use_line": 88},
        "classify.classify, a denial matching a tracker write pattern",
    ),
    contracts.HALT_SKILL_SUBSTITUTION: (
        {"name": "code-review", "required": "compound-engineering:ce-code-review", "line": 44},
        "classify.classify, the Skill call scan",
    ),
    contracts.HALT_NO_ENVELOPE: (
        {"last_message": "I have stopped rather than working around the denial."},
        "classify.classify, kept as a finding when the envelope is absent",
    ),
    contracts.CLOSEOUT_UNFINISHED: (
        {"task": "T-1", "last_message": "(no final message)"},
        "closeout.run, when the closeout printed no terminal line",
    ),
    contracts.BLOCKED_UNRECORDED: (
        {"task": "T-1", "evidence": "no comment newer than 'c-1' after the closeout"},
        "closeout.confirm_blocked_comment",
    ),
}


class CauseLineTable(unittest.TestCase):
    """Every class in HALT_LINES, rendered through the real summary from a real state file."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = os.path.join(self.tmp.name, "home")
        os.makedirs(self.home)
        self.manifest_path = os.path.join(self.tmp.name, "manifest.toml")
        with open(self.manifest_path, "w", encoding="utf-8") as handle:
            handle.write("# not loaded; summary.build reads three attributes only\n")
        self.repo = os.path.join(self.tmp.name, "repo")
        self.store = state.StateStore(self.manifest_path, self.repo, home=self.home)

    def tearDown(self):
        self.tmp.cleanup()

    def summarise(self, task_ids):
        return summary.build(_Manifest(self.manifest_path, self.repo, task_ids), self.store)

    def test_the_table_covers_every_class_in_halt_lines(self):
        """A new class without a row is a class nobody has rendered. Fail loudly rather than
        letting it reach an operator untested."""
        covered = set(RECORD_ROWS) | set(FINDING_ROWS)
        self.assertEqual(sorted(covered), sorted(contracts.HALT_LINES))

    def test_no_placeholder_survives_a_record_cause_line(self):
        for halt_class, (evidence, fields, raiser) in sorted(RECORD_ROWS.items()):
            with self.subTest(halt_class=halt_class, raiser=raiser):
                self.store.upsert("T-1", halt_class=halt_class, halt_evidence=evidence,
                                  wall_seconds=1.0, active_seconds=1.0, findings=[],
                                  **fields)
                entry = self.summarise(["T-1"])["tasks"][0]
                self.assertNotIn("?", entry["cause"],
                                 "%s renders a placeholder: %s" % (halt_class, entry["cause"]))
                self.assertNotIn("{", entry["cause"],
                                 "%s left a field unfilled: %s" % (halt_class, entry["cause"]))

    def test_no_placeholder_survives_a_finding_line(self):
        for halt_class, (finding, raiser) in sorted(FINDING_ROWS.items()):
            with self.subTest(halt_class=halt_class, raiser=raiser):
                self.store.upsert("T-1", status=contracts.STATUS_BLOCKED,
                                  halt_class=contracts.HALT_BLOCKED_ENVELOPE,
                                  halt_evidence={"blocker": "a stated blocker"},
                                  findings=[dict(finding, **{"class": halt_class})])
                line = self.summarise(["T-1"])["tasks"][0]["findings"][0]["line"]
                self.assertNotIn("?", line,
                                 "%s renders a placeholder: %s" % (halt_class, line))
                self.assertNotIn("{", line, "%s left a field unfilled: %s" % (halt_class, line))

    def test_a_record_field_never_shadows_the_evidence(self):
        """The defect behind the runner_crashed line. The record is a rendering source too, and
        a record key that collides with an evidence key used to win, so every crashed task read
        `during halted` no matter what it was doing when the runner died."""
        self.store.upsert("T-1", status=contracts.STATUS_HALTED,
                          halt_class=contracts.HALT_RUNNER_CRASHED,
                          halt_evidence={"status_before": contracts.STATUS_MERGING,
                                         "branch": "relay/T-1"},
                          branch="relay/other")
        entry = self.summarise(["T-1"])["tasks"][0]
        self.assertIn(contracts.STATUS_MERGING, entry["cause"])
        self.assertIn("relay/T-1", entry["cause"])
        self.assertNotIn("relay/other", entry["cause"])

    def test_an_empty_record_still_renders_rather_than_raising(self):
        """The safety net the rest of this module exists to stop relying on. It stays: a record
        that halted before its evidence was filled must still print a line."""
        for halt_class in sorted(contracts.HALT_LINES):
            with self.subTest(halt_class=halt_class):
                self.store.upsert("T-1", halt_class=halt_class, halt_evidence={}, findings=[])
                entry = self.summarise(["T-1"])["tasks"][0]
                self.assertTrue(entry["cause"])
                self.assertNotIn("{", entry["cause"])


class CauseLinesFromARealRun(RunCase):
    """The table above is hand written, so two classes are also taken from a real run of the
    loop over the stub. If a raiser drifts from its row, these two notice."""

    def test_a_landed_and_a_blocked_task_both_name_their_evidence(self):
        self.task_success("T-1")
        self.closeout_landed("T-1")
        self.task_blocked("T-2")
        self.closeout_blocked("T-2")
        self.task_success("T-3")
        self.closeout_landed("T-3")
        from relay import run as runner
        outcome = runner.run(self.manifest, home=self.home, base_env=self.base_env(),
                             stream=None)
        data = summary.build(self.manifest, outcome.store)
        by_id = {entry["id"]: entry for entry in data["tasks"]}
        self.assertEqual(by_id["T-1"]["class"], contracts.HALT_LANDED)
        self.assertEqual(by_id["T-2"]["class"], contracts.HALT_BLOCKED_ENVELOPE)
        for task_id in ("T-1", "T-2", "T-3"):
            self.assertNotIn("?", by_id[task_id]["cause"],
                             "%s: %s" % (task_id, by_id[task_id]["cause"]))

    def test_a_timed_out_task_names_its_minutes_and_its_tree(self):
        """Finding 22 came from here. The launcher measures seconds and the template asks for
        minutes, so the line an operator read said `? active minutes` for every timeout."""
        self.task_success("T-1")
        self.closeout_landed("T-1")
        self.queue_entry("success.jsonl", None, sleep=20)
        self.closeout_blocked("T-2")
        self.task_success("T-3")
        self.closeout_landed("T-3")
        from relay import run as runner
        outcome = runner.run(self.manifest, home=self.home, base_env=self.base_env(),
                             stream=None, timeout_overrides={"task_seconds": 2})
        entry = [e for e in summary.build(self.manifest, outcome.store)["tasks"]
                 if e["id"] == "T-2"][0]
        self.assertEqual(entry["class"], contracts.HALT_TIMEOUT)
        self.assertNotIn("?", entry["cause"])
        self.assertIn("clean", entry["cause"])
        self.assertIn("active minutes", entry["cause"])


class LinesFromTheFirstLiveRun(unittest.TestCase):
    """Two misreports the 2026-08-26 live run's summary printed."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = os.path.join(self.tmp.name, "home")
        os.makedirs(self.home)
        self.manifest_path = os.path.join(self.tmp.name, "manifest.toml")
        with open(self.manifest_path, "w", encoding="utf-8") as handle:
            handle.write("# not loaded\n")
        self.repo = os.path.join(self.tmp.name, "repo")
        self.store = state.StateStore(self.manifest_path, self.repo, home=self.home)

    def tearDown(self):
        self.tmp.cleanup()

    def text(self):
        return summary.render(summary.build(_Manifest(self.manifest_path, self.repo, ["T-1"]), self.store))

    def test_a_landed_task_names_its_ref_once(self):
        self.store.upsert("T-1", status=contracts.STATUS_LANDED, halt_class=contracts.HALT_LANDED,
                          halt_evidence={"ref": "abc1234"}, landing_ref="abc1234", findings=[])
        self.assertEqual(self.text().count("landed at abc1234"), 1)

    def test_a_refused_retry_prints_the_refusal_beside_the_class_line(self):
        message = "retry refused: relay/T-1 carries commits past the baseline; keep or discard them by hand first"
        self.store.upsert("T-1", status=contracts.STATUS_HALTED, halt_class=contracts.HALT_UNCLEAN_EXIT,
                          halt_evidence={"branch": "relay/T-1"}, halt_message=message, findings=[])
        text = self.text()
        self.assertIn("left the tree dirty on relay/T-1", text)
        self.assertIn(message, text)

    def test_a_halt_message_equal_to_the_cause_is_not_printed_twice(self):
        self.store.upsert("T-1", status=contracts.STATUS_HALTED, halt_class=contracts.HALT_UNCLEAN_EXIT,
                          halt_evidence={"branch": "relay/T-1"},
                          halt_message="left the tree dirty on relay/T-1", findings=[])
        self.assertEqual(self.text().count("left the tree dirty on relay/T-1"), 1)


class ContinuedPastChecks(CauseLineTable):
    """Issue #15: a task the run continued past is a check by hand item of its own, and the
    single task a halted run stopped on is still listed exactly once."""

    def halted(self, task_id, continued_past):
        self.store.upsert(task_id, status=contracts.STATUS_HALTED,
                          halt_class=contracts.HALT_GATE_REFUSED,
                          halt_evidence={"branch": "main", "sha": "a" * 40, "log": "/gate.log"},
                          halt_message="gate refused", continued_past=continued_past,
                          findings=[], wall_seconds=1.0, active_seconds=1.0)

    def kinds(self, task_ids):
        data = self.summarise(task_ids)
        return data, [(check["kind"], check["task"]) for check in data["pending_checks"]]

    def test_a_continued_past_task_in_a_completed_run_is_listed_by_class(self):
        self.halted("T-2", True)
        self.store.write_terminal(contracts.RUN_COMPLETED)
        data, kinds = self.kinds(["T-2"])
        self.assertEqual(kinds, [("continued_past", "T-2")])
        text = data["pending_checks"][0]["text"]
        self.assertIn("T-2", text)
        self.assertIn(contracts.HALT_GATE_REFUSED, text)
        self.assertTrue(data["tasks"][0]["continued_past"])
        self.assertIn(text, summary.render(data))

    def test_the_task_a_run_halted_on_is_listed_once(self):
        self.halted("T-2", False)
        self.store.write_terminal(contracts.RUN_HALTED, "T-2", contracts.HALT_GATE_REFUSED)
        _, kinds = self.kinds(["T-2"])
        self.assertEqual([k for k, _ in kinds], ["halted"])

    def test_a_halted_run_lists_a_continued_past_task_and_its_stop_separately(self):
        self.halted("T-1", True)
        self.halted("T-3", False)
        self.store.write_terminal(contracts.RUN_HALTED, "T-3", contracts.HALT_GATE_REFUSED)
        _, kinds = self.kinds(["T-1", "T-3"])
        self.assertEqual(sorted(kinds), [("continued_past", "T-1"), ("halted", "T-3")])

    def test_landed_and_blocked_records_are_untouched(self):
        self.store.upsert("T-1", status=contracts.STATUS_LANDED, halt_class=contracts.HALT_LANDED,
                          landing_ref="b" * 40, findings=[])
        self.store.upsert("T-2", status=contracts.STATUS_BLOCKED,
                          halt_class=contracts.HALT_BLOCKED_ENVELOPE, branch="relay/T-2",
                          halt_evidence={"blocker": "x"}, findings=[])
        self.store.write_terminal(contracts.RUN_COMPLETED)
        _, kinds = self.kinds(["T-1", "T-2"])
        self.assertEqual(kinds, [("stranded_branch", "T-2")])
