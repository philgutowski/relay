"""Claude backend. Capability record from origin U1 pins."""
import json

from .. import contracts
from . import (Evidence as _Evidence, _parse_leading_digit, _read_jsonl, _record)

CAPABILITY = _record("claude")

parse_version = _parse_leading_digit

# Bounds on one printed stream event, and the argument-key order tail.py's decode() used before
# Backends U6. Kept private to this module rather than imported from tail.py, which imports
# backends for dispatch; the reverse import would be circular.
_TEXT_CHARS = 600
_ARGUMENT_CHARS = 110
_ARGUMENT_KEYS = ("command", "file_path", "pattern", "skill", "description")


def _argument_of(tool_input):
    if not isinstance(tool_input, dict):
        return ""
    for key in _ARGUMENT_KEYS:
        value = tool_input.get(key)
        if value:
            return str(value)
    return ""


def normalize_transcript(transcript_path, log_path=None):
    """The written line shape (Backends U6, origin KTD2) is Claude's own transcript primitive,
    so this normalizer is the identity wrap the origin plan predicted: `_read_jsonl` already
    produces lines shaped exactly like a parsed Claude transcript object. A transcript that
    cannot be opened at all degrades to empty rather than raising; `readable()` is the single
    place that turns that into a verdict."""
    try:
        lines, malformed = _read_jsonl(transcript_path)
    except (OSError, TypeError):
        lines, malformed = [], 0
    return _Evidence(lines=lines, malformed_lines=malformed, decoded_events=len(lines),
                      undetectable=frozenset())


def readable(transcript_path, evidence):
    """Readable when the file opened at all, matching the classifier's own behavior before
    Backends U6: a transcript that opened but decoded nothing is still present."""
    try:
        with open(transcript_path, encoding="utf-8"):
            return True
    except (OSError, TypeError):
        return False


def normalize_stream(raw, state=None):
    """One raw stdout line in, zero or more printable events out. `state` is unused: Claude's
    stream needs no cross-call buffering, unlike Grok's per-token stream (KTD6)."""
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    raw = (raw or "").strip()
    if not raw:
        return [], None
    try:
        payload = json.loads(raw)
    except ValueError:
        return [], None
    if not isinstance(payload, dict):
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
            argument = _argument_of(block.get("input"))
            name = str(block.get("name") or "tool")
            events.append("  > %-10s %s" % (name, argument[:_ARGUMENT_CHARS]))
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
