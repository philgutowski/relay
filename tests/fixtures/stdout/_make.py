#!/usr/bin/env python3
"""Regenerates the stdout stream fixture from real line shapes.

This is the stdout side of `tests/fixtures/transcripts/_make.py`. That file models the session
jsonl the CLI writes to disk; this one models what `claude -p --output-format stream-json
--verbose` prints on stdout, which is what the runner captures into
`<state>/logs/<task-id>.stdout.log` and what `relay tail` decodes.

Every line shape below is copied from a real Relay task log (CLI 2.1.245): the same keys in the
same places, with long values trimmed and the signature, ids, and paths made generic. The shapes
that matter to the decoder and are absent from the stub are all here: a `thinking` block beside a
`text` block, a `tool_use` whose input carries none of the five argument keys, and the
`tool_progress`, `rate_limit_event`, and `result` line types that carry no `message` at all.

The stub cannot produce these; it prints `system` and `result` lines only. Pinning the decoder
against this fixture is what stops the decoder and the stub from agreeing by construction, which
is the failure `docs/solutions/logic-errors/stubbed-seams-agree-by-construction-first-live-run-found-five-contract-defects.md`
documents. Run this file to rewrite the fixture; commit both.
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
SESSION = "00000000-0000-4000-8000-000000000001"
CWD = "/tmp/relay-fixture-repo"

_counter = {"n": 0}


def uuid():
    _counter["n"] += 1
    return "%08x-0000-4000-8000-%012x" % (_counter["n"], _counter["n"])


def assistant(content, parent_tool_use_id=None):
    """An assistant line. `parent_tool_use_id` is set on lines a subagent produced; the decoder
    renders them the same as any other, which is why the fixture carries one."""
    return {
        "type": "assistant",
        "message": {
            "id": "msg_%s" % uuid()[:8],
            "type": "message",
            "role": "assistant",
            "model": "claude-opus-5",
            "content": content,
            "stop_reason": None,
            "stop_sequence": None,
            "usage": {"input_tokens": 4, "output_tokens": 120},
        },
        "parent_tool_use_id": parent_tool_use_id,
        "request_id": "req_%s" % uuid()[:8],
        "session_id": SESSION,
        "timestamp": "2026-08-27T18:04:11.412Z",
        "uuid": uuid(),
    }


def user(content):
    return {
        "type": "user",
        "message": {"role": "user", "content": content},
        "parent_tool_use_id": None,
        "session_id": SESSION,
        "timestamp": "2026-08-27T18:04:12.006Z",
        "uuid": uuid(),
    }


def text(body):
    return {"type": "text", "text": body}


def thinking(body):
    """The signature is a long opaque token in the real line; trimmed here because nothing reads
    it. The decoder must skip the whole block, so its contents do not matter."""
    return {"type": "thinking", "thinking": body, "signature": "ErgCCqgBCBEYAipAHkmYjyTB"}


def tool_use(tool_id, name, inp):
    return {"type": "tool_use", "id": tool_id, "name": name, "input": inp,
            "caller": {"type": "direct"}}


def tool_result(tool_id, body):
    return [{"type": "tool_result", "content": body, "is_error": False, "tool_use_id": tool_id}]


def system_init():
    return {"type": "system", "subtype": "init", "cwd": CWD, "session_id": SESSION,
            "model": "claude-opus-5", "permissionMode": "dontAsk", "uuid": uuid()}


def thinking_tokens():
    return {"type": "system", "subtype": "thinking_tokens", "tokens": 61, "session_id": SESSION,
            "uuid": uuid()}


def tool_progress(tool_id):
    """A heartbeat line for a long running tool. It carries no `message` key at all."""
    return {"type": "tool_progress", "tool_use_id": tool_id + "-heartbeat-0",
            "tool_name": "TaskOutput", "parent_tool_use_id": tool_id,
            "elapsed_time_seconds": 30, "heartbeat": True, "session_id": SESSION, "uuid": uuid()}


def rate_limit_event():
    return {"type": "rate_limit_event", "rate_limit_info": {
        "status": "allowed", "resetsAt": 1787868600, "rateLimitType": "five_hour",
        "unifiedWindows": {"five_hour": {"utilization": 0.44, "resetsAt": 1787868600}}},
        "uuid": uuid()}


def result():
    return {"type": "result", "subtype": "success", "is_error": False, "num_turns": 12,
            "stop_reason": "end_turn", "session_id": SESSION, "duration_api_ms": 41201,
            "total_cost_usd": 0.42, "uuid": uuid()}


TEXT_FIRST = "I'll start by setting up the branch, then run the compound-engineering pipeline."
TEXT_LAST = "The branch is ready and the suite is green."


def stream():
    """One task's stdout, in the order the CLI prints it."""
    lines = [system_init(), thinking_tokens()]

    # A thinking block and a text block on one assistant line: only the text renders.
    lines.append(assistant([thinking("Let me check the branch first."), text(TEXT_FIRST)]))

    # Bash, keyed on `command`.
    lines.append(assistant([tool_use("toolu_01BASH", "Bash", {
        "command": "git status && git branch --list relay/T-5",
        "description": "Check the working tree"})]))
    lines.append(user(tool_result("toolu_01BASH", "On branch main\nnothing to commit")))

    # Read, keyed on `file_path`.
    lines.append(assistant([tool_use("toolu_01READ", "Read", {
        "file_path": CWD + "/skills/relay/scripts/relay/cli.py"})]))
    lines.append(user(tool_result("toolu_01READ", "     1\t\"\"\"The operator interface\"\"\"")))

    # Grep, keyed on `pattern`.
    lines.append(assistant([tool_use("toolu_01GREP", "Grep", {
        "pattern": "def cmd_", "output_mode": "content"})]))
    lines.append(user(tool_result("toolu_01GREP", "cli.py:90:def cmd_validate")))

    # Skill, keyed on `skill`.
    lines.append(assistant([tool_use("toolu_01SKILL", "Skill", {
        "skill": "compound-engineering:ce-plan", "args": "add the tail verb"})]))
    lines.append(user(tool_result("toolu_01SKILL", "Launching skill")))

    # Agent, keyed on `description` because it carries no command, path, or pattern.
    lines.append(assistant([tool_use("toolu_01AGENT", "Agent", {
        "description": "Review the follower", "subagent_type": "Explore",
        "prompt": "Read the runner package and report the log layout."})]))

    # A heartbeat while that agent runs, then a line the agent itself produced.
    lines.append(tool_progress("toolu_01AGENT"))
    lines.append(assistant([tool_use("toolu_01SUB", "Read", {
        "file_path": CWD + "/skills/relay/scripts/relay/run.py"})],
        parent_tool_use_id="toolu_01AGENT"))
    lines.append(user(tool_result("toolu_01AGENT", "The logs live under <state>/logs/.")))

    # TaskOutput: a real call whose input carries none of the five keys.
    lines.append(assistant([tool_use("toolu_01OUT", "TaskOutput", {"taskId": "3"})]))
    lines.append(user(tool_result("toolu_01OUT", "done")))

    lines.append(rate_limit_event())
    lines.append(assistant([text(TEXT_LAST)]))
    lines.append(result())
    return lines


def write(name, build):
    _counter["n"] = 0
    path = os.path.join(HERE, name)
    with open(path, "w") as handle:
        for line in build():
            handle.write(json.dumps(line) + "\n")


if __name__ == "__main__":
    write("real_stream.jsonl", stream)
    print("fixture written to", HERE)
