"""U10: the six operator verbs and the summary they render.

The summary tests hold R46's one direction: the JSON is the summary and the text is rendered
from it, so every text line names the JSON field it came from.
"""
import io
import json
import os
import re
import unittest

import _paths
from relay import cli, contracts, summary
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
