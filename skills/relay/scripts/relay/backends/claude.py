"""Claude backend. Capability record from origin U1 pins."""
from .. import contracts
from . import (_none, _parse_leading_digit, _record)

CAPABILITY = _record("claude")

readable = _none
normalize_transcript = _none
normalize_stream = _none
parse_version = _parse_leading_digit


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
