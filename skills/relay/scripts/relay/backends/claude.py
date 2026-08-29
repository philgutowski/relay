"""Claude backend. Capability record from origin U1 pins."""
from . import (_empty_args, _empty_sources, _none, _parse_leading_digit, _record)

CAPABILITY = _record("claude")

build_args = _empty_args
evidence_sources = _empty_sources
readable = _none
normalize_transcript = _none
normalize_stream = _none
parse_version = _parse_leading_digit


def qualify_skill(name):
    return CAPABILITY.skill_form % name
