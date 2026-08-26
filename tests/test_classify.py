"""U7: every fixture maps to exactly one class and the expected findings."""
import os
import unittest
from types import SimpleNamespace

import _paths
from relay import classify, contracts, summary
from test_summary import FINDING_ROWS

FIXTURES = os.path.join(_paths.FIXTURES_DIR, "transcripts")
# The shape the Jira adapter's write_tool_patterns() returns in U4 (KTD16).
JIRA_PATTERNS = {"tools": ["mcp__atlassian__"], "bash": [], "paths": []}
MARKDOWN_PATTERNS = {"tools": [], "bash": [], "paths": ["tracker.md"]}
GITHUB_PATTERNS = {"tools": [], "bash": ["gh issue", "gh project item-edit"], "paths": []}

EXITED = SimpleNamespace(timed_out=False, exit_code=0)
TIMED_OUT = SimpleNamespace(timed_out=True, exit_code=-15)

IW83 = os.path.expanduser(
    "~/.claude/projects/-Users-pgutowski-Documents-PhilAI-Integrel-support-workbench/"
    "9581f5c5-2eb9-47c3-bc46-2af7ce43f4be.jsonl"
)


def run(name, launch=EXITED, patterns=None):
    return classify.classify(os.path.join(FIXTURES, name), launch, patterns)


def classes(result):
    return [f["class"] for f in result["findings"]]


class Fixtures(unittest.TestCase):
    def test_success_is_routable_with_a_complete_envelope_and_no_findings(self):
        r = run("success.jsonl")
        self.assertIsNone(r["halt_class"])
        self.assertTrue(r["routable"])
        self.assertEqual(r["envelope"]["status"], "complete")
        self.assertTrue(r["envelope"]["fenced"])
        self.assertEqual(r["envelope"]["changed_files"], ["core/thing.py", "tests/test_thing.py"])
        self.assertEqual(r["envelope"]["blockers"], [])
        self.assertEqual(r["envelope"]["plan_path"], "docs/plans/2026-08-25-1400-feat-t1-plan.md")
        self.assertEqual(r["findings"], [])
        self.assertEqual(r["malformed_lines"], 0)
        self.assertEqual(r["tool_calls"], 2)

    def test_quoted_status_done_does_not_beat_the_fenced_envelope(self):
        """The mid-run text quotes `status: Done` from a card; the final fenced block wins."""
        r = run("success.jsonl")
        self.assertEqual(r["envelope"]["status"], "complete")

    def test_blocked_envelope_captures_the_first_blocker(self):
        r = run("blocked.jsonl")
        self.assertEqual(r["halt_class"], contracts.HALT_BLOCKED_ENVELOPE)
        self.assertFalse(r["routable"])
        self.assertEqual(r["envelope"]["status"], "blocked")
        self.assertTrue(r["envelope"]["blockers"][0].startswith("acceptance criterion 3"))
        self.assertEqual(len(r["envelope"]["blockers"]), 2)

    def test_no_envelope_carries_the_first_200_characters(self):
        r = run("no_envelope.jsonl")
        self.assertEqual(r["halt_class"], contracts.HALT_NO_ENVELOPE)
        self.assertIsNone(r["envelope"])
        self.assertEqual(len(r["last_message"]), 200)
        self.assertTrue(r["last_message"].startswith("Round two applied."))
        self.assertIn(contracts.HALT_NO_ENVELOPE, classes(r))

    def test_path_gate_finding_names_the_path_and_the_tool(self):
        r = run("path_gate.jsonl")
        gates = [f for f in r["findings"] if f["class"] == contracts.HALT_PATH_GATE]
        self.assertEqual(len(gates), 1)
        self.assertEqual(gates[0]["tool"], "Edit")
        self.assertTrue(gates[0]["target"].endswith(".claude/skills/itg-brief/SKILL.md"))
        self.assertIsNotNone(gates[0]["tool_use_line"])
        self.assertEqual(r["halt_class"], contracts.HALT_PATH_GATE)
        self.assertIn(contracts.HALT_NO_ENVELOPE, classes(r), "the missing envelope stays visible")

    def test_skill_substitution_names_bare_and_required(self):
        r = run("skill_substitution.jsonl")
        subs = [f for f in r["findings"] if f["class"] == contracts.HALT_SKILL_SUBSTITUTION]
        self.assertEqual(len(subs), 2)
        self.assertEqual(subs[0]["name"], "code-review")
        self.assertEqual(subs[0]["required"], "compound-engineering:ce-code-review")
        self.assertTrue(r["routable"], "a substitution is a warning; the verdict is unchanged")
        self.assertEqual(classify.finding_line(subs[0]), "ran code-review instead of compound-engineering:ce-code-review")

    def test_tracker_denied_with_jira_patterns(self):
        r = run("tracker_denied.jsonl", patterns=JIRA_PATTERNS)
        denied = [f for f in r["findings"] if f["class"] == contracts.HALT_TRACKER_WRITE_DENIED]
        self.assertEqual(len(denied), 1)
        self.assertEqual(denied[0]["tool"], "mcp__atlassian__transitionJiraIssue")
        self.assertTrue(r["routable"], "verify decides partial_landing from git and the card")
        self.assertEqual(classify.finding_line(denied[0]), "code landed, card unmoved: mcp__atlassian__transitionJiraIssue denied")

    def test_tracker_denied_without_patterns_is_a_plain_denial(self):
        r = run("tracker_denied.jsonl", patterns=MARKDOWN_PATTERNS)
        self.assertEqual(classes(r), [contracts.HALT_DENIED_TOOL])
        self.assertTrue(r["findings"][0]["target"].startswith('{"cloudId"'))

    def test_last_assistant_text_wins_over_nine_end_turns(self):
        r = run("multi_end_turn.jsonl")
        self.assertEqual(r["envelope"]["status"], "complete")
        self.assertTrue(r["routable"])

    def test_malformed_line_is_counted_and_skipped(self):
        r = run("malformed.jsonl")
        self.assertEqual(r["malformed_lines"], 1)
        self.assertTrue(r["routable"])

    def test_timeout_beats_everything(self):
        r = run("success.jsonl", launch=TIMED_OUT)
        self.assertEqual(r["halt_class"], contracts.HALT_TIMEOUT)
        self.assertFalse(r["routable"])
        self.assertEqual(r["envelope"]["status"], "complete")

    def test_missing_transcript_is_no_envelope_not_a_crash(self):
        r = classify.classify(os.path.join(FIXTURES, "does-not-exist.jsonl"), EXITED)
        self.assertFalse(r["transcript_present"])
        self.assertEqual(r["halt_class"], contracts.HALT_NO_ENVELOPE)


class DenialTargets(unittest.TestCase):
    def test_bash_command_naming_claude_dir_stays_denied_tool(self):
        """KTD6 promotes to path_gate from input.file_path only; a denied Bash command that
        merely mentions the directory is a plain denial with the command as its target."""
        import json
        import tempfile
        lines = [
            {"type": "assistant", "isSidechain": False, "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "git checkout -- /repo/.claude/x"}}]}},
            {"type": "user", "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1", "is_error": True,
                 "content": "Permission to use Bash has been denied because Claude Code is running in don't ask mode."}]}},
            {"type": "assistant", "isSidechain": False, "message": {"role": "assistant", "content": [{"type": "text", "text": "stopped"}]}},
        ]
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as handle:
            for line in lines:
                handle.write(json.dumps(line) + "\n")
        r = classify.classify(handle.name, EXITED)
        os.unlink(handle.name)
        self.assertEqual([f["class"] for f in r["findings"]], [contracts.HALT_DENIED_TOOL, contracts.HALT_NO_ENVELOPE])
        self.assertEqual(r["findings"][0]["target"], "git checkout -- /repo/.claude/x")
        self.assertEqual(r["halt_class"], contracts.HALT_NO_ENVELOPE)


class EnvelopeParsing(unittest.TestCase):
    def test_unfenced_last_match_wins(self):
        text = "Earlier I wrote status: blocked while waiting.\n\nFinal:\n- status: complete\n- blockers: none\n"
        env = classify.parse_envelope(text)
        self.assertEqual(env["status"], "complete")
        self.assertFalse(env["fenced"])
        self.assertEqual(env["blockers"], [])

    def test_status_word_inside_prose_does_not_match(self):
        self.assertIsNone(classify.parse_envelope("The card status: Done was stale, nothing else here."))

    def test_inline_blocker(self):
        env = classify.parse_envelope("```relay-envelope\nstatus: blocked\nblockers: needs a decision on the token\n```")
        self.assertEqual(env["blockers"], ["needs a decision on the token"])

    def test_bold_markdown_keys_parse(self):
        env = classify.parse_envelope("**status**: `failed`\n**blockers**:\n- the gate is red\n")
        self.assertEqual(env["status"], "failed")
        self.assertEqual(env["blockers"], ["the gate is red"])


class WritePatterns(unittest.TestCase):
    def test_gh_pr_create_is_not_a_tracker_write(self):
        use = {"name": "Bash", "input": {"command": "gh pr create --fill"}}
        self.assertFalse(classify.matches_write_pattern(use, GITHUB_PATTERNS))
        use = {"name": "Bash", "input": {"command": "gh issue close 12"}}
        self.assertTrue(classify.matches_write_pattern(use, GITHUB_PATTERNS))

    def test_markdown_path_match(self):
        use = {"name": "Edit", "input": {"file_path": "/x/repo/tracker.md"}}
        self.assertTrue(classify.matches_write_pattern(use, MARKDOWN_PATTERNS))
        use = {"name": "Edit", "input": {"file_path": "/x/repo/docs/tracker.md.bak"}}
        self.assertFalse(classify.matches_write_pattern(use, MARKDOWN_PATTERNS))

    def test_required_skill_mapping(self):
        self.assertEqual(classify.required_skill_for("code-review"), "compound-engineering:ce-code-review")
        self.assertEqual(classify.required_skill_for("ce-work"), "compound-engineering:ce-work")
        self.assertEqual(classify.required_skill_for("lfg"), "compound-engineering:lfg")
        self.assertIsNone(classify.required_skill_for("compound-engineering:ce-work"))
        self.assertIsNone(classify.required_skill_for("dj-sync"))


class RealTranscriptSmoke(unittest.TestCase):
    def test_proof_run_transcript_classifies_as_the_plan_predicts(self):
        if not os.path.exists(IW83):
            self.skipTest("the 2026-08-25 proof transcript is not on this machine")
        r = classify.classify(IW83, EXITED, JIRA_PATTERNS)
        self.assertEqual(r["malformed_lines"], 0)
        self.assertEqual(r["line_count"], 1127)
        self.assertEqual(r["tool_calls"], 261)
        self.assertIn(r["halt_class"], (contracts.HALT_PATH_GATE, contracts.HALT_BLOCKED_ENVELOPE, contracts.HALT_NO_ENVELOPE))
        gates = [f for f in r["findings"] if f["class"] == contracts.HALT_PATH_GATE]
        subs = [f for f in r["findings"] if f["class"] == contracts.HALT_SKILL_SUBSTITUTION]
        self.assertEqual(len(gates), 1)
        self.assertTrue(gates[0]["target"].endswith(".claude/skills/itg-brief/SKILL.md"))
        self.assertEqual(len(subs), 2)
        self.assertEqual(r["halt_class"], contracts.HALT_PATH_GATE)
        self.assertFalse(any(f["class"] == contracts.HALT_TRACKER_WRITE_DENIED for f in r["findings"]),
                         "the Jira transition succeeded in that run")


if __name__ == "__main__":
    unittest.main()


class ParagraphBlockers(unittest.TestCase):
    """First live run: the blocker was written as prose under `blockers:` and the record read
    "no blocker text in the envelope"."""

    def test_a_paragraph_under_blockers_is_one_blocker(self):
        env = classify.parse_envelope(
            "```relay-envelope\nstatus: blocked\nblockers:\n"
            "Cannot move the tracker card: no card id was provided in this session.\n"
            "changed_files: toolkit/stats.py, tests/test_stats.py\nplan_path: docs/plans/x.md\n```")
        self.assertEqual(env["blockers"],
                         ["Cannot move the tracker card: no card id was provided in this session."])
        self.assertEqual(env["changed_files"], ["toolkit/stats.py, tests/test_stats.py"])
        self.assertEqual(env["plan_path"], "docs/plans/x.md")

    def test_a_multi_line_paragraph_stops_at_the_next_key(self):
        env = classify.parse_envelope(
            "status: blocked\nblockers:\nfirst line of prose\nsecond line of prose\n\nplan_path: p.md\n")
        self.assertEqual(env["blockers"], ["first line of prose", "second line of prose"])
        self.assertEqual(env["plan_path"], "p.md")

    def test_an_empty_blockers_key_followed_by_another_key_stays_empty(self):
        env = classify.parse_envelope("status: complete\nblockers:\nchanged_files:\n- a.py\nplan_path: p.md\n")
        self.assertEqual(env["blockers"], [])
        self.assertEqual(env["changed_files"], ["a.py"])


class FindingLines(unittest.TestCase):
    """`classify.finding_line` feeds the closeout brief, which the Closeout process reads when it
    writes the tracker card. The same table `tests/test_summary.py` renders through the summary
    is rendered here through this entry point, so the brief cannot carry a placeholder the
    summary would have caught."""

    def test_no_placeholder_survives_a_finding_line(self):
        for halt_class, (finding, raiser) in sorted(FINDING_ROWS.items()):
            with self.subTest(halt_class=halt_class, raiser=raiser):
                line = classify.finding_line(dict(finding, **{"class": halt_class}))
                self.assertNotIn("?", line, "%s renders a placeholder: %s" % (halt_class, line))
                self.assertNotIn("{", line, "%s left a field unfilled: %s" % (halt_class, line))

    def test_the_brief_and_the_summary_render_a_finding_the_same_way(self):
        for halt_class, (finding, raiser) in sorted(FINDING_ROWS.items()):
            with self.subTest(halt_class=halt_class, raiser=raiser):
                finding = dict(finding, **{"class": halt_class})
                self.assertEqual(classify.finding_line(finding),
                                 summary.cause_line(halt_class, finding))

    def test_a_template_field_no_finding_supplies_renders_as_a_placeholder_not_braces(self):
        line = classify.finding_line({"class": contracts.HALT_DENIED_TOOL})
        self.assertNotIn("{", line)
        self.assertIn("?", line)


if __name__ == "__main__":
    unittest.main()
