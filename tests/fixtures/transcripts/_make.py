#!/usr/bin/env python3
"""Regenerates the transcript fixtures from real line shapes.

Every line shape below is copied from the IW-83 session transcript (CLI 2.1.245, plugin
3.23.4): the same keys in the same places, with the long values trimmed. The denial text, the
tool_use and tool_result join by id, the Skill call shape, the stop_reason values, and the
last-prompt line are all verbatim shapes. Run this file to rewrite the fixtures; commit both.
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
CWD = "/tmp/relay-fixture-repo"
SESSION = "00000000-0000-4000-8000-000000000001"
DENIAL_TEXT = (
    "Permission to use {tool} has been denied because Claude Code is running in don't ask mode. "
    "IMPORTANT: You *may* attempt to accomplish this action using other tools that might naturally "
    "be used to accomplish this goal, e.g. using head instead of cat. But you *should not* attempt "
    "to work around this denial in malicious ways. If you believe this capability is essential to "
    "complete the user's request, STOP and explain to the user what you were trying to do and why "
    "you need this permission. Let the user decide how to proceed."
)

_counter = {"n": 0}


def uuid():
    _counter["n"] += 1
    return "%08x-0000-4000-8000-%012x" % (_counter["n"], _counter["n"])


def common(kind, parent, branch="main"):
    return {
        "parentUuid": parent,
        "isSidechain": False,
        "type": kind,
        "uuid": uuid(),
        "timestamp": "2026-08-25T16:05:53.788Z",
        "userType": "external",
        "entrypoint": "sdk-cli",
        "cwd": CWD,
        "sessionId": SESSION,
        "version": "2.1.245",
        "gitBranch": branch,
    }


def user_prompt(parent, text):
    line = common("user", parent)
    line["message"] = {"role": "user", "content": text}
    return line


def assistant(parent, content, stop_reason, branch="main"):
    line = common("assistant", parent, branch)
    line["message"] = {
        "model": "claude-opus-5",
        "id": "msg_%s" % line["uuid"][:8],
        "type": "message",
        "role": "assistant",
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "content": content,
        "usage": {"input_tokens": 2, "output_tokens": 100},
    }
    line["requestId"] = "req_%s" % line["uuid"][:8]
    line["effort"] = "high"
    return line


def tool_use(tool_id, name, inp):
    return {"type": "tool_use", "id": tool_id, "name": name, "input": inp, "caller": {"type": "direct"}}


def text(body):
    return {"type": "text", "text": body}


def tool_result(parent, tool_id, content, is_error=False, branch="main"):
    line = common("user", parent, branch)
    line["promptId"] = "f2f9d2b3-b3ac-4691-8c71-892032988677"
    line["message"] = {
        "role": "user",
        "content": [{"type": "tool_result", "content": content, "is_error": is_error, "tool_use_id": tool_id}],
    }
    line["toolUseResult"] = ("Error: " + content) if is_error else content
    if is_error:
        line["toolDenialKind"] = "permission-rule"
    line["sourceToolAssistantUUID"] = parent
    return line


def last_prompt(leaf, prompt):
    return {"type": "last-prompt", "lastPrompt": prompt[:200], "leafUuid": leaf, "sessionId": SESSION}


PROMPT = "Handle one task only: T-1. Create and stay on relay/T-1. Run compound-engineering:ce-plan then compound-engineering:ce-work mode:return-to-caller."

ENVELOPE_COMPLETE = (
    "Task T-1 is built and reviewed on relay/T-1, nine commits ahead of main, tree clean.\n\n"
    "```relay-envelope\n"
    "status: complete\n"
    "plan_path: docs/plans/2026-08-25-1400-feat-t1-plan.md\n"
    "changed_files:\n"
    "- core/thing.py\n"
    "- tests/test_thing.py\n"
    "blockers: none\n"
    "```\n"
)
ENVELOPE_BLOCKED = (
    "Stopped before the merge step because the card's acceptance criterion cannot be met without a design answer.\n\n"
    "```relay-envelope\n"
    "status: blocked\n"
    "plan_path: docs/plans/2026-08-25-1400-feat-t1-plan.md\n"
    "changed_files:\n"
    "- core/thing.py\n"
    "blockers:\n"
    "- acceptance criterion 3 names a status token nobody has chosen; needs an operator decision\n"
    "- the gate is green on the partial branch\n"
    "```\n"
)
NO_ENVELOPE_TEXT = (
    "Round two applied. One thing remains before merge, and it needs you.\n\n"
    "The Edit was denied under don't-ask mode. Approve it and I will apply, re-run the gate, and merge. "
    "Alternatively, say \"merge without it\" and I will merge the nine commits as they stand.\n\n"
    "Current state, verified just now: branch relay/T-1, 8 commits ahead of main, clean tree, nothing pushed."
)
WAITING_TEXT = (
    "The fix is committed. Kicking off the test suite in the background now.\n\n"
    "Standing by for the test suite's completion notification."
)
# The recap runs past LAST_MESSAGE_CHARS (200) before the waiting phrase, so a finding that
# reused the head-truncated `last_message` would miss it entirely.
WAITING_FAR_TEXT = (
    "Recap of everything applied this round, file by file, so the reviewer has full context "
    "before the next step: contracts.py gained the new finding class, classify.py gained the "
    "detection regex and the append site, both brief templates gained the sharpened rule, and "
    "every touched test file gained matching coverage.\n\n"
    "Standing by for the test suite's completion notification."
)
OUTSTANDING_BY_TEXT = (
    "Nine commits landed. One item outstanding by end of day: the dashboard link needs the "
    "reviewer's sign-off before merge.\n\n" + ENVELOPE_COMPLETE
)
WAITING_THEN_COMPLETE_TEXT = (
    "The gate is still finishing. Standing by for the test suite's completion notification. "
    "It just finished green.\n\n" + ENVELOPE_COMPLETE
)
WILL_RESUME_TEXT = (
    "The fix is committed and the suite is running in the background.\n\n"
    "I will resume once it wraps up."
)
WILL_CHECK_BACK_TEXT = (
    "The fix is committed and the suite is running in the background.\n\n"
    "I will check back once it wraps up."
)
ONCE_IT_FINISHES_TEXT = (
    "Kicking off the full suite in the background now.\n\n"
    "Once it finishes, I'll report back."
)
WAITING_THEN_BLOCKED_TEXT = (
    "Standing by for the test suite's completion notification.\n\n" + ENVELOPE_BLOCKED
)
QUOTED_CARD_TEXT = (
    "Read the card. It currently reads:\n\n"
    "> status: Done\n> assignee: nobody\n\n"
    "That status is stale; the card is really in Backlog. Proceeding with the plan."
)


def build_common_prefix():
    """Prompt, a prefixed Skill call and its result, and a mid-run text quoting a card."""
    lines = []
    p = user_prompt(None, PROMPT)
    lines.append(p)
    a1 = assistant(p["uuid"], [tool_use("toolu_01PLAN", "Skill", {"skill": "compound-engineering:ce-plan", "args": "T-1 do the thing"})], "tool_use")
    lines.append(a1)
    r1 = tool_result(a1["uuid"], "toolu_01PLAN", "Plan ready at docs/plans/2026-08-25-1400-feat-t1-plan.md")
    lines.append(r1)
    a2 = assistant(r1["uuid"], [text(QUOTED_CARD_TEXT)], "end_turn", branch="relay/T-1")
    lines.append(a2)
    a3 = assistant(a2["uuid"], [tool_use("toolu_01WORK", "Skill", {"skill": "compound-engineering:ce-work", "args": "mode:return-to-caller docs/plans/2026-08-25-1400-feat-t1-plan.md"})], "tool_use", branch="relay/T-1")
    lines.append(a3)
    r3 = tool_result(a3["uuid"], "toolu_01WORK", "status: complete\nplan_path: docs/plans/2026-08-25-1400-feat-t1-plan.md", branch="relay/T-1")
    lines.append(r3)
    return lines


def finish(lines, final_text):
    last = assistant(lines[-1]["uuid"], [text(final_text)], "end_turn", branch="relay/T-1")
    lines.append(last)
    lines.append(last_prompt(last["uuid"], PROMPT))
    return lines


def write(name, build, extra_raw=None):
    _counter["n"] = 0
    lines = build()
    path = os.path.join(HERE, name)
    with open(path, "w") as handle:
        for i, line in enumerate(lines):
            handle.write(json.dumps(line) + "\n")
            if extra_raw and extra_raw[0] == i:
                handle.write(extra_raw[1] + "\n")


def success():
    return finish(build_common_prefix(), ENVELOPE_COMPLETE)


def blocked():
    return finish(build_common_prefix(), ENVELOPE_BLOCKED)


def no_envelope():
    return finish(build_common_prefix(), NO_ENVELOPE_TEXT)


def waiting_last_message():
    """Round six #35's own shape: a background gate command and an ending on a promise to
    resume, which headless claude -p never delivers."""
    return finish(build_common_prefix(), WAITING_TEXT)


def waiting_last_message_past_the_head_truncation():
    """The waiting phrase sits past the first 200 characters of the last message."""
    return finish(build_common_prefix(), WAITING_FAR_TEXT)


def will_resume():
    """The regex's second alternative, isolated from the other two."""
    return finish(build_common_prefix(), WILL_RESUME_TEXT)


def will_check_back():
    """The regex's second alternative, the other named phrasing."""
    return finish(build_common_prefix(), WILL_CHECK_BACK_TEXT)


def once_it_finishes():
    """The brief's own quoted cautionary phrase, verbatim, isolated from the other branches."""
    return finish(build_common_prefix(), ONCE_IT_FINISHES_TEXT)


def waiting_then_blocked():
    """R4's gate is `not complete`, not `absent`: a blocked envelope preceded by waiting
    language still trips the finding, unlike a complete one."""
    return finish(build_common_prefix(), WAITING_THEN_BLOCKED_TEXT)


def outstanding_by():
    """`"standing by"` has no word-boundary protection of its own; `"outstanding by"` must not
    match it, the same substring-match trap `_KILL_COMMAND_RE` is anchored to avoid."""
    return finish(build_common_prefix(), OUTSTANDING_BY_TEXT)


def waiting_then_complete():
    """R4: waiting language followed by a delivered, complete envelope did not exhibit the
    failure. The finding must not fire for it."""
    return finish(build_common_prefix(), WAITING_THEN_COMPLETE_TEXT)


def path_gate():
    lines = build_common_prefix()
    edit_path = CWD + "/.claude/skills/itg-brief/SKILL.md"
    a = assistant(lines[-1]["uuid"], [tool_use("toolu_01HwgXRnMVx7V112x3B3N1JJ", "Edit", {"replace_all": False, "file_path": edit_path, "old_string": "three statuses", "new_string": "four statuses"})], "tool_use", branch="relay/T-1")
    lines.append(a)
    lines.append(tool_result(a["uuid"], "toolu_01HwgXRnMVx7V112x3B3N1JJ", DENIAL_TEXT.format(tool="Edit"), is_error=True, branch="relay/T-1"))
    return finish(lines, NO_ENVELOPE_TEXT)


def skill_substitution():
    lines = build_common_prefix()
    for tid in ("toolu_01V3JKgePr4nvMbmgN3jfiqg", "toolu_012ooW2ZNeVRx5RjTRaxMqvB"):
        a = assistant(lines[-1]["uuid"], [tool_use(tid, "Skill", {"skill": "code-review", "args": "high"})], "tool_use", branch="relay/T-1")
        lines.append(a)
        lines.append(tool_result(a["uuid"], tid, "Review complete. Ready with fixes.", branch="relay/T-1"))
    return finish(lines, ENVELOPE_COMPLETE)


def tracker_denied():
    lines = build_common_prefix()
    a = assistant(lines[-1]["uuid"], [tool_use("toolu_01225pg62mnbHZy5GdNBunHH", "mcp__atlassian__transitionJiraIssue", {"cloudId": "00000000-0000-0000-0000-000000000000", "issueIdOrKey": "T-1", "transition": {"id": "31"}})], "tool_use", branch="relay/T-1")
    lines.append(a)
    lines.append(tool_result(a["uuid"], "toolu_01225pg62mnbHZy5GdNBunHH", DENIAL_TEXT.format(tool="mcp__atlassian__transitionJiraIssue"), is_error=True, branch="relay/T-1"))
    return finish(lines, ENVELOPE_COMPLETE)


def multi_end_turn():
    """Nine end_turn stops. The first carries a stale blocked envelope; the last is complete."""
    lines = build_common_prefix()
    first = assistant(lines[-1]["uuid"], [text(ENVELOPE_BLOCKED)], "end_turn", branch="relay/T-1")
    lines.append(first)
    for n in range(7):
        a = assistant(lines[-1]["uuid"], [text("Checkpoint %d reached, continuing." % n)], "end_turn", branch="relay/T-1")
        lines.append(a)
    return finish(lines, ENVELOPE_COMPLETE)


CLOSEOUT_PROMPT = (
    "Relay closeout for T-1. Outcome landed at abc1234def. Two duties, in order: record the "
    "outcome on the tracker, then the compound judgment."
)
CLOSEOUT_COMPLETE_TEXT = (
    "Closed the card and named the merge commit. The task turned on a gate that only fires at "
    "push time, which is worth keeping, so I ran the compound skill and committed the doc.\n\n"
    "Documentation complete"
)
CLOSEOUT_SKIPPED_TEXT = (
    "Closed the card and named the merge commit. Nothing here a future session would get wrong "
    "without a note: the work was routine and the code says what it does.\n\n"
    "Documentation skipped"
)
# Longer than classify.LAST_MESSAGE_CHARS on purpose: the first live run's closeout explained
# its skip at this length and the terminal line fell past the head the parser was handed.
CLOSEOUT_SKIPPED_LONG_TEXT = (
    "Recorded the blocked outcome on tasks.md and committed (not pushed, per the closeout scope). "
    "The task's own branch shows a complete, correct looking implementation with no reported "
    "blockers or denials, so there is no visible cause to document as a learning, just an "
    "unexplained non landing. Nothing here rises to a durable learning worth compounding.\n\n"
    "Documentation skipped"
)
CLOSEOUT_UNFINISHED_TEXT = (
    "Closed the card and named the merge commit. I started the compound judgment and the"
)


def build_closeout_prefix(tracker_write="tracker.md"):
    """The closeout's own shape: one tracker write, then a decision. The tracker write is a Bash
    edit of the markdown tracker, which is what the markdown adapter's write patterns name."""
    lines = []
    p = user_prompt(None, CLOSEOUT_PROMPT)
    lines.append(p)
    a1 = assistant(p["uuid"], [tool_use("toolu_01TRACK", "Edit", {
        "file_path": CWD + "/" + tracker_write, "replace_all": False,
        "old_string": "- [ ] T-1", "new_string": "- [x] T-1"})], "tool_use")
    lines.append(a1)
    lines.append(tool_result(a1["uuid"], "toolu_01TRACK", "Edited " + tracker_write))
    return lines


def closeout_complete():
    lines = build_closeout_prefix()
    a = assistant(lines[-1]["uuid"], [tool_use("toolu_01COMP", "Skill", {
        "skill": "compound-engineering:ce-compound",
        "args": "mode:non-interactive depth:full the gate fires only at push time"})], "tool_use")
    lines.append(a)
    lines.append(tool_result(a["uuid"], "toolu_01COMP", "Documentation complete"))
    return finish(lines, CLOSEOUT_COMPLETE_TEXT)


def closeout_skipped():
    return finish(build_closeout_prefix(), CLOSEOUT_SKIPPED_TEXT)


def closeout_skipped_long():
    return finish(build_closeout_prefix(), CLOSEOUT_SKIPPED_LONG_TEXT)


def closeout_unfinished():
    return finish(build_closeout_prefix(), CLOSEOUT_UNFINISHED_TEXT)


def closeout_tracker_denied():
    """AE1 happens here more often than in the task process: the card write is refused after the
    code has already merged."""
    lines = []
    p = user_prompt(None, CLOSEOUT_PROMPT)
    lines.append(p)
    a = assistant(p["uuid"], [tool_use("toolu_01DENY", "mcp__atlassian__transitionJiraIssue", {
        "cloudId": "00000000-0000-0000-0000-000000000000", "issueIdOrKey": "T-1",
        "transition": {"id": "31"}})], "tool_use")
    lines.append(a)
    lines.append(tool_result(a["uuid"], "toolu_01DENY",
                             DENIAL_TEXT.format(tool="mcp__atlassian__transitionJiraIssue"), is_error=True))
    return finish(lines, CLOSEOUT_SKIPPED_TEXT)


if __name__ == "__main__":
    write("success.jsonl", success)
    write("blocked.jsonl", blocked)
    write("no_envelope.jsonl", no_envelope)
    write("waiting_last_message.jsonl", waiting_last_message)
    write("waiting_last_message_past_the_head_truncation.jsonl",
          waiting_last_message_past_the_head_truncation)
    write("outstanding_by.jsonl", outstanding_by)
    write("will_resume.jsonl", will_resume)
    write("will_check_back.jsonl", will_check_back)
    write("once_it_finishes.jsonl", once_it_finishes)
    write("waiting_then_blocked.jsonl", waiting_then_blocked)
    write("waiting_then_complete.jsonl", waiting_then_complete)
    write("path_gate.jsonl", path_gate)
    write("skill_substitution.jsonl", skill_substitution)
    write("tracker_denied.jsonl", tracker_denied)
    write("multi_end_turn.jsonl", multi_end_turn)
    write("malformed.jsonl", success, extra_raw=(2, '{"type": "assistant", "message": {this is not json'))
    write("closeout_complete.jsonl", closeout_complete)
    write("closeout_skipped.jsonl", closeout_skipped)
    write("closeout_skipped_long.jsonl", closeout_skipped_long)
    write("closeout_unfinished.jsonl", closeout_unfinished)
    write("closeout_tracker_denied.jsonl", closeout_tracker_denied)
    print("fixtures written to", HERE)
