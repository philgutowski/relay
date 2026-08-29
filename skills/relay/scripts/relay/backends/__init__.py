"""CLI backends, launch facts only (Backends U4).

A backend is the CLI a Task process runs on. This package is the only per backend table
the rest of the seam may read. Construction copies `contracts.BACKEND_PINS` and does no I/O.

The public callable surface of each backend module is exactly INTERFACE. The capability
record is a frozen attribute, not a method. Launching, bounding, killing the process group,
heartbeating the lease, merging, pushing, and verifying stay in the shared run loop.

The interface, with the shapes each callable returns:

    build_args(...)              -> argument list, origin U5 fills the body
    parse_version(text)          -> version string or None, never raises
    evidence_sources(...)        -> evidence locator tuple, origin U5 fills the body
    readable(...)                -> readability state, origin U6 fills the body
    normalize_transcript(...)    -> normalized lines, origin U6 fills the body
    normalize_stream(...)        -> normalized lines, origin U6 fills the body
    qualify_skill(name)          -> the skill invocation string for this backend
"""
from dataclasses import dataclass

from .. import contracts

BACKENDS = ("claude", "codex", "grok")

INTERFACE = (
    "build_args",
    "parse_version",
    "evidence_sources",
    "readable",
    "normalize_transcript",
    "normalize_stream",
    "qualify_skill",
)


class ConfigurationError(ValueError):
    """A backend name the operator must fix before any run. Raised at construction,
    before a single request, so `relay validate` can name the missing CLI."""


@dataclass(frozen=True)
class Capability:
    binary: str
    version_tested: str
    version_output_sample: str
    plugin_version: str
    plugin_query: tuple
    headless_flag: str
    session_id_choosable: bool
    permission_mode: str
    forbidden_permission_modes: tuple
    output_format: tuple
    allow_flag: str | None
    deny_flag: str | None
    enforces_at_launch: bool
    skill_form: str
    evidence: str
    credential_prefixes: tuple
    credential_file: str
    nesting_markers: tuple
    writes_into_worktree: bool
    extra_writable_dirs: tuple


def _record(name):
    """The frozen pin copy for one backend. Backend modules call this and do not
    import Capability, so the type object is not a public callable on the module."""
    return Capability(**contracts.BACKEND_PINS[name])


def build(name):
    """The backend module the Task names. Imports are local so a machine without
    two of the CLIs can still construct the third."""
    if name == "claude":
        from . import claude

        return claude
    if name == "codex":
        from . import codex

        return codex
    if name == "grok":
        from . import grok

        return grok
    raise ConfigurationError("unknown backend %r; expected claude, codex, or grok" % name)
