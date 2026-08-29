"""Codex backend. Capability record from origin U1 pins."""
import os

from . import (_last_message_path, _none, _parse_after_name_token, _record)

CAPABILITY = _record("codex")

readable = _none
normalize_transcript = _none
normalize_stream = _none
parse_version = _parse_after_name_token


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
