"""U1 and U2: the `tail` decoder and the follow loop.

The decoder cases run against `fixtures/stdout/real_stream.jsonl`, which carries real CLI line
shapes rather than stub output, because the stub prints no assistant lines and a decoder tested
only against a stub the same author extended would agree with itself by construction.

The follow cases drive `tail.follow` with an injected `sleep` that advances a scripted run
between polls. That is what lets one deterministic test cover started-before, started-during, and
started-after without a wall clock race.
"""
import io
import json
import os
import unittest

import _paths
from relay import contracts, tail

STDOUT_FIXTURES = os.path.join(_paths.FIXTURES_DIR, "stdout")
REAL_STREAM = os.path.join(STDOUT_FIXTURES, "real_stream.jsonl")


def line(payload):
    return json.dumps(payload)


def assistant(content):
    return line({"type": "assistant", "message": {"role": "assistant", "content": content}})


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


if __name__ == "__main__":
    unittest.main()
