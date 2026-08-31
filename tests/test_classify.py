"""U7: every fixture maps to exactly one class and the expected findings."""
import os
import unittest
from types import SimpleNamespace

import _paths
from relay import classify, contracts, summary
from test_summary import FINDING_ROWS

FIXTURES = os.path.join(_paths.FIXTURES_DIR, "transcripts")
BACKEND_FIXTURES = os.path.join(_paths.FIXTURES_DIR, "backends")
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
        self.assertEqual(r["envelope"]["learnings"], [])
        self.assertEqual(r["envelope"]["plan_path"], "docs/plans/2026-08-25-1400-feat-t1-plan.md")
        self.assertEqual(r["findings"], [])
        self.assertEqual(r["malformed_lines"], 0)
        self.assertEqual(r["tool_calls"], 2)
        self.assertEqual(r["undetectable"], [], "Backends U6: Claude's evidence has no blind spot")

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
        self.assertEqual(gates[0]["detail"], contracts.PATH_GATE_CLAUDE_DIR)
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

    def test_missing_transcript_is_a_runner_fault_not_a_crash(self):
        """R20, KTD5. Reading nothing is still not fatal, but the class is the runner fault one.

        no_envelope would be a claim about the task, that the process ran and printed no
        envelope, and nobody observed that. The runner never opened the evidence.
        """
        r = classify.classify(os.path.join(FIXTURES, "does-not-exist.jsonl"), EXITED)
        self.assertFalse(r["transcript_present"])
        self.assertEqual(r["halt_class"], contracts.HALT_UNEXPECTED_ERROR)
        self.assertFalse(r["routable"])

    def test_missing_transcript_records_findings_as_unavailable_not_as_none_found(self):
        """R13. A reader has to be able to tell "we looked and found none" from "we could not
        look", so the marker is set and the no_envelope finding is never appended."""
        r = classify.classify(os.path.join(FIXTURES, "does-not-exist.jsonl"), EXITED)
        self.assertTrue(r["findings_unavailable"])
        self.assertIsNone(r["findings"])

    def test_a_readable_run_with_no_envelope_still_reports_its_findings_as_available(self):
        """The narrowing must not swallow the ordinary silent task: the evidence opened, the
        envelope was genuinely absent, and both the class and the finding stand."""
        r = run("no_envelope.jsonl")
        self.assertTrue(r["transcript_present"])
        self.assertFalse(r["findings_unavailable"])
        self.assertEqual(r["halt_class"], contracts.HALT_NO_ENVELOPE)
        self.assertIn(contracts.HALT_NO_ENVELOPE, classes(r))

    def test_a_timed_out_run_whose_transcript_is_missing_is_still_the_timeout_class(self):
        """KTD4: a killed process that never wrote its evidence is the timeout class, not a
        runner fault. Timeout keeps beating everything."""
        r = classify.classify(os.path.join(FIXTURES, "does-not-exist.jsonl"), TIMED_OUT)
        self.assertEqual(r["halt_class"], contracts.HALT_TIMEOUT)

    def test_the_digest_carries_the_unavailable_marker_and_the_pinned_key_set_still_matches(self):
        """The marker is part of the digest contract, so it survives the write to disk that
        run.py and closeout.py read, and classify still sets exactly DIGEST_KEYS."""
        import json
        import tempfile
        r = classify.classify(os.path.join(FIXTURES, "does-not-exist.jsonl"), EXITED)
        self.assertIn("findings_unavailable", contracts.DIGEST_KEYS)
        self.assertEqual(set(r), set(contracts.DIGEST_KEYS))
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            path = handle.name
        classify.write_digest(r, path)
        with open(path, encoding="utf-8") as handle:
            written = json.load(handle)
        os.unlink(path)
        self.assertTrue(written["findings_unavailable"])
        self.assertIsNone(written["findings"])


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

    def test_the_required_skill_is_named_in_the_backends_own_form(self):
        """Backends KTD15's fourth call site. A finding that names an invocation the task's CLI
        cannot run tells the operator to fix it with a command that does not exist there."""
        self.assertEqual(classify.required_skill_for("code-review", backend="codex"),
                         "$ce-code-review")
        self.assertEqual(classify.required_skill_for("code-review", backend="grok"),
                         "/ce-code-review")
        self.assertIsNone(classify.required_skill_for("$ce-work", backend="codex"))
        self.assertIsNone(classify.required_skill_for("/ce-work", backend="grok"))

    def test_a_bare_sigil_is_not_proof_of_plugin_ownership(self):
        """`$` and `/` are how those CLIs invoke every skill, the harness's included, so the
        prefix alone cannot decide whether a call was already qualified."""
        self.assertEqual(classify.required_skill_for("$code-review", backend="codex"),
                         "$ce-code-review")
        self.assertEqual(classify.required_skill_for("/code-review", backend="grok"),
                         "/ce-code-review")

    def test_a_prefixed_name_the_plugin_does_not_ship_is_still_a_substitution(self):
        """The one claude outcome this tightening changes. `compound-engineering:code-review` is
        not a skill the plugin ships, so accepting it silently was wrong. A real plugin skill
        outside the pipeline set still returns None, because the bare-name pass finds no match."""
        self.assertEqual(classify.required_skill_for("compound-engineering:code-review"),
                         "compound-engineering:ce-code-review")
        self.assertIsNone(classify.required_skill_for("compound-engineering:ce-debug"))

    def test_a_claude_qualified_call_on_another_backend_is_a_substitution(self):
        self.assertEqual(classify.required_skill_for("compound-engineering:ce-work",
                                                     backend="codex"),
                         "$ce-work")


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


class LearningsField(unittest.TestCase):
    """R5, R6, R7: the envelope's fifth, optional key. Parsed the same way as blockers and
    changed_files, only ever present when an envelope was found at all."""

    def test_a_bulleted_learning_is_captured(self):
        env = classify.parse_envelope(
            "```relay-envelope\nstatus: complete\nlearnings:\n"
            "- the timeout was upstream, not in this service\n```")
        self.assertEqual(env["learnings"], ["the timeout was upstream, not in this service"])

    def test_an_absent_learnings_key_is_empty(self):
        env = classify.parse_envelope("status: complete\nblockers: none\nchanged_files:\n- a.py\nplan_path: p.md\n")
        self.assertEqual(env["learnings"], [])

    def test_a_present_but_empty_learnings_key_stays_empty(self):
        env = classify.parse_envelope("status: complete\nlearnings:\nplan_path: p.md\n")
        self.assertEqual(env["learnings"], [])

    def test_a_multi_line_learnings_paragraph_stops_at_the_next_key(self):
        env = classify.parse_envelope(
            "status: complete\nlearnings:\nfirst line of prose\nsecond line of prose\n\nplan_path: p.md\n")
        self.assertEqual(env["learnings"], ["first line of prose", "second line of prose"])

    def test_a_colon_led_line_inside_learnings_truncates_there(self):
        """KTD2: `_list_after`'s shared paragraph branch stops at any bare word followed by a
        colon, not only the real envelope keys."""
        env = classify.parse_envelope(
            "status: complete\nlearnings:\nthe cause was subtle\nCause: the timeout was upstream\n")
        self.assertEqual(env["learnings"], ["the cause was subtle"])

    def test_a_status_shaped_line_inside_learnings_overrides_the_declared_status(self):
        """Adversarial review on T-7: STATUS_RE scans the whole block and the last match wins
        (classify.py's own `matches[-1]`), so a learnings line that itself reads as a status
        declaration silently reclassifies the task. Pre-existing in `_list_after`'s shared
        parsing (the same shape already reaches `blockers` today); pinned here, not fixed, since
        changing how `status` is read is out of scope for the learnings key (R10). The brief's ask
        (`brief-local-merge.md`) warns the task away from writing a line shaped this way."""
        env = classify.parse_envelope(
            "status: complete\nlearnings:\nStatus: failed to reproduce until I disabled caching\n")
        self.assertEqual(env["status"], "failed")

    def test_a_present_learning_reaches_the_digest_through_classify(self):
        """The testing reviewer on T-7: prove the extraction through classify.classify() end to
        end, not only through parse_envelope() directly."""
        import json
        import tempfile
        lines = [
            {"type": "assistant", "isSidechain": False, "message": {"role": "assistant", "content": [
                {"type": "text", "text": (
                    "```relay-envelope\nstatus: complete\nblockers:\nchanged_files:\n"
                    "plan_path: docs/plans/x.md\nlearnings:\n"
                    "- the retry helper already existed; no need to write a new one\n```")}]}},
        ]
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as handle:
            for line in lines:
                handle.write(json.dumps(line) + "\n")
        r = classify.classify(handle.name, EXITED)
        os.unlink(handle.name)
        self.assertEqual(r["envelope"]["learnings"],
                         ["the retry helper already existed; no need to write a new one"])

    def test_no_status_line_yields_no_envelope_even_with_learnings_present(self):
        self.assertIsNone(classify.parse_envelope("learnings:\n- something\n"))


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


CLAUDE_BACKEND_FIXTURES = os.path.join(BACKEND_FIXTURES, "claude")


def run_claude_fixture(name):
    return classify.classify(os.path.join(CLAUDE_BACKEND_FIXTURES, name),
                             SimpleNamespace(timed_out=False, exit_code=0, log_path=None))


class ClaudeBackendFixtures(unittest.TestCase):
    """Backends U5: the U1 capture fixtures under tests/fixtures/backends/claude/ were never
    read through classify() before this unit; the pre-existing Fixtures class above exercises
    only the older, hand-written tests/fixtures/transcripts/ set."""

    def test_session_transcript_complete_normalizes_to_a_complete_envelope(self):
        r = run_claude_fixture("session-transcript-complete.jsonl")
        self.assertEqual(r["envelope"]["status"], "complete")
        self.assertTrue(r["routable"])
        self.assertEqual(r["undetectable"], [])

    def test_session_transcript_blocked_normalizes_to_a_blocked_envelope(self):
        r = run_claude_fixture("session-transcript-blocked.jsonl")
        self.assertEqual(r["envelope"]["status"], "blocked")
        self.assertTrue(r["envelope"]["blockers"][0].startswith(
            "Cannot move/comment the tracker card for T-1"))

    def test_closeout_terminal_line_past_the_200_character_head_is_still_readable(self):
        r = run_claude_fixture("closeout-stdout.jsonl")
        self.assertTrue(r["last_message_tail"].rstrip().endswith("Documentation skipped"))

    def test_the_real_bash_denial_names_bash_and_the_command(self):
        """The fixture that found DENIAL_REGEX did not match a real Bash denial at all: the
        message names the command between the tool and the verdict ("Permission to use Bash
        with command ... has been denied."), a shape no hand-written fixture exercised."""
        r = run_claude_fixture("denial-refusal.jsonl")
        denied = [f for f in r["findings"] if f["class"] == contracts.HALT_DENIED_TOOL]
        self.assertEqual(len(denied), 1)
        self.assertEqual(denied[0]["tool"], "Bash")
        self.assertIn("rm -rf", denied[0]["target"])


CODEX_FIXTURES = os.path.join(BACKEND_FIXTURES, "codex")


def run_codex(last_message_name, stdout_name=None, launch=None):
    log_path = os.path.join(CODEX_FIXTURES, stdout_name) if stdout_name else None
    launch = launch or SimpleNamespace(timed_out=False, exit_code=0, log_path=log_path)
    return classify.classify(os.path.join(CODEX_FIXTURES, last_message_name), launch,
                             backend="codex")


class CodexEvidence(unittest.TestCase):
    """Backends U6, U2: the last-message file is the final text; the stdout log is tool calls
    and the decoded-event count only."""

    def test_last_message_complete_plus_stdout_normalizes_to_a_complete_envelope(self):
        r = run_codex("last-message-complete.txt", "stdout-complete.jsonl")
        self.assertEqual(r["envelope"]["status"], "complete")
        self.assertTrue(r["routable"])
        self.assertGreater(r["tool_calls"], 0)

    def test_last_message_blocked_plus_stdout_normalizes_to_a_blocked_envelope(self):
        r = run_codex("last-message-blocked.txt", "stdout-blocked.jsonl")
        self.assertEqual(r["envelope"]["status"], "blocked")
        self.assertTrue(r["envelope"]["blockers"][0].startswith(
            "The project’s Slack webhook"))

    def test_closeout_terminal_line_past_the_200_character_head_is_still_readable(self):
        r = run_codex("closeout-last-message-skipped-long.txt", "closeout-stdout.jsonl")
        self.assertTrue(r["transcript_present"])
        self.assertTrue(r["last_message_tail"].rstrip().endswith("Documentation skipped"))

    def test_the_stray_non_json_line_is_skipped_without_losing_the_events_around_it(self):
        r = run_codex("last-message-complete.txt", "stdout-complete.jsonl")
        self.assertEqual(r["malformed_lines"], 1, "the launcher's stderr merge line")
        self.assertGreater(r["tool_calls"], 0)

    def test_a_multi_path_file_change_synthesizes_one_edit_block_per_path(self):
        """No captured fixture exercises a multi-path file_change; this pins the plan's own
        stated design (one block per entry in item['changes']) against a hand-built event."""
        import json
        import tempfile
        module = classify.backends.build("codex")
        event = {"type": "item.completed", "item": {"id": "item_1", "type": "file_change",
                 "changes": [{"path": "a.py", "kind": "add"}, {"path": "b.py", "kind": "add"}]}}
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as handle:
            handle.write(json.dumps(event) + "\n")
        evidence = module.normalize_transcript(
            os.path.join(CODEX_FIXTURES, "last-message-complete.txt"), log_path=handle.name)
        os.unlink(handle.name)
        edit_paths = [block["input"]["file_path"] for _number, line in evidence.lines
                     for block in line["message"]["content"] if block.get("name") == "Edit"]
        self.assertEqual(edit_paths, ["a.py", "b.py"])

    def test_a_timed_out_run_with_no_last_message_file_stays_the_timeout_class(self):
        r = run_codex("does-not-exist.txt", launch=SimpleNamespace(
            timed_out=True, exit_code=-15, log_path=None))
        self.assertEqual(r["halt_class"], contracts.HALT_TIMEOUT)

    def test_a_stdout_log_that_decodes_zero_events_is_not_readable(self):
        empty_log = os.path.join(_paths.FIXTURES_DIR, "does-not-exist.jsonl")
        module = classify.backends.build("codex")
        last_message = os.path.join(CODEX_FIXTURES, "last-message-complete.txt")
        evidence = module.normalize_transcript(last_message, log_path=empty_log)
        self.assertEqual(evidence.decoded_events, 0)
        self.assertFalse(module.readable(last_message, evidence))

    def test_denied_path_gate_and_tracker_write_and_skill_substitution_are_all_unavailable(self):
        r = run_codex("last-message-complete.txt", "stdout-complete.jsonl")
        self.assertEqual(r["undetectable"], sorted([
            contracts.HALT_DENIED_TOOL, contracts.HALT_PATH_GATE,
            contracts.HALT_SKILL_SUBSTITUTION, contracts.HALT_TRACKER_WRITE_DENIED,
        ]))
        self.assertEqual(classes(r), [])


class UnenforcedAudit(unittest.TestCase):
    """Backends U10: walk Codex tool_use commands against DISALLOWED_TOOLS."""

    def _event(self, command):
        return {
            "type": "item.completed",
            "item": {
                "id": "item_1",
                "type": "command_execution",
                "command": command,
                "aggregated_output": "",
                "exit_code": 0,
                "status": "completed",
            },
        }

    def _classify(self, command, patterns=None, backend="codex", last_message="last-message-complete.txt"):
        import json
        import tempfile
        event = self._event(command)
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as handle:
            handle.write(json.dumps(event) + "\n")
            log_path = handle.name
        try:
            launch = SimpleNamespace(timed_out=False, exit_code=0, log_path=log_path)
            return classify.classify(
                os.path.join(CODEX_FIXTURES, last_message), launch,
                backend=backend,
                disallow_patterns=patterns or list(contracts.DISALLOWED_TOOLS),
            )
        finally:
            os.unlink(log_path)

    def test_a_wrapped_git_clean_is_a_finding_and_still_routable(self):
        r = self._classify("/bin/zsh -lc 'pwd && git clean -fd'")
        hits = [f for f in r["findings"] if f["class"] == contracts.UNENFORCED_DISALLOWED]
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["tool"], "Bash")
        self.assertIn("git clean -fd", hits[0]["argument"])
        self.assertEqual(hits[0]["pattern"], "Bash(git clean*)")
        self.assertTrue(r["routable"])
        self.assertIsNone(r["halt_class"])
        self.assertIsInstance(r["tool_calls"], int)

    def test_a_wrapped_rm_rf_is_a_finding_and_classify_does_not_halt(self):
        r = self._classify("/bin/zsh -lc 'ls && rm -rf /tmp/x'")
        hits = [f for f in r["findings"] if f["class"] == contracts.UNENFORCED_DISALLOWED]
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["pattern"], "Bash(rm -rf*)")
        self.assertIsNone(r["halt_class"])
        self.assertTrue(r["routable"])

    def test_a_zsh_lc_rm_rf_without_and_still_matches(self):
        r = self._classify("/bin/zsh -lc 'rm -rf /tmp/x'")
        hits = [f for f in r["findings"] if f["class"] == contracts.UNENFORCED_DISALLOWED]
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["pattern"], "Bash(rm -rf*)")

    def test_git_c_reset_hard_matches_the_destructive_glob(self):
        r = self._classify("/bin/zsh -lc 'git -C . reset --hard HEAD'")
        hits = [f for f in r["findings"] if f["class"] == contracts.UNENFORCED_DISALLOWED]
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["pattern"], "Bash(git reset --hard*)")

    def test_a_wrapped_kill_is_a_finding_on_the_unenforced_audit_path(self):
        """Round six #40's fix (kill/pkill/killall in DISALLOWED_TOOLS) reaches Codex the same
        way every other entry does: through this audit, not only through scan_self_kill."""
        r = self._classify("/bin/zsh -lc 'ps aux | grep python && kill -9 61799'")
        hits = [f for f in r["findings"] if f["class"] == contracts.UNENFORCED_DISALLOWED]
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["pattern"], "Bash(kill*)")
        self.assertIsNone(r["halt_class"])
        self.assertTrue(r["routable"])

    def test_a_claude_denial_does_not_also_raise_the_unenforced_class(self):
        r = classify.classify(
            os.path.join(FIXTURES, "path_gate.jsonl"), EXITED,
            backend="claude",
            disallow_patterns=list(contracts.DISALLOWED_TOOLS),
        )
        self.assertNotIn(contracts.UNENFORCED_DISALLOWED, classes(r))
        self.assertIn(contracts.HALT_PATH_GATE, classes(r))

    def test_unreadable_evidence_does_not_look_like_a_clean_audit(self):
        empty_log = os.path.join(_paths.FIXTURES_DIR, "does-not-exist.jsonl")
        launch = SimpleNamespace(timed_out=False, exit_code=0, log_path=empty_log)
        r = classify.classify(
            os.path.join(CODEX_FIXTURES, "last-message-complete.txt"), launch,
            backend="codex",
            disallow_patterns=list(contracts.DISALLOWED_TOOLS),
        )
        self.assertTrue(r["findings_unavailable"])
        self.assertIsNone(r["findings"])


GROK_FIXTURES = os.path.join(BACKEND_FIXTURES, "grok")


def run_grok(name):
    return classify.classify(os.path.join(GROK_FIXTURES, name),
                             SimpleNamespace(timed_out=False, exit_code=0, log_path=None),
                             backend="grok")


class GrokEvidence(unittest.TestCase):
    """Backends U6, U3: `updates.jsonl`'s `agent_message_chunk` events are already complete
    per-turn text, and its embedded `tool_call_update` denial is a real, detectable finding."""

    def test_a_null_x_ai_tool_meta_does_not_crash_the_tool_name_lookup(self):
        """Adversarial review: `dict.get(key, default)` only supplies `default` when `key` is
        absent, so `_meta: {"x.ai/tool": null}` (the key present, the value not a dict) must not
        reach a bare `.get("name")` on `None`. Falls back to `title` instead of raising."""
        module = classify.backends.build("grok")
        update = {"toolCallId": "call-1", "title": "read_file", "_meta": {"x.ai/tool": None}}
        name = module._tool_name_of(update)
        self.assertEqual(name, "read_file")

    def test_session_transcript_complete_normalizes_to_a_complete_envelope(self):
        r = run_grok("session-transcript-complete.jsonl")
        self.assertEqual(r["envelope"]["status"], "complete")
        self.assertTrue(r["routable"])

    def test_session_transcript_complete_also_carries_an_embedded_denial(self):
        """The same run's own tool_call_update, not the stdout-shaped denial-refusal.jsonl
        fixture, which is tail's evidence, not classify's (KTD4)."""
        r = run_grok("session-transcript-complete.jsonl")
        denied = [f for f in r["findings"] if f["class"] == contracts.HALT_DENIED_TOOL]
        self.assertEqual(len(denied), 1)
        self.assertEqual(denied[0]["tool"], "Bash", "run_terminal_command maps to Bash")
        self.assertIn("rm -rf", denied[0]["target"])

    def test_session_transcript_blocked_normalizes_to_a_blocked_envelope(self):
        r = run_grok("session-transcript-blocked.jsonl")
        self.assertEqual(r["envelope"]["status"], "blocked")
        self.assertTrue(r["envelope"]["blockers"][0].startswith(
            "The project's ops-kept Slack webhook URL"))

    def test_auto_mode_blocked_this_action_is_not_a_denial(self):
        """session-transcript-blocked.jsonl also carries a tool_call_update failed for Grok's
        own auto-mode judgment, phrased differently from a --deny rule denial; it must not be
        read as one."""
        r = run_grok("session-transcript-blocked.jsonl")
        denied = [f for f in r["findings"] if f["class"] == contracts.HALT_DENIED_TOOL]
        self.assertEqual(denied, [])

    def test_skill_substitution_is_unavailable_but_denial_still_reports_normally(self):
        r = run_grok("session-transcript-complete.jsonl")
        self.assertEqual(r["undetectable"], [contracts.HALT_SKILL_SUBSTITUTION])
        self.assertTrue(any(f["class"] == contracts.HALT_DENIED_TOOL for f in r["findings"]))

    def test_closeout_terminal_line_past_the_200_character_head_is_still_readable(self):
        """closeout-last-message-skipped-long.txt is real captured prose with no session-file
        companion; wrapped as one agent_message_chunk line, it proves the 200-character head/tail
        split through this normalizer without inventing new content."""
        import json
        import tempfile
        with open(os.path.join(GROK_FIXTURES, "closeout-last-message-skipped-long.txt"),
                  encoding="utf-8") as handle:
            text = handle.read()
        line = {"timestamp": 0, "method": "session/update", "params": {"sessionId": "s",
                "update": {"sessionUpdate": "agent_message_chunk",
                           "content": {"type": "text", "text": text}}}}
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as handle:
            handle.write(json.dumps(line) + "\n")
        r = classify.classify(handle.name,
                              SimpleNamespace(timed_out=False, exit_code=0, log_path=None),
                              backend="grok")
        os.unlink(handle.name)
        self.assertTrue(r["last_message_tail"].rstrip().endswith("Documentation skipped"))


class SelfKillScan(unittest.TestCase):
    """Round six #40: a task killed its own Runner with `kill -9 <pids...>` before the record's
    `transcript_path` was ever written back to state (run.py writes it only after `launch.launch`
    returns). `classify.scan_self_kill` reads the deterministic raw stdout log instead, so it
    works even on a record state never got to finish describing."""

    def _log(self, tmp_dir, lines):
        import json
        path = os.path.join(tmp_dir, "self-kill.stdout.log")
        with open(path, "w", encoding="utf-8") as handle:
            for obj in lines:
                handle.write(json.dumps(obj) + "\n")
        return path

    def _claude_line(self, command):
        return {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Bash", "input": {"command": command}}]}}

    def test_a_kill_naming_the_victim_pid_is_a_finding(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            log = self._log(tmp, [self._claude_line("kill -9 57246 61799 61800")])
            finding = classify.scan_self_kill(log, 61799)
            self.assertIsNotNone(finding)
            self.assertEqual(finding["class"], contracts.RUNNER_SELF_KILL)
            self.assertIn("61799", finding["pids"].split())
            self.assertEqual(finding["command"], "kill -9 57246 61799 61800")
            self.assertEqual(finding["victim_pid"], "61799")

    def test_a_kill_not_naming_the_victim_pid_is_not_a_finding(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            log = self._log(tmp, [self._claude_line("kill -9 57246 61799 61800")])
            self.assertIsNone(classify.scan_self_kill(log, 99999))

    def test_an_unrelated_command_naming_the_victim_pid_is_not_a_finding(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            log = self._log(tmp, [self._claude_line("git status"),
                                   {"type": "assistant", "message": {"content": [
                                       {"type": "text", "text": "pid 61799 looked fine"}]}}])
            self.assertIsNone(classify.scan_self_kill(log, 61799))

    def test_a_bare_signal_flag_is_never_extracted_as_a_pid(self):
        """`_PID_TOKEN_RE` requires 2+ digits, so the `-9` signal flag in `kill -9 <pid>` is
        never itself extracted as a PID token alongside the real one."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            log = self._log(tmp, [self._claude_line("kill -9 61799")])
            finding = classify.scan_self_kill(log, 61799)
            self.assertNotIn("9", finding["pids"].split())

    def test_a_command_nested_deeper_than_the_claude_shape_still_matches(self):
        """The scan walks every string leaf rather than one backend's known field path, so a
        differently-nested JSON shape (e.g. another backend's event) still matches."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            nested = {"type": "item.completed", "item": {"id": "x", "type": "command_execution",
                      "detail": {"argv": {"raw": "pkill -9 -f 61799"}}}}
            log = self._log(tmp, [nested])
            finding = classify.scan_self_kill(log, 61799)
            self.assertIsNotNone(finding)

    def test_a_missing_log_file_returns_none(self):
        self.assertIsNone(classify.scan_self_kill("/nonexistent/path.stdout.log", 61799))

    def test_a_log_file_of_only_malformed_json_returns_none(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "malformed.stdout.log")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("not json\n{also not json\n")
            self.assertIsNone(classify.scan_self_kill(path, 61799))

    def test_prose_that_merely_starts_with_kill_is_not_a_finding(self):
        """Code review: `fnmatch("killing worker 4821", "kill*")` is true, since fnmatch has no
        word-boundary concept. `_KILL_COMMAND_RE` requires a space or end of string right after
        the command name, so ordinary prose that happens to start with "kill" never matches."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            log = self._log(tmp, [{"type": "assistant", "message": {"content": [
                {"type": "text", "text": "killing worker 4821 now, this is not a runner kill"}]}}])
            self.assertIsNone(classify.scan_self_kill(log, 4821))

    def test_a_pid_named_only_in_a_trailing_chained_command_is_not_a_finding(self):
        """Code review: a leaf like `kill -9 100 && echo pid 61799 done` is two shell segments.
        Matching against the whole leaf would read 61799 as a PID `kill` named; matching against
        each split segment separately confines the PID list to what the kill command itself named."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            log = self._log(tmp, [self._claude_line("kill -9 100 && echo pid 61799 done")])
            self.assertIsNone(classify.scan_self_kill(log, 61799))
            finding = classify.scan_self_kill(log, 100)
            self.assertEqual(finding["command"], "kill -9 100")
            self.assertEqual(finding["pids"], "100")

    def test_killall_names_a_process_not_a_pid_so_it_can_never_match(self):
        """`killall` kills by process name, so a real killall self-kill never carries the
        victim's PID as a literal token in the command text. This is a structural limit of a
        PID-token scan, not a bug: document it instead of leaving it silently unproven."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            log = self._log(tmp, [self._claude_line("killall -9 python")])
            self.assertIsNone(classify.scan_self_kill(log, 61799))


if __name__ == "__main__":
    unittest.main()
