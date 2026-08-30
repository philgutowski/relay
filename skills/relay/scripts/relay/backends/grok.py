"""Grok backend. Capability record from origin U1 pins."""
import os
from urllib.parse import quote as _quote

from .. import contracts
from . import (Evidence as _Evidence, TEXT_CHARS as _TEXT_CHARS, _argument_of,
               _decode_stream_line, _parse_after_name_token, _read_jsonl, _record,
               _tool_call_event)

CAPABILITY = _record("grok")

parse_version = _parse_after_name_token

# Grok's own tool input carries `target_file` where Claude's carries `file_path`; both are
# listed so the tool-call line names the argument either way.
_ARGUMENT_KEYS = ("command", "file_path", "target_file", "pattern", "skill", "description")

# Grok's structured deny mechanism (demonstrated in denial-refusal.jsonl) is detectable; its
# skill invocation is a plain slash-command string with no distinguishing tool call, so
# substitution cannot be (Backends U6, R4).
_UNDETECTABLE = frozenset((contracts.HALT_SKILL_SUBSTITUTION,))

# The marker Grok's own `--deny` rule denial carries. A `tool_call_update` failing for another
# reason (a missing file, or Grok's own `auto`-mode judgment call, "Auto mode blocked this
# action ...") is not a denial and is not synthesized.
_DENIAL_MARKER = "Denied by permission policy"


def _update_of(obj):
    """The `sessionUpdate` payload one `updates.jsonl` line carries, or None for a line this
    normalizer does not read (`hook_execution`, `plan`, `turn_completed`, ...)."""
    params = obj.get("params")
    return (params or {}).get("update") if isinstance(params, dict) else None


def _tool_name_of(update):
    """The tool name a `tool_call` update carries. `dict.get(key, default)` only supplies the
    default when the key is absent, so a `_meta` that carries `"x.ai/tool": null` (or any
    non-dict value) must be checked explicitly rather than trusted to fall through."""
    meta = (update.get("_meta") or {}).get("x.ai/tool")
    name = meta.get("name") if isinstance(meta, dict) else None
    return str(name or update.get("title") or "")


def _update_text(update):
    """Flatten a `tool_call_update`'s content field: a list of `{"content": {"text": ...}}`
    wrappers, joined the way Claude's own `_text_of` flattens a `tool_result`."""
    parts = []
    for item in update.get("content") or []:
        if isinstance(item, dict):
            inner = item.get("content")
            if isinstance(inner, dict) and inner.get("type") == "text":
                parts.append(str(inner.get("text", "")))
    return "\n".join(parts)


def normalize_transcript(transcript_path, log_path=None):
    """`transcript_path` is `updates.jsonl`. `agent_message_chunk` events are already complete
    per-turn text, so `last_text` needs no token reassembly (KTD4). That reassembly is tail's
    job, on a different file, the raw stdout stream, not this one."""
    raw_lines, malformed, opened = _read_jsonl(transcript_path)

    lines = []
    tool_calls = {}
    for number, obj in raw_lines:
        update = _update_of(obj)
        if not isinstance(update, dict):
            continue
        kind = update.get("sessionUpdate")
        if kind == "agent_message_chunk":
            content = update.get("content") or {}
            text = str(content.get("text", "")) if isinstance(content, dict) else ""
            if text:
                lines.append((number, {
                    "type": contracts.TRANSCRIPT_TYPE_ASSISTANT,
                    "message": {"content": [{"type": "text", "text": text}]},
                }))
        elif kind == "tool_call":
            call_id = update.get("toolCallId")
            name = _tool_name_of(update)
            raw_input = update.get("rawInput") or {}
            if name == "run_terminal_command":
                block = {"type": "tool_use", "id": call_id, "name": "Bash",
                         "input": {"command": raw_input.get("command", "")}}
            else:
                block = {"type": "tool_use", "id": call_id, "name": name, "input": raw_input}
            tool_calls[call_id] = block
            lines.append((number, {
                "type": contracts.TRANSCRIPT_TYPE_ASSISTANT,
                "message": {"content": [block]},
            }))
        elif kind == "tool_call_update":
            if update.get("status") != "failed":
                continue
            body = _update_text(update)
            if _DENIAL_MARKER not in body:
                continue
            call = tool_calls.get(update.get("toolCallId")) or {}
            tool = call.get("name") or "tool"
            lines.append((number, {
                "type": contracts.TRANSCRIPT_TYPE_USER,
                "message": {"content": [{
                    "type": "tool_result", "tool_use_id": update.get("toolCallId"),
                    "is_error": True,
                    "content": "Permission to use %s has been denied" % tool,
                }]},
            }))

    return _Evidence(lines=lines, malformed_lines=malformed, decoded_events=len(raw_lines),
                      undetectable=_UNDETECTABLE, opened=opened)


def readable(transcript_path, evidence):
    """Readable when the file opened at all, matching Claude's own test: `updates.jsonl` is
    written incrementally by the CLI, not named by the Runner, so there is no separate
    decoded-event floor the way Codex's stdout log needs one (KTD4 governs Codex only).
    `evidence.opened` is the answer `normalize_transcript`'s own read already found."""
    return evidence.opened


def normalize_stream(raw, state=None):
    """One raw stdout line in, zero or more printable events out. `state` is the in-progress
    text-token buffer: Grok's `--output-format streaming-json` stdout carries one JSON object
    per token, so a printed message is the last contiguous run of `text` events, assembled here
    across calls (R9, KTD6) rather than in module state, which a concurrent second reader of the
    same backend would corrupt."""
    obj = _decode_stream_line(raw)
    if obj is None:
        return [], state

    kind = obj.get("type")
    if kind == "text":
        buffer = list(state or ())
        buffer.append(str(obj.get("data", "")))
        return [], buffer

    # Any non-`text` event ends the current message, if one was building. `thought` is
    # suppressed the same way Claude's `thinking` blocks are: it produces no event of its own,
    # but it still flushes a pending buffer, matching every other non-text event.
    events = []
    if state:
        body = "".join(state).strip()
        if body:
            events.append(body[:_TEXT_CHARS])
    if kind == "tool_call":
        name = str(obj.get("toolName") or obj.get("title") or "tool")
        argument = _argument_of(obj.get("rawInput"), _ARGUMENT_KEYS)
        events.append(_tool_call_event(name, argument))
    return events, None


def build_args(manifest, task, brief_text, session_id, allowed=None, disallowed=None,
               log_path=None, repo=None, **_kwargs):
    cap = CAPABILITY
    args = [
        cap.binary, cap.headless_flag, brief_text,
        "-s", session_id,
        "--model", task.model,
        "--effort", task.effort,
        "--permission-mode", cap.permission_mode,
    ]
    for rule in allowed or ():
        args.extend([cap.allow_flag, rule])
    for rule in disallowed or ():
        args.extend([cap.deny_flag, rule])
    args.extend(cap.output_format)
    return args


def evidence_sources(home, cwd, session_id, log_path=None, **_kwargs):
    encoded = _quote(cwd, safe="")
    return (os.path.join(home, ".grok", "sessions", encoded, session_id, "updates.jsonl"),)


def qualify_skill(name):
    return CAPABILITY.skill_form % name
