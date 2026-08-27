"""Follower (U10): decode a running task's stdout and print one line per event.

A Relay run is invisible while it runs. The runner captures each Task process's stdout, which is
`claude -p --output-format stream-json --verbose`, into `<state>/logs/<task-id>.stdout.log`. That
is one JSON object per line and unreadable at a glance. This module turns it into text.

Three things here are not obvious.

The source is the per Task stdout log, not the session transcript and not `runner.log`. The
transcript under `~/.claude/projects/` is the classifier's input, written by the CLI on its own
schedule, so it is the wrong file to follow live. `runner.log` is closer, since `cmd_run` passes a
`stream` writer into `launch.launch` and a detached run's log therefore carries every Task's
stream json in one ordered file. It loses twice: a foreground `relay run` writes no `runner.log`
at all while `launch.launch` writes the per Task log either way, and a `runner.log` line carries
no Task id, so the phase headers below could not be derived from it.

Reads are byte oriented and buffer the incomplete tail. A follower reads a file another process
is appending to, so a read lands mid line routinely. Decoding that fragment fails and the event
is lost silently, which is the defect in the hand written prototype this replaces. Bytes rather
than text also keep a multi byte character split across a read boundary from being mangled.

The log sequence comes from the Manifest and the cursor advances on what exists on disk. Reading
`state.json` for the current Task would be tighter, but a candidate list plus a frontier handles
the three cases a cursor does not: a Task excluded before launch writes no log at all, a Task
whose Closeout never ran writes no closeout log, and a `tail` started late begins mid list.

Nothing here takes the Lease. `tail` is a reader, the same rule `status` follows.
"""
import json
import os
import time

from . import contracts

# Bounds on one printed event. A task process writes messages far longer than a terminal line,
# and a follower that reflows them is unreadable next to the tool calls between them.
TEXT_CHARS = 600
ARGUMENT_CHARS = 110

# The argument keys, in the order the first present one wins. Copied from the operator's own
# prototype: these are what a reader wants to see for the tools a task actually calls. A tool
# whose input carries none of them renders as a bare name, which is correct for TaskOutput and
# ToolSearch and is why the list is not extended to cover them.
ARGUMENT_KEYS = ("command", "file_path", "pattern", "skill", "description")

POLL_SECONDS = 1.0

# Phase names for the two logs a Task can produce, in the order the runner writes them.
PHASE_TASK = "task"
PHASE_CLOSEOUT = "closeout"


def _argument_of(tool_input):
    if not isinstance(tool_input, dict):
        return ""
    for key in ARGUMENT_KEYS:
        value = tool_input.get(key)
        if value:
            return str(value)
    return ""


def decode(raw):
    """One raw stdout line in, zero or more printable events out.

    Accepts bytes or str, because the follower reads bytes and the tests read either. A line that
    is not JSON, is not an object, or carries no content blocks yields nothing rather than
    raising: a follower that dies on one malformed line is worse than one that skips it, and the
    stream carries several line types (`system`, `tool_progress`, `rate_limit_event`, `result`)
    that hold no message at all.
    """
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    raw = (raw or "").strip()
    if not raw:
        return []
    try:
        payload = json.loads(raw)
    except ValueError:
        return []
    if not isinstance(payload, dict):
        return []
    message = payload.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, list):
        return []

    is_assistant = payload.get("type") == contracts.TRANSCRIPT_TYPE_ASSISTANT
    events = []
    for block in content:
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind == "text" and is_assistant:
            # `thinking` blocks are deliberately not rendered. They outnumbered text blocks two
            # to one in the log this was built against and would bury everything else.
            body = str(block.get("text", "")).strip()
            if body:
                events.append(body[:TEXT_CHARS])
        elif kind == "tool_use":
            argument = _argument_of(block.get("input"))
            name = str(block.get("name") or "tool")
            events.append("  > %-10s %s" % (name, argument[:ARGUMENT_CHARS]))
    return events
