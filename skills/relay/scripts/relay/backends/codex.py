"""Codex backend. Capability record from origin U1 pins."""
from . import (_empty_args, _empty_sources, _none, _parse_after_name_token, _record)

CAPABILITY = _record("codex")

build_args = _empty_args
evidence_sources = _empty_sources
readable = _none
normalize_transcript = _none
normalize_stream = _none


def parse_version(text):
    return _parse_after_name_token(text)


def qualify_skill(name):
    return CAPABILITY.skill_form % name
