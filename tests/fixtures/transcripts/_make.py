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


if __name__ == "__main__":
    write("success.jsonl", success)
    write("blocked.jsonl", blocked)
    write("no_envelope.jsonl", no_envelope)
    write("path_gate.jsonl", path_gate)
    write("skill_substitution.jsonl", skill_substitution)
    write("tracker_denied.jsonl", tracker_denied)
    write("multi_end_turn.jsonl", multi_end_turn)
    write("malformed.jsonl", success, extra_raw=(2, '{"type": "assistant", "message": {this is not json'))
    print("fixtures written to", HERE)
