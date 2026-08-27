"""U1 and U2: the `tail` decoder and the follow loop.

The decoder cases run against `fixtures/stdout/real_stream.jsonl`, which carries real CLI line
shapes rather than stub output, because the stub prints no assistant lines and a decoder tested
only against a stub the same author extended would agree with itself by construction.

The follow cases drive `tail.follow` with an injected `sleep` that advances a scripted run
between polls. That is what lets one deterministic test cover started-before, started-during, and
started-after without a wall clock race.
"""
import json
import os
import tempfile
import unittest

import _paths
from relay import cli, contracts, state, tail
from test_cli import CliCase
from test_run import CLOSE_SH, TASK_BRANCH_SH

STDOUT_FIXTURES = os.path.join(_paths.FIXTURES_DIR, "stdout")
REAL_STREAM = os.path.join(STDOUT_FIXTURES, "real_stream.jsonl")


def line(payload):
    return json.dumps(payload)


def assistant(content):
    return line({"type": "assistant", "message": {"role": "assistant", "content": content}})


def say(body):
    return assistant([{"type": "text", "text": body}]) + "\n"


class _Task:
    def __init__(self, task_id):
        self.id = task_id


class _Manifest:
    """The two fields `tail` reads off a manifest. A real one needs a repo and a tracker; the
    follow loop needs neither, which is itself worth pinning."""

    def __init__(self, ids):
        self.tasks = [_Task(task_id) for task_id in ids]


class Decode(unittest.TestCase):
    def test_an_assistant_text_block_renders_its_text(self):
        events = tail.decode(assistant([{"type": "text", "text": "  Setting up the branch.  "}]))
        self.assertEqual(events, ["Setting up the branch."])

    def test_a_tool_use_renders_the_name_and_the_command(self):
        events = tail.decode(assistant([{"type": "tool_use", "name": "Bash",
                                         "input": {"command": "git status"}}]))
        self.assertEqual(len(events), 1)
        self.assertIn("Bash", events[0])
        self.assertIn("git status", events[0])

    def test_the_argument_keys_are_tried_in_the_documented_order(self):
        """file_path wins over description, pattern over skill, and so on down the list."""
        events = tail.decode(assistant([{"type": "tool_use", "name": "Read",
                                         "input": {"file_path": "relay/cli.py",
                                                   "description": "read it"}}]))
        self.assertIn("relay/cli.py", events[0])
        self.assertNotIn("read it", events[0])

    def test_a_tool_use_with_none_of_the_argument_keys_renders_the_name_alone(self):
        events = tail.decode(assistant([{"type": "tool_use", "name": "TaskOutput",
                                         "input": {"taskId": "3"}}]))
        self.assertEqual(len(events), 1)
        self.assertIn("TaskOutput", events[0])
        self.assertNotIn("taskId", events[0])

    def test_a_thinking_block_beside_a_text_block_renders_only_the_text(self):
        events = tail.decode(assistant([{"type": "thinking", "thinking": "weighing it up"},
                                        {"type": "text", "text": "Decided."}]))
        self.assertEqual(events, ["Decided."])

    def test_a_text_block_on_a_user_line_is_not_an_assistant_message(self):
        events = tail.decode(line({"type": "user", "message": {
            "role": "user", "content": [{"type": "text", "text": "the prompt"}]}}))
        self.assertEqual(events, [])

    def test_a_line_that_is_not_json_yields_nothing(self):
        self.assertEqual(tail.decode('{"type": "assistant", "message": {this is not json'), [])

    def test_a_line_whose_content_is_a_string_yields_nothing(self):
        self.assertEqual(tail.decode(line({"type": "assistant", "message": {
            "role": "assistant", "content": "plain text, not blocks"}})), [])

    def test_a_json_line_that_is_not_an_object_yields_nothing(self):
        self.assertEqual(tail.decode("[1, 2, 3]"), [])

    def test_a_blank_line_yields_nothing(self):
        self.assertEqual(tail.decode("   "), [])

    def test_the_line_types_that_carry_no_message_yield_nothing(self):
        for kind in ("tool_progress", "rate_limit_event", "result", "system"):
            self.assertEqual(tail.decode(line({"type": kind, "uuid": "x"})), [],
                             "%s should decode to no events" % kind)

    def test_bytes_and_str_decode_the_same(self):
        payload = assistant([{"type": "text", "text": "same either way"}])
        self.assertEqual(tail.decode(payload), tail.decode(payload.encode("utf-8")))

    def test_a_long_text_block_is_bounded(self):
        events = tail.decode(assistant([{"type": "text", "text": "x" * 5000}]))
        self.assertLessEqual(len(events[0]), tail.TEXT_CHARS + 4)

    def test_a_long_argument_is_bounded(self):
        events = tail.decode(assistant([{"type": "tool_use", "name": "Bash",
                                         "input": {"command": "y" * 5000}}]))
        self.assertLessEqual(len(events[0]), tail.ARGUMENT_CHARS + 40)


class RealStream(unittest.TestCase):
    """The decoder against captured CLI output, not against the stub."""

    def setUp(self):
        with open(REAL_STREAM, "rb") as handle:
            self.events = [event for raw in handle for event in tail.decode(raw)]
        self.text = "\n".join(self.events)

    def test_the_fixture_regenerates_identically(self):
        """A hand edit to the fixture that _make.py would not produce is a defect, because the
        fixture's whole value is that it carries shapes nobody invented."""
        import subprocess
        with open(REAL_STREAM, "rb") as handle:
            before = handle.read()
        subprocess.run(["python3", os.path.join(STDOUT_FIXTURES, "_make.py")],
                       check=True, capture_output=True)
        with open(REAL_STREAM, "rb") as handle:
            self.assertEqual(handle.read(), before)

    def test_both_assistant_texts_render(self):
        self.assertIn("I'll start by setting up the branch", self.text)
        self.assertIn("The branch is ready and the suite is green.", self.text)

    def test_no_thinking_text_reaches_the_output(self):
        self.assertNotIn("Let me check the branch first", self.text)
        self.assertNotIn("signature", self.text)

    def test_every_tool_call_renders_with_its_argument(self):
        for name, argument in (("Bash", "git status"), ("Read", "cli.py"), ("Grep", "def cmd_"),
                               ("Skill", "compound-engineering:ce-plan"),
                               ("Agent", "Review the follower")):
            hits = [event for event in self.events if name in event and argument in event]
            self.assertTrue(hits, "no rendered event for %s with %r" % (name, argument))

    def test_the_task_output_call_renders_without_an_argument(self):
        hits = [event for event in self.events if "TaskOutput" in event]
        self.assertEqual(len(hits), 1)
        self.assertNotIn("taskId", hits[0])

    def test_a_subagent_line_renders_like_any_other(self):
        """The line carrying parent_tool_use_id is a subagent's. Attributing it is deferred, so
        it has to render rather than vanish."""
        hits = [event for event in self.events if "run.py" in event]
        self.assertTrue(hits)

    def test_the_result_and_heartbeat_lines_contribute_nothing(self):
        self.assertNotIn("total_cost_usd", self.text)
        self.assertNotIn("heartbeat", self.text)


class FollowCase(unittest.TestCase):
    """Drives `tail.follow` with an injected `sleep` that advances a scripted run between polls,
    so started-before, started-during, and started-after are one deterministic pass each."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = os.path.join(self.tmp.name, "home")
        os.makedirs(self.home)
        self.manifest_path = os.path.join(self.tmp.name, "manifest.toml")
        with open(self.manifest_path, "w") as handle:
            handle.write("# a path is all the store needs\n")
        self.repo = os.path.join(self.tmp.name, "repo")
        os.makedirs(self.repo)
        self.manifest = _Manifest(["T-1", "T-2", "T-3"])
        self.store = state.StateStore(self.manifest_path, self.repo, home=self.home)
        self.lines = []

    def tearDown(self):
        self.tmp.cleanup()

    def stream(self, text):
        self.lines.append(text)

    @property
    def text(self):
        return "\n".join(self.lines)

    def log(self, task_id, phase=tail.PHASE_TASK):
        name = "%s.stdout.log" % task_id if phase == tail.PHASE_TASK else "%s.closeout.stdout.log" % task_id
        return self.store.path("logs", name)

    def append(self, task_id, payload, phase=tail.PHASE_TASK):
        with open(self.log(task_id, phase), "ab") as handle:
            handle.write(payload if isinstance(payload, bytes) else payload.encode("utf-8"))

    def terminal(self, run_status=contracts.RUN_COMPLETED):
        self.store.write_terminal(run_status)

    def go(self, script=(), limit=40):
        """Follow, running one scripted step per sleep. The script mutates the filesystem and the
        state store the way a live runner would, between polls."""
        steps = list(script)
        calls = {"n": 0}

        def sleep(_seconds):
            calls["n"] += 1
            if calls["n"] > limit:
                raise AssertionError("follow did not terminate after %d polls" % limit)
            if steps:
                steps.pop(0)()

        return tail.follow(self.manifest, self.store, self.stream, sleep=sleep, poll_seconds=0)


class FollowAfterTheRun(FollowCase):
    def test_a_finished_run_replays_in_manifest_order_and_returns_completed(self):
        self.append("T-1", say("one"))
        self.append("T-1", say("one closeout"), phase=tail.PHASE_CLOSEOUT)
        self.append("T-2", say("two"))
        self.terminal()
        self.assertEqual(self.go(), contracts.RUN_COMPLETED)
        self.assertLess(self.text.index("one"), self.text.index("one closeout"))
        self.assertLess(self.text.index("one closeout"), self.text.index("two"))

    def test_a_halted_run_returns_halted(self):
        self.append("T-1", say("one"))
        self.terminal(contracts.RUN_HALTED)
        self.assertEqual(self.go(), contracts.RUN_HALTED)

    def test_a_terminal_record_already_present_replays_and_exits_rather_than_waiting(self):
        """KTD4: a finished previous run and a run about to start are the same state, and the
        finished one wins, so `follow` never hangs on a store that already reached terminal."""
        self.terminal()
        self.assertEqual(self.go(), contracts.RUN_COMPLETED)

    def test_the_phase_header_names_the_task_and_the_phase(self):
        self.append("T-1", say("one"))
        self.append("T-1", say("closing"), phase=tail.PHASE_CLOSEOUT)
        self.terminal()
        self.go()
        self.assertIn("T-1", self.text)
        self.assertIn(tail.PHASE_CLOSEOUT, self.text)

    def test_the_state_directory_is_named_once_for_orientation(self):
        self.terminal()
        self.go()
        self.assertIn(self.store.dir, self.text)


class FollowDuringTheRun(FollowCase):
    def test_a_run_that_starts_after_follow_does_is_picked_up(self):
        status = self.go(script=[
            lambda: self.append("T-1", say("first line")),
            lambda: self.append("T-1", say("second line")),
            lambda: self.terminal(),
        ])
        self.assertEqual(status, contracts.RUN_COMPLETED)
        self.assertIn("first line", self.text)
        self.assertIn("second line", self.text)

    def test_follow_says_it_is_waiting_once_and_then_stays_quiet(self):
        self.go(script=[lambda: None, lambda: None, lambda: self.terminal()])
        waiting = [entry for entry in self.lines if "waiting" in entry]
        self.assertEqual(len(waiting), 1, self.lines)

    def test_no_line_is_printed_twice_across_polls(self):
        self.go(script=[
            lambda: self.append("T-1", say("only once")),
            lambda: None,
            lambda: None,
            lambda: self.terminal(),
        ])
        self.assertEqual(self.text.count("only once"), 1)

    def test_a_line_split_across_two_polls_is_emitted_once_and_whole(self):
        payload = say("a whole sentence that arrived in two pieces")
        half = len(payload) // 2
        self.go(script=[
            lambda: self.append("T-1", payload[:half]),
            lambda: self.append("T-1", payload[half:]),
            lambda: self.terminal(),
        ])
        self.assertEqual(self.text.count("a whole sentence that arrived in two pieces"), 1)

    def test_a_multibyte_character_split_across_a_read_boundary_survives(self):
        payload = say("a café and a 寿司 bar").encode("utf-8")
        cut = payload.index("caf".encode("utf-8")) + 4  # lands inside the two byte e-acute
        self.go(script=[
            lambda: self.append("T-1", payload[:cut]),
            lambda: self.append("T-1", payload[cut:]),
            lambda: self.terminal(),
        ])
        self.assertIn("a café and a 寿司 bar", self.text)

    def test_the_terminal_record_appearing_with_the_last_lines_does_not_truncate_them(self):
        def finish():
            self.append("T-3", say("the very last line"))
            self.terminal()

        self.go(script=[lambda: self.append("T-1", say("early")), finish])
        self.assertIn("the very last line", self.text)


class FollowAcrossBoundaries(FollowCase):
    def test_the_cursor_advances_task_to_closeout_to_the_next_task(self):
        self.go(script=[
            lambda: self.append("T-1", say("task one")),
            lambda: self.append("T-1", say("closeout one"), phase=tail.PHASE_CLOSEOUT),
            lambda: self.append("T-2", say("task two")),
            lambda: self.terminal(),
        ])
        order = [self.text.index(body) for body in ("task one", "closeout one", "task two")]
        self.assertEqual(order, sorted(order))

    def test_a_task_that_wrote_no_log_at_all_is_skipped(self):
        """An excluded task never launches, so its log never appears. The cursor must not wait
        on it."""
        self.go(script=[
            lambda: self.append("T-1", say("task one")),
            lambda: self.append("T-3", say("task three")),
            lambda: self.terminal(),
        ])
        self.assertIn("task one", self.text)
        self.assertIn("task three", self.text)
        self.assertNotIn("T-2", self.text)

    def test_a_task_whose_closeout_never_ran_advances_to_the_next_task(self):
        self.go(script=[
            lambda: self.append("T-1", say("task one")),
            lambda: self.append("T-2", say("task two")),
            lambda: self.terminal(),
        ])
        self.assertIn("task two", self.text)

    def test_the_candidate_list_is_two_logs_per_task_in_manifest_order(self):
        entries = tail.candidates(self.manifest, self.store)
        self.assertEqual([(task_id, phase) for task_id, phase, _ in entries], [
            ("T-1", tail.PHASE_TASK), ("T-1", tail.PHASE_CLOSEOUT),
            ("T-2", tail.PHASE_TASK), ("T-2", tail.PHASE_CLOSEOUT),
            ("T-3", tail.PHASE_TASK), ("T-3", tail.PHASE_CLOSEOUT),
        ])


class FollowOnAManifestValidateWouldReject(FollowCase):
    """`tail` deliberately does not validate the manifest, so it accepts shapes the other verbs
    refuse. `validate` rejects both of these (manifest.py: "tasks is empty", "id is listed
    twice"), which means the follow loop is the only thing standing between them and the
    operator."""

    def test_a_manifest_with_no_tasks_reports_the_run_instead_of_raising(self):
        self.manifest = _Manifest([])
        self.terminal()
        self.assertEqual(self.go(), contracts.RUN_COMPLETED)

    def test_a_task_listed_twice_does_not_replay_its_log_twice(self):
        self.manifest = _Manifest(["T-1", "T-1"])
        self.append("T-1", say("said once"))
        self.terminal()
        self.go()
        self.assertEqual(self.text.count("said once"), 1)


class FollowTakesNoLease(FollowCase):
    def test_following_leaves_the_state_file_byte_identical(self):
        holder = state.StateStore(self.manifest_path, self.repo, home=self.home)
        self.assertTrue(holder.acquire().ok)
        self.terminal()
        self.append("T-1", say("watched while the lease was held"))
        with open(self.store.state_path, "rb") as handle:
            before = handle.read()
        self.go()
        with open(self.store.state_path, "rb") as handle:
            self.assertEqual(handle.read(), before)
        self.assertIsNotNone(self.store.lease())
        holder.release()


class TailAStubRun(CliCase):
    """U4: a real run over the stub, with the stub echoing decodable stdout, then tailed.

    Everything above this point tests the follower against files a test wrote. This drives the
    actual runner, so the log layout, the write order, and the boundary between a task and its
    closeout are the runner's rather than the test's.
    """

    def stream_file(self, name, body):
        path = os.path.join(self.tmp.name, name + ".jsonl")
        with open(path, "w") as handle:
            handle.write(say(body))
            handle.write(assistant([{"type": "tool_use", "name": "Skill",
                                     "input": {"skill": "compound-engineering:ce-work"}}]) + "\n")
        return path

    def streamed_run(self):
        for task_id in ("T-1", "T-2", "T-3"):
            self.queue_entry("success.jsonl",
                             TASK_BRANCH_SH % (task_id, task_id.lower().replace("-", "_"), task_id),
                             stream=self.stream_file(task_id, "working on %s" % task_id))
            self.queue_entry("closeout_skipped.jsonl", CLOSE_SH % (task_id, task_id),
                             stream=self.stream_file(task_id + "-closeout",
                                                     "closing out %s" % task_id))
        return self.call("run", self.manifest_path)

    def test_the_stub_writes_the_streamed_lines_into_the_task_log(self):
        self.streamed_run()
        with open(self.store().path("logs", "T-1.stdout.log")) as handle:
            self.assertIn("working on T-1", handle.read())

    def test_a_queue_entry_without_the_stream_key_is_unchanged(self):
        """The key is opt in: the existing suite drives the same stub with no stream at all."""
        self.complete_run()
        with open(self.store().path("logs", "T-1.stdout.log")) as handle:
            body = handle.read()
        self.assertIn("stub_done", body)
        self.assertEqual([], [event for raw in body.splitlines() for event in tail.decode(raw)])

    def test_tailing_the_finished_run_prints_every_task_in_manifest_order(self):
        self.streamed_run()
        code, out = self.call("tail", self.manifest_path)
        self.assertEqual(code, cli.EXIT_OK, out)
        for task_id in ("T-1", "T-2", "T-3"):
            self.assertIn("working on %s" % task_id, out)
        positions = [out.index("working on %s" % task_id) for task_id in ("T-1", "T-2", "T-3")]
        self.assertEqual(positions, sorted(positions))

    def test_the_closeout_output_lands_between_its_task_and_the_next(self):
        self.streamed_run()
        _, out = self.call("tail", self.manifest_path)
        self.assertLess(out.index("working on T-1"), out.index("closing out T-1"))
        self.assertLess(out.index("closing out T-1"), out.index("working on T-2"))

    def test_every_phase_header_names_its_task_and_phase(self):
        self.streamed_run()
        _, out = self.call("tail", self.manifest_path)
        for task_id in ("T-1", "T-2", "T-3"):
            self.assertIn("== %s %s ==" % (task_id, tail.PHASE_TASK), out)
            self.assertIn("== %s %s ==" % (task_id, tail.PHASE_CLOSEOUT), out)

    def test_the_tool_calls_render_decoded_rather_than_as_stream_json(self):
        self.streamed_run()
        _, out = self.call("tail", self.manifest_path)
        self.assertIn("compound-engineering:ce-work", out)
        self.assertNotIn('"type": "tool_use"', out)


if __name__ == "__main__":
    unittest.main()
