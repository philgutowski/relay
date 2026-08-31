"""U10: the six operator verbs and the summary they render.

The summary tests hold R46's one direction: the JSON is the summary and the text is rendered
from it, so every text line names the JSON field it came from.
"""
import io
import json
import os
import re
import time
import unittest
from types import SimpleNamespace
from unittest import mock

import _paths
from relay import cli, contracts, manifest as manifest_module, state, summary, tail
from test_run import RunCase


def resolve(data, source):
    """Follow a source string like `tasks[2].findings[0].line` into the summary JSON."""
    node = data
    for part in source.split("."):
        match = re.match(r"^([A-Za-z_]+)((?:\[\d+\])*)$", part)
        if not match:
            return KeyError(part)
        node = node[match.group(1)]
        for index in re.findall(r"\[(\d+)\]", match.group(2)):
            node = node[int(index)]
    return node


class CliCase(RunCase):
    def call(self, *argv):
        out = io.StringIO()
        code = cli.main(list(argv), env=self.base_env(), out=out)
        return code, out.getvalue()

    def complete_run(self):
        self.task_success("T-1")
        self.closeout_landed("T-1")
        self.task_blocked("T-2")
        self.closeout_blocked("T-2")
        self.task_success("T-3")
        self.closeout_landed("T-3")
        return self.call("run", self.manifest_path)

    def wait_for_terminal(self, seconds=120):
        """Wait out a detached runner this case left going, so tearDown does not delete the
        state directory under it."""
        import time
        deadline = time.time() + seconds
        while time.time() < deadline and not self.store().terminal():
            time.sleep(0.2)
        return self.store().terminal()

    def halted_run(self):
        """T-2's process reports status complete and creates no branch, so the runner has
        nothing to merge and halts with a named class instead of trusting the claim."""
        self.task_success("T-1")
        self.closeout_landed("T-1")
        self.queue_entry("success.jsonl", None)
        return self.call("run", self.manifest_path)


class Validate(CliCase):
    def test_a_good_manifest_validates_and_names_the_closeout_allowed_paths(self):
        code, text = self.call("validate", self.manifest_path)
        self.assertEqual(code, cli.EXIT_OK, text)
        self.assertIn("is valid", text)
        self.assertIn("3 task(s)", text)
        self.assertIn("tracker.md", text)

    def test_a_manifest_with_a_broken_rule_exits_config_and_names_the_field(self):
        with open(self.manifest_path) as handle:
            text = handle.read()
        with open(self.manifest_path, "w") as handle:
            handle.write(text.replace('command = ["true"]', 'command = "true"'))
        code, out = self.call("validate", self.manifest_path)
        self.assertEqual(code, cli.EXIT_CONFIG)
        self.assertIn("gate.command", out)

    def test_a_missing_manifest_exits_config_rather_than_raising(self):
        code, out = self.call("validate", os.path.join(self.tmp.name, "nope.toml"))
        self.assertEqual(code, cli.EXIT_CONFIG)
        self.assertIn("manifest not found", out)

    def test_the_list_flag_prints_the_tracker_candidates(self):
        code, out = self.call("validate", self.manifest_path, "--list")
        self.assertEqual(code, cli.EXIT_OK, out)
        for task_id in ("T-1", "T-2", "T-3"):
            self.assertIn("candidate: %s" % task_id, out)

    def test_an_unknown_verb_exits_config_not_halted(self):
        code, _ = self.call("diagnose", self.manifest_path)
        self.assertEqual(code, cli.EXIT_CONFIG)

    def test_a_missing_backend_binary_exits_config_and_names_the_backend(self):
        with mock.patch.object(manifest_module.shutil, "which", return_value=None):
            code, out = self.call("validate", self.manifest_path)
        self.assertEqual(code, cli.EXIT_CONFIG)
        self.assertIn("claude", out)
        self.assertIn("binary", out)

    def test_a_missing_backend_plugin_exits_config_and_names_the_backend(self):
        plugin_result = SimpleNamespace(returncode=0, stdout="other-plugin 9.0.0", stderr="")
        with mock.patch.object(manifest_module.shutil, "which", return_value="/test-bin/claude"), \
                mock.patch.object(manifest_module, "_run_plugin_query", return_value=plugin_result):
            code, out = self.call("validate", self.manifest_path)
        self.assertEqual(code, cli.EXIT_CONFIG)
        self.assertIn("claude", out)
        self.assertIn("plugin", out)
        self.assertNotIn("binary", out)

    def test_a_probe_exception_exits_config_and_names_the_backend(self):
        with mock.patch.object(manifest_module.shutil, "which", return_value="/test-bin/claude"), \
                mock.patch.object(manifest_module, "_run_plugin_query",
                                  side_effect=OSError("no such file or directory")):
            code, out = self.call("validate", self.manifest_path)
        self.assertEqual(code, cli.EXIT_CONFIG)
        self.assertIn("claude", out)


class RunVerb(CliCase):
    def test_a_complete_run_exits_ok_and_prints_the_summary(self):
        code, out = self.complete_run()
        self.assertEqual(code, cli.EXIT_OK, out)
        self.assertIn("relay run completed", out)
        self.assertIn("T-1", out)

    def test_an_invalid_manifest_is_refused_before_any_process_starts(self):
        with open(self.manifest_path) as handle:
            text = handle.read()
        with open(self.manifest_path, "w") as handle:
            handle.write(text.replace('gate = "A pre push hook', 'gate = ""\nunused = "A pre push hook'))
        code, out = self.call("run", self.manifest_path)
        self.assertEqual(code, cli.EXIT_CONFIG)
        self.assertIn("qualifying.gate", out)
        self.assertIsNone(self.store().read())

    def test_a_missing_backend_binary_is_refused_before_any_process_starts(self):
        with mock.patch.object(manifest_module.shutil, "which", return_value=None):
            code, out = self.call("run", self.manifest_path)
        self.assertEqual(code, cli.EXIT_CONFIG)
        self.assertIn("backend claude binary", out)
        self.assertIsNone(self.store().read())

    def test_a_halted_run_exits_halted(self):
        code, out = self.halted_run()
        self.assertEqual(code, cli.EXIT_HALTED, out)
        self.assertIn(contracts.HALT_UNCLEAN_EXIT, out)
        self.assertIn("left no commits", out)


class DetachedRun(CliCase):
    def test_detach_returns_at_once_and_the_run_completes_in_its_own_session(self):
        import time
        self.task_success("T-1")
        self.closeout_landed("T-1")
        self.task_blocked("T-2")
        self.closeout_blocked("T-2")
        self.task_success("T-3")
        self.closeout_landed("T-3")
        code, out = self.call("run", self.manifest_path, "--detach")
        self.assertEqual(code, cli.EXIT_OK, out)
        self.assertIn("runner detached: pid", out)
        self.assertIn("runner log:", out)
        deadline = time.time() + 120
        while time.time() < deadline and not self.store().terminal():
            time.sleep(1)
        terminal = self.store().terminal()
        self.assertIsNotNone(terminal, "the detached run never wrote a terminal record")
        self.assertEqual(terminal["run_status"], contracts.RUN_COMPLETED)
        with open(self.store().path("runner.log")) as handle:
            self.assertIn("relay run completed", handle.read())


class DetachCommand(CliCase):
    """U4: the argv a detached runner is started with.

    Pinned here rather than by watching `runner.log` grow, because a test that polls a log while
    a run is in flight is a race against the run finishing first.
    """

    def test_the_interpreter_runs_unbuffered(self):
        argv = cli.detach_command("/x/relay_cli.py", "/x/manifest.toml", False)
        self.assertEqual(argv[1], "-u")
        self.assertEqual(argv[2], "/x/relay_cli.py")

    def test_the_verb_and_the_manifest_follow_the_entry_point(self):
        argv = cli.detach_command("/x/relay_cli.py", "/x/manifest.toml", False)
        self.assertEqual(argv[3:], ["run", "/x/manifest.toml"])

    def test_retry_blocked_is_carried_through_only_when_asked(self):
        self.assertIn("--retry-blocked", cli.detach_command("/e", "/m", True))
        self.assertNotIn("--retry-blocked", cli.detach_command("/e", "/m", False))


class FollowedRun(CliCase):
    """U3: `run --follow` launches the runner, follows it from the launch, and reports."""

    def queue_complete(self):
        self.task_success("T-1")
        self.closeout_landed("T-1")
        self.task_blocked("T-2")
        self.closeout_blocked("T-2")
        self.task_success("T-3")
        self.closeout_landed("T-3")

    def test_a_followed_run_detaches_follows_and_prints_the_summary(self):
        self.queue_complete()
        code, out = self.call("run", self.manifest_path, "--follow")
        self.assertEqual(code, cli.EXIT_OK, out)
        self.assertIn("runner detached: pid", out)
        self.assertIn("following: %s" % self.store().dir, out)
        self.assertIn("relay run completed", out)
        self.assertEqual(self.store().terminal()["run_status"], contracts.RUN_COMPLETED)

    def test_follow_implies_detach_so_the_detach_line_is_printed_without_the_flag(self):
        """The detach line is the observable that separates the two paths: a foreground run
        never prints one."""
        self.queue_complete()
        _, followed = self.call("run", self.manifest_path, "--follow")
        self.assertIn("runner detached: pid", followed)

    def test_a_followed_halted_run_exits_halted_and_names_the_class(self):
        self.task_success("T-1")
        self.closeout_landed("T-1")
        self.queue_entry("success.jsonl", None)
        code, out = self.call("run", self.manifest_path, "--follow")
        self.assertEqual(code, cli.EXIT_HALTED, out)
        self.assertIn(contracts.HALT_UNCLEAN_EXIT, out)

    def test_a_second_followed_run_does_not_replay_the_first(self):
        """The state directory is keyed on the manifest path, and the runner appends to the task
        logs, so without a floor the second launch would replay the first run."""
        self.queue_complete()
        self.call("run", self.manifest_path, "--follow")
        first = self.store().terminal()["written_at"]
        self.queue_complete()
        code, out = self.call("run", self.manifest_path, "--follow")
        self.assertEqual(code, cli.EXIT_OK, out)
        self.assertNotEqual(self.store().terminal()["written_at"], first)
        # Every task landed or blocked in the first run, so the second launches none of them and
        # writes no task log. Without a floor the follower would still replay all three.
        self.assertNotIn("== T-1 %s ==" % tail.PHASE_TASK, out)
        self.assertNotIn("stub_done", out)

    def test_the_bound_returns_at_once_and_says_the_run_continues(self):
        self.queue_complete()
        code, out = self.call("run", self.manifest_path, "--follow", "--for", "0")
        self.assertEqual(code, cli.EXIT_OK, out)
        self.assertIn("the run continues", out)
        self.assertIn("relay tail", out)
        self.assertNotIn("relay run completed", out)
        self.wait_for_terminal()

    def test_phases_prints_the_events_without_the_decoded_activity(self):
        self.queue_complete()
        code, out = self.call("run", self.manifest_path, "--follow", "--phases")
        self.assertEqual(code, cli.EXIT_OK, out)
        self.assertIn("== T-1 %s ==" % tail.PHASE_TASK, out)
        self.assertIn("T-1 is now %s" % contracts.STATUS_LANDED, out)
        self.assertNotIn("stub_done", out)

    def test_a_runner_that_dies_without_a_record_ends_the_follow_with_its_own_code(self):
        """A second runner cannot take the lease, so it exits 3 having written nothing. Without
        R16 the follower would sit until its bound with nothing to report."""
        holder = self.store()
        self.assertTrue(holder.acquire().ok)
        try:
            code, out = self.call("run", self.manifest_path, "--follow")
        finally:
            holder.release()
        self.assertEqual(code, cli.EXIT_LEASE, out)
        self.assertIn("without writing a terminal record", out)

    def test_an_interrupt_leaves_the_run_alive_and_names_the_way_back(self):
        self.queue_complete()
        original = cli.tail_module.follow

        def interrupt(*_args, **_kwargs):
            raise KeyboardInterrupt()

        cli.tail_module.follow = interrupt
        try:
            code, out = self.call("run", self.manifest_path, "--follow")
        finally:
            cli.tail_module.follow = original
        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("the run continues", out)
        self.assertIn("relay tail %s" % self.manifest_path, out)
        self.wait_for_terminal()

    def test_a_followed_run_that_reclaims_a_stale_lease_still_follows_to_completion(self):
        """R3: seed a stale lease with T-1 already marked running under it, the way a crashed
        runner would leave things. The detached child this --follow launch spawns is the one
        that has to discover and reclaim that lease during its own startup, not this test, so the
        lease is left in place rather than reclaimed in-process. If the reclaim (U1) still leaked
        a phantom terminal record, or if some regression made the run stop right after reclaiming
        instead of continuing, the follow would end early: the completion line, the terminal
        record's run_status, and the phase headers for every one of the three tasks all have to
        show up for this to pass, which rules out a coincidental early exit.

        R4 rides along here rather than in its own fixture: the same run that proves the reclaim
        doesn't disturb `status`/`--follow` is used again to prove summary.build()'s halt_task/
        halt_class stay consistent with run_status once that run is done."""
        seed = state.StateStore(self.manifest_path, self.repo, home=self.home, pid=999999,
                                ttl_seconds=1)
        seed.acquire()
        seed.upsert("T-1", status=contracts.STATUS_RUNNING)
        time.sleep(1.1)

        self.queue_complete()
        code, out = self.call("run", self.manifest_path, "--follow")

        self.assertEqual(code, cli.EXIT_OK, out)
        self.assertIn("relay run completed", out)
        self.assertEqual(self.store().terminal()["run_status"], contracts.RUN_COMPLETED)
        self.assertIn("== T-1 %s ==" % tail.PHASE_TASK, out)
        self.assertIn("== T-3 %s ==" % tail.PHASE_TASK, out)

        data = summary.build(self.manifest, self.store())
        self.assertEqual(data["run_status"], contracts.RUN_COMPLETED)
        self.assertIsNone(data["halt_task"])
        self.assertIsNone(data["halt_class"])

    def test_notifications_are_off_unless_the_flag_is_given(self):
        """The suite is hermetic by construction: no case passes --notify, so no case can fire
        one. This pins the default rather than trusting it."""
        seen = []
        original = cli.notify.build

        def record(enabled, **kwargs):
            seen.append(enabled)
            return original(False)

        cli.notify.build = record
        try:
            self.complete_run()
            self.call("tail", self.manifest_path)
        finally:
            cli.notify.build = original
        self.assertEqual(seen, [False])


class StatusVerb(CliCase):
    def test_status_prints_the_terminal_record_and_the_cursor(self):
        self.complete_run()
        code, out = self.call("status", self.manifest_path)
        self.assertEqual(code, cli.EXIT_OK, out)
        self.assertIn("status: %s" % contracts.RUN_COMPLETED, out)
        self.assertIn("cursor: 3 of 3", out)
        self.assertIn("T-2 %s" % contracts.STATUS_BLOCKED, out)

    def test_status_never_takes_the_lease(self):
        self.complete_run()
        holder = self.store()
        self.assertTrue(holder.acquire().ok)
        before = json.dumps(holder.read(), sort_keys=True)
        code, out = self.call("status", self.manifest_path)
        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("lease: pid", out)
        self.assertEqual(json.dumps(self.store().read(), sort_keys=True), before)
        holder.release()

    def test_status_before_any_run_says_so(self):
        code, out = self.call("status", self.manifest_path)
        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("no state", out)

    def test_an_unchanged_manifest_prints_no_stale_line(self):
        self.complete_run()
        _, out = self.call("status", self.manifest_path)
        self.assertNotIn("stale state", out)
        self.assertNotIn("not in this manifest", out)

    def test_status_right_after_a_bare_reclaim_prints_no_terminal_record(self):
        """R2: reclaiming a stale lease (U1) only marks the in-flight record halted/crashed; it
        must not fabricate a run-level terminal record. No run() call happens here at all, so if
        `status` prints one it can only be a phantom conjured by the reclaim itself."""
        stale = state.StateStore(self.manifest_path, self.repo, home=self.home, pid=999999,
                                 ttl_seconds=1)
        stale.acquire()
        time.sleep(1.1)
        reclaimer = state.StateStore(self.manifest_path, self.repo, home=self.home, pid=888888)
        self.assertTrue(reclaimer.acquire().ok)

        code, out = self.call("status", self.manifest_path)
        self.assertEqual(code, cli.EXIT_OK, out)
        self.assertNotIn("terminal record:", out)


class StatusAgainstAShrunkManifest(CliCase):
    """U5: the state directory is keyed on the manifest's real path, so editing the manifest in
    place keeps everything the previous, longer run left behind."""

    def shrink_to_one_task(self):
        """Drop the T-2 and T-3 task blocks, keeping the path so the state directory is reused."""
        with open(self.manifest_path) as handle:
            text = handle.read()
        head, _, _ = text.partition('[[tasks]]\nid = "T-2"')
        with open(self.manifest_path, "w") as handle:
            handle.write(head)

    def shrunk_status(self):
        self.complete_run()
        self.shrink_to_one_task()
        code, out = self.call("status", self.manifest_path)
        self.assertEqual(code, cli.EXIT_OK, out)
        return out

    def test_the_cursor_is_reported_with_the_reason_it_looks_wrong(self):
        out = self.shrunk_status()
        self.assertIn("cursor: 3 of 1 task(s)", out)
        self.assertIn("stale state", out)
        self.assertIn("different manifest", out)

    def test_the_records_outside_the_manifest_are_marked_and_the_one_inside_is_not(self):
        out = self.shrunk_status()
        for line in out.splitlines():
            if line.startswith("  T-1 "):
                self.assertNotIn("not in this manifest", line)
            if line.startswith("  T-2 ") or line.startswith("  T-3 "):
                self.assertIn("not in this manifest", line)

    def test_the_terminal_record_is_attributed_to_the_previous_run(self):
        out = self.shrunk_status()
        self.assertIn("terminal record: %s (of that previous run)" % contracts.RUN_COMPLETED, out)

    def test_a_stale_status_still_takes_no_lease(self):
        self.complete_run()
        self.shrink_to_one_task()
        before = json.dumps(self.store().read(), sort_keys=True)
        self.call("status", self.manifest_path)
        self.assertEqual(json.dumps(self.store().read(), sort_keys=True), before)


class TailVerb(CliCase):
    """The seventh verb. The follow loop itself is covered in test_tail; these cases pin the
    wiring: the exit code mapping, the lease rule, and the failure modes it shares with the
    other six."""

    def test_tail_on_a_completed_run_exits_ok_and_prints_decoded_activity(self):
        self.complete_run()
        code, out = self.call("tail", self.manifest_path)
        self.assertEqual(code, cli.EXIT_OK, out)
        self.assertIn(self.store().dir, out)

    def test_tail_on_a_halted_run_exits_halted(self):
        self.halted_run()
        code, out = self.call("tail", self.manifest_path)
        self.assertEqual(code, cli.EXIT_HALTED, out)

    def test_tail_on_a_missing_manifest_exits_config_like_every_other_verb(self):
        code, out = self.call("tail", os.path.join(self.tmp.name, "nope.toml"))
        self.assertEqual(code, cli.EXIT_CONFIG)
        self.assertIn("manifest not found", out)

    def test_tail_never_takes_the_lease(self):
        self.complete_run()
        holder = self.store()
        self.assertTrue(holder.acquire().ok)
        before = json.dumps(holder.read(), sort_keys=True)
        code, out = self.call("tail", self.manifest_path)
        self.assertEqual(code, cli.EXIT_OK, out)
        self.assertEqual(json.dumps(self.store().read(), sort_keys=True), before)
        holder.release()

    def test_an_interrupt_while_following_exits_ok_without_a_traceback(self):
        self.complete_run()
        original = cli.tail_module.follow

        def interrupt(*_args, **_kwargs):
            raise KeyboardInterrupt()

        cli.tail_module.follow = interrupt
        try:
            code, _ = self.call("tail", self.manifest_path)
        finally:
            cli.tail_module.follow = original
        self.assertEqual(code, cli.EXIT_OK)


class SummaryVerb(CliCase):
    def test_every_text_line_names_a_field_that_exists_in_the_json(self):
        self.complete_run()
        data = json.loads(self.call("summary", self.manifest_path, "--json")[1])
        for line, source in summary.lines(data):
            self.assertTrue(source, "a summary line carries no source: %r" % line)
            value = resolve(data, source)
            self.assertNotIsInstance(value, KeyError, "unresolvable source %r for %r" % (source, line))

    def test_the_text_is_rendered_from_the_json_and_never_the_other_way(self):
        self.complete_run()
        code, as_json = self.call("summary", self.manifest_path, "--json")
        self.assertEqual(code, cli.EXIT_OK)
        data = json.loads(as_json)
        _, as_text = self.call("summary", self.manifest_path)
        self.assertEqual(as_text.strip(), summary.render(data).strip())

    def test_the_summary_names_a_class_and_a_cause_for_every_task_that_did_not_land(self):
        self.complete_run()
        data = json.loads(self.call("summary", self.manifest_path, "--json")[1])
        blocked = [entry for entry in data["tasks"] if entry["status"] == contracts.STATUS_BLOCKED]
        self.assertTrue(blocked)
        for entry in blocked:
            self.assertTrue(entry["class"])
            self.assertTrue(entry["cause"])
            self.assertIn(entry["cause"], summary.render(data))

    def test_the_summary_points_at_no_machine_readable_output_file(self):
        self.complete_run()
        _, text = self.call("summary", self.manifest_path)
        self.assertNotIn(".json", text)
        self.assertIn(self.store().dir, text)

    def test_a_halted_run_summary_names_the_class_the_cause_and_the_state_path(self):
        self.halted_run()
        code, text = self.call("summary", self.manifest_path)
        self.assertEqual(code, cli.EXIT_HALTED)
        self.assertIn(contracts.HALT_UNCLEAN_EXIT, text)
        self.assertIn("check by hand:", text)
        self.assertIn(self.store().dir, text)
        self.assertNotIn(".json", text)

    def test_the_pending_checks_name_the_stranded_branch_of_a_blocked_task(self):
        self.complete_run()
        data = json.loads(self.call("summary", self.manifest_path, "--json")[1])
        kinds = {check["kind"]: check for check in data["pending_checks"]}
        self.assertIn("stranded_branch", kinds)
        self.assertIn("relay/T-2", kinds["stranded_branch"]["text"])


class VerifyVerb(CliCase):
    def test_verify_re_runs_the_verdict_for_a_landed_task(self):
        self.complete_run()
        code, out = self.call("verify", self.manifest_path, "T-1")
        self.assertEqual(code, cli.EXIT_OK, out)
        self.assertIn("T-1: landed", out)
        self.assertIn("card_terminal", out)

    def test_verify_reports_not_landed_for_a_blocked_task(self):
        self.complete_run()
        code, out = self.call("verify", self.manifest_path, "T-2")
        self.assertEqual(code, cli.EXIT_HALTED)
        self.assertIn("T-2: not landed", out)

    def test_verify_on_an_unknown_task_is_a_configuration_error(self):
        self.complete_run()
        code, out = self.call("verify", self.manifest_path, "T-9")
        self.assertEqual(code, cli.EXIT_CONFIG)
        self.assertIn("no record for T-9", out)


class LeaseVerb(CliCase):
    def test_a_live_lease_is_reported_and_exits_lease(self):
        holder = self.store()
        holder.acquire()
        code, out = self.call("lease", self.manifest_path)
        self.assertEqual(code, cli.EXIT_LEASE)
        self.assertIn("pid %s" % holder.pid, out)
        holder.release()

    def test_a_free_lease_exits_ok(self):
        code, out = self.call("lease", self.manifest_path)
        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("free", out)

    def test_break_clears_the_lease(self):
        holder = self.store()
        holder.acquire()
        code, out = self.call("lease", self.manifest_path, "--break")
        self.assertEqual(code, cli.EXIT_OK)
        self.assertIn("lease broken", out)
        self.assertIsNone(self.store().lease())


class EntryPoint(unittest.TestCase):
    def test_the_plugin_entry_point_delegates_to_the_cli(self):
        import importlib.util
        import _paths

        path = os.path.join(_paths.SCRIPTS_DIR, "relay_cli.py")
        spec = importlib.util.spec_from_file_location("relay_cli", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertTrue(callable(module.main))


if __name__ == "__main__":
    unittest.main()
