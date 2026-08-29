"""Grok backend. Capability record from origin U1 pins."""
import os
from urllib.parse import quote as _quote

from . import (_none, _parse_after_name_token, _record)

CAPABILITY = _record("grok")

readable = _none
normalize_transcript = _none
normalize_stream = _none
parse_version = _parse_after_name_token


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
