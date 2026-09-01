"""Claude backend. Capability record from origin U1 pins."""
from .. import contracts
from . import (Evidence as _Evidence, TEXT_CHARS as _TEXT_CHARS, _argument_of,
               _decode_stream_line, _parse_leading_digit, _read_jsonl, _record, _tool_call_event)

# Issue #58, KTD11. A short, deliberately partial list of the aliases this CLI takes for
# `--model`, so a manifest that sends a claude model to another backend is refused at validate.
# An unlisted name is allowed through, so a new alias costs nothing here.
CAPABILITY = _record("claude", known_models=("opus", "sonnet", "haiku"))

parse_version = _parse_leading_digit

# The argument-key order tail.py's decode() used before Backends U6.
_ARGUMENT_KEYS = ("command", "file_path", "pattern", "skill", "description")


def normalize_transcript(transcript_path, log_path=None):
    """The written line shape (Backends U6, origin KTD2) is Claude's own transcript primitive,
    so this normalizer is the identity wrap the origin plan predicted: `_read_jsonl` already
    produces lines shaped exactly like a parsed Claude transcript object."""
    lines, malformed, opened = _read_jsonl(transcript_path)
    return _Evidence(lines=lines, malformed_lines=malformed, decoded_events=len(lines),
                      undetectable=frozenset(), opened=opened)


def readable(transcript_path, evidence):
    """Readable when the file opened at all, matching the classifier's own behavior before
    Backends U6: a transcript that opened but decoded nothing is still present. `evidence.opened`
    is the answer `normalize_transcript`'s own read already found; this never re-touches the
    filesystem."""
    return evidence.opened


def normalize_stream(raw, state=None):
    """One raw stdout line in, zero or more printable events out. `state` is unused: Claude's
    stream needs no cross-call buffering, unlike Grok's per-token stream (KTD6)."""
    payload = _decode_stream_line(raw)
    if payload is None:
        return [], None
    message = payload.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, list):
        return [], None

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
                events.append(body[:_TEXT_CHARS])
        elif kind == "tool_use":
            argument = _argument_of(block.get("input"), _ARGUMENT_KEYS)
            name = str(block.get("name") or "tool")
            events.append(_tool_call_event(name, argument))
    return events, None


def build_args(manifest, task, brief_text, session_id, allowed=None, disallowed=None,
               log_path=None, repo=None, **_kwargs):
    cap = CAPABILITY
    args = [
        cap.binary, cap.headless_flag, brief_text,
        "--session-id", session_id,
        "--model", task.model,
        "--effort", task.effort,
        "--permission-mode", cap.permission_mode,
        cap.allow_flag, ",".join(allowed or ()),
        cap.deny_flag, ",".join(disallowed or ()),
    ]
    args.extend(cap.output_format)
    return args


def evidence_sources(home, cwd, session_id, log_path=None, **_kwargs):
    return (contracts.transcript_path(home, cwd, session_id),)


def qualify_skill(name):
    return CAPABILITY.skill_form % name
