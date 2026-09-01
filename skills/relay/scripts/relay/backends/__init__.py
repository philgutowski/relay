"""CLI backends, launch facts only (Backends U4).

A backend is the CLI a Task process runs on. This package is the only per backend table
the rest of the seam may read. Construction copies `contracts.BACKEND_PINS` and does no I/O.

The public callable surface of each backend module is exactly INTERFACE. The capability
record is a frozen attribute, not a method. Launching, bounding, killing the process group,
heartbeating the lease, merging, pushing, and verifying stay in the shared run loop.

The interface, with the shapes each callable returns:

    build_args(...)                      -> argument list for that backend
    parse_version(text)                  -> version string or None, never raises
    evidence_sources(...)                -> evidence locator tuple
    readable(transcript_path, evidence)  -> bool (Backends U6)
    normalize_transcript(path, log_path=None) -> Evidence (Backends U6)
    normalize_stream(raw_line, state=None)    -> (events, state) (Backends U6)
    qualify_skill(name)                  -> the skill invocation string for this backend
"""
import json
import os
import re
from dataclasses import dataclass

from .. import contracts

# Same leading digit token Claude's parse_version uses. Codex and Grok skip the first
# name token and apply it to the rest. launch.cli_version calls parse_version per backend.
_VERSION_TOKEN_RE = re.compile(r"^(\d[\w.\-]*)")

# Bounds on one printed stream event (Backends U6). A task process writes messages far longer
# than a terminal line, and a Follower that reflows them is unreadable next to the tool calls
# between them. Shared so every backend's `normalize_stream` truncates the same way.
TEXT_CHARS = 600
ARGUMENT_CHARS = 110

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
    """A backend name not in the closed set. Raised by `build()` before any
    process starts."""


@dataclass(frozen=True)
class Evidence:
    """The written line shape (Backends U6, origin KTD2). `lines` replays Claude's own
    transcript primitive, a list of `(line_number, dict)` pairs, each dict shaped like a
    parsed Claude transcript object: `type` of `assistant` or `user`, `message.content` a list
    of `text`, `tool_use`, or `tool_result` blocks. A normalizer that cannot observe a given
    finding class (no per-tool deny signal, no structured skill call) never synthesizes a block
    for it and instead names the halt-class constant in `undetectable`, so a reader can tell
    "not checked" from "checked, none found" (R13)."""

    lines: list
    malformed_lines: int
    decoded_events: int
    undetectable: frozenset
    # Whether the primary evidence file (`transcript_path`) opened at all. Set once, by
    # whichever read `normalize_transcript` already performs, so `readable()` never re-touches
    # the filesystem to ask a question the parse already answered.
    opened: bool = True


def _read_jsonl(path):
    """One JSON object per line, tolerating malformed lines. Shared by any normalizer whose
    native evidence is itself JSON-lines (Claude, Grok); Codex's stdout log also qualifies.
    Returns `(lines, malformed_count, opened)`, `lines` a list of `(line_number, dict)` pairs. A
    line that fails to parse, or parses to something other than a dict, is counted and skipped.
    A file that will not open at all degrades to `([], 0, False)` rather than raising."""
    lines = []
    malformed = 0
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            for number, raw in enumerate(handle, start=1):
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    obj = json.loads(raw)
                except ValueError:
                    malformed += 1
                    continue
                if isinstance(obj, dict):
                    lines.append((number, obj))
                else:
                    malformed += 1
    except (OSError, TypeError):
        return [], 0, False
    return lines, malformed, True


def read_jsonl(path):
    """Public name for `_read_jsonl`, for callers outside this package (classify.scan_self_kill).
    Every in-package normalizer keeps using `_read_jsonl` directly; this just gives an outside
    caller a name that is not the underscore-prefixed internal one."""
    return _read_jsonl(path)


def _argument_of(tool_input, keys):
    """The first present argument value among `keys`, in order. Shared by every backend's
    `normalize_stream`, parameterized by that backend's own argument-key tuple."""
    if not isinstance(tool_input, dict):
        return ""
    for key in keys:
        value = tool_input.get(key)
        if value:
            return str(value)
    return ""


def _tool_call_event(name, argument):
    """One printed tool-call line, in the shape every backend's `normalize_stream` renders."""
    return "  > %-10s %s" % (name, argument[:ARGUMENT_CHARS])


def _decode_stream_line(raw):
    """One raw stdout line (bytes or str) to a parsed JSON object, or `None` when the line is
    blank, not JSON, or not an object. Shared by every backend's `normalize_stream`, which each
    map that object's own event shape from here."""
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        obj = json.loads(raw)
    except ValueError:
        return None
    return obj if isinstance(obj, dict) else None


@dataclass(frozen=True)
class Capability:
    binary: str
    version_tested: str
    version_output_sample: str
    plugin_version: str
    plugin_query: tuple
    plugin_version_pattern: str
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
    config_overrides: tuple
    strict_config: bool
    grants_network: bool
    # Issue #57. A backend fact the brief states as an instruction, since instruction is the
    # only enforcement layer some backends have for it (R10's shape, mirroring
    # `enforces_at_launch`). `None` for a backend with no such constraint, matching the
    # `allow_flag`/`deny_flag` precedent for "this backend has none" rather than `""`.
    commit_message_constraint: str | None
    # Issue #58, KTD11. Model names this backend is known to accept, so `manifest.validate` can
    # refuse a manifest that hands one backend's model to another. Deliberately partial: the
    # check is negative, a name no record claims is allowed through, so a list that goes stale
    # costs nothing and a provider shipping a new model never breaks a manifest. Supplied by
    # each backend module rather than by `contracts.BACKEND_PINS`, because a pin is a launch
    # fact and these names are neither read nor written at launch.
    known_models: tuple = ()


# Capability fields that do not come from `contracts.BACKEND_PINS`. Named here so the pins
# completeness test can subtract them rather than hard coding the exception.
NON_PIN_FIELDS = ("known_models",)


def _record(name, **extra):
    """The frozen pin copy for one backend, plus any non-pin field the backend module supplies
    (`NON_PIN_FIELDS`). Backend modules call this and do not import Capability, so the type
    object is not a public callable on the module."""
    return Capability(**contracts.BACKEND_PINS[name], **extra)


def _parse_leading_digit(text):
    """A version only when stripped stdout leads with a digit and the matched token
    contains a dot, so a bare update-banner digit (e.g. "3 updates available") is not
    mistaken for a version. Never raises."""
    if text is None:
        return None
    stripped = str(text).strip()
    if not stripped:
        return None
    match = _VERSION_TOKEN_RE.match(stripped)
    if not match:
        return None
    token = match.group(1)
    return token if "." in token else None


def _parse_after_name_token(text):
    """Skip the leading name token, then take a leading digit version. Never raises."""
    if text is None:
        return None
    stripped = str(text).strip()
    if not stripped:
        return None
    parts = stripped.split(None, 1)
    if len(parts) < 2:
        return None
    return _parse_leading_digit(parts[1])


def _last_message_path(log_path, session_id):
    """The file Codex writes the final agent message to. Named by the runner
    before launch (origin KTD4), next to the stdout log when one exists."""
    if log_path:
        directory = os.path.dirname(log_path)
        base = os.path.basename(log_path)
        if base.endswith(".stdout.log"):
            stem = base[:-len(".stdout.log")]
        else:
            stem = os.path.splitext(base)[0]
        name = stem + ".last-message.txt"
        return os.path.join(directory, name) if directory else name
    return "%s.last-message.txt" % session_id


def build(name):
    """The backend module the Task names. Imports are local so importing
    `backends` does not load every backend module."""
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
