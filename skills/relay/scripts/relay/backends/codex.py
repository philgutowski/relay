"""Codex backend. Capability record from origin U1 pins."""
import os

from .. import contracts
from . import (Evidence as _Evidence, _last_message_path, _none, _parse_after_name_token,
               _read_jsonl, _record)

CAPABILITY = _record("codex")

parse_version = _parse_after_name_token
# U4 fills this body: the stdout stream normalizer for the Follower.
normalize_stream = _none

# Codex has no per-tool deny flag (no `enforces_at_launch`) and no structured skill-invocation
# call, so none of these four classes can be observed from its evidence (Backends U6, R3, R4).
# The first three depend on a denial existing at all; the fourth is a separate capability.
_UNDETECTABLE = frozenset((
    contracts.HALT_DENIED_TOOL,
    contracts.HALT_PATH_GATE,
    contracts.HALT_TRACKER_WRITE_DENIED,
    contracts.HALT_SKILL_SUBSTITUTION,
))


def normalize_transcript(transcript_path, log_path=None):
    """`transcript_path` is the `--output-last-message` file: prose, not JSON, read directly as
    the final message (KTD5). `log_path` is the stdout log, parsed for tool-use synthesis and
    for the decoded-event count `readable()` needs (KTD4); Codex never demonstrates a denial, so
    no `tool_result` block is ever synthesized. `decoded_events` counts the log's own valid JSON
    lines, not the last-message file: a log that opened but decoded nothing is not readable even
    when the last-message file is present, per KTD4."""
    try:
        with open(transcript_path, encoding="utf-8", errors="replace") as handle:
            last_text = handle.read()
    except (OSError, TypeError):
        last_text = None

    lines = []
    malformed = 0
    decoded_events = 0
    if log_path:
        try:
            raw_lines, malformed = _read_jsonl(log_path)
        except (OSError, TypeError):
            raw_lines = []
        decoded_events = len(raw_lines)
        for number, obj in raw_lines:
            block = _tool_use_of(obj)
            if block is not None:
                lines.append((number, {
                    "type": contracts.TRANSCRIPT_TYPE_ASSISTANT,
                    "message": {"content": [block]},
                }))
    elif last_text is not None:
        # No stdout log given (a direct unit-test call, or a Task with no log yet). The final
        # message alone still counts as one decoded event.
        decoded_events = 1

    if last_text is not None:
        # Appended last, at a line number past every log line, so the classifier's "last
        # assistant text block" scan picks this up as `last_text` regardless of how many
        # tool-use lines the log contributed.
        final_line = (lines[-1][0] + 1) if lines else 1
        lines.append((final_line, {
            "type": contracts.TRANSCRIPT_TYPE_ASSISTANT,
            "message": {"content": [{"type": "text", "text": last_text}]},
        }))
    return _Evidence(lines=lines, malformed_lines=malformed, decoded_events=decoded_events,
                      undetectable=_UNDETECTABLE)


def _tool_use_of(obj):
    """One decoded stdout event to a synthesized `tool_use` block, or None when the event kind
    carries no tool call. `item.completed` is used, not `item.started`, so a command still
    running when the log ends is not double counted or counted as a completed call."""
    if obj.get("type") != "item.completed":
        return None
    item = obj.get("item") or {}
    kind = item.get("type")
    if kind == "command_execution":
        return {"type": "tool_use", "id": item.get("id"), "name": "Bash",
                "input": {"command": item.get("command", "")}}
    if kind == "file_change":
        changes = item.get("changes") or []
        path = changes[0].get("path", "") if changes else ""
        return {"type": "tool_use", "id": item.get("id"), "name": "Edit",
                "input": {"file_path": path}}
    return None


def readable(transcript_path, evidence):
    """KTD4: readable only when the last-message file exists and the normalizer decoded at
    least one event. The stdout log always opens once the launcher creates it, so a file-open
    test alone cannot tell a genuine run from an empty one."""
    return os.path.exists(transcript_path) and evidence.decoded_events >= 1


def build_args(manifest, task, brief_text, session_id, allowed=None, disallowed=None,
               log_path=None, repo=None, **_kwargs):
    cap = CAPABILITY
    repo = repo or os.path.realpath(manifest.project.repo)
    last_message = _last_message_path(log_path, session_id)
    args = [
        cap.binary, cap.headless_flag,
        "--sandbox", cap.permission_mode,
        "--model", task.model,
        "-C", repo,
        "--output-last-message", last_message,
    ]
    args.extend(cap.output_format)
    for token in cap.extra_writable_dirs:
        args.extend(["--add-dir", token.replace("<repo>", repo)])
    args.append(brief_text)
    return args


def evidence_sources(home, cwd, session_id, log_path=None, **_kwargs):
    last_message = _last_message_path(log_path, session_id)
    if log_path:
        return (last_message, log_path)
    return (last_message,)


def qualify_skill(name):
    return CAPABILITY.skill_form % name
