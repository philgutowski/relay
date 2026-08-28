"""Exit classifier (U7): read a session transcript after exit and say what happened.

The transcript is the jsonl file the CLI writes, one JSON object per line. Three line types
carry evidence. An `assistant` line holds the model's content blocks: `text`, or `tool_use`
with an id, a name, and an input. A `user` line holds either the prompt or `tool_result`
blocks, each naming the `tool_use_id` it answers, with `is_error` set on a denial. The last
`assistant` line with a text block is the final message, where the return envelope lives.

Two joins do the work (KTD6). A denial is a `tool_result` whose content matches the denial
regex; joined by id to its `tool_use` it yields the tool name and the path or argument it was
denied on. A substitution is a `Skill` tool_use whose `input.skill` lacks the required prefix.
Classes assigned here are the ones the transcript alone can decide: timeout (from the launch
result), blocked_envelope, no_envelope, and path_gate. gate_refused, partial_landing,
tracker_write_denied as a class, remote_advanced, closeout_out_of_scope, and ci_undecided need
git or tracker evidence and are assigned by verify (U8); the findings here attach to them.
"""
import json
import re

from . import contracts, summary

LAST_MESSAGE_CHARS = 200
ARGUMENT_CHARS = 120

FENCE_RE = re.compile(r"```%s[ \t]*\n(.*?)```" % re.escape(contracts.ENVELOPE_FENCE_TAG), re.S)
STATUS_RE = re.compile(
    r"^[ \t]*(?:[-*]\s*)?[`*]*%s[`*]*\s*:\s*[`*]*(%s)\b" % (contracts.ENVELOPE_STATUS_KEY, "|".join(contracts.ENVELOPE_STATUSES)),
    re.M | re.I,
)
PLAN_PATH_RE = re.compile(r"^[ \t]*(?:[-*]\s*)?[`*]*%s[`*]*\s*:\s*[`*]*([^\s`*]+)" % contracts.ENVELOPE_PLAN_PATH_KEY, re.M)


def _text_of(content):
    """Flatten a tool_result content field: a string, or a list of text blocks."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")))
            elif isinstance(item, str):
                parts.append(item)
        return "\n".join(parts)
    return "" if content is None else str(content)


def _target_of(tool_use):
    """What a denied tool was aimed at: the file path when there is one, else the command,
    else the first 120 characters of the input."""
    inp = tool_use.get("input") or {}
    if isinstance(inp, dict):
        for key in ("file_path", "path", "notebook_path"):
            if inp.get(key):
                return str(inp[key])
        if inp.get("command"):
            return str(inp["command"])[:ARGUMENT_CHARS]
    return json.dumps(inp, sort_keys=True)[:ARGUMENT_CHARS]


def matches_write_pattern(tool_use, patterns):
    """KTD16: an adapter describes a tracker write as tool name prefixes, Bash command
    prefixes, or file paths (for the markdown adapter's Edit and Write)."""
    if not patterns:
        return False
    name = tool_use.get("name") or ""
    inp = tool_use.get("input") or {}
    for prefix in patterns.get("tools", ()):
        if name.startswith(prefix):
            return True
    if name == "Bash" and isinstance(inp, dict):
        command = str(inp.get("command", "")).lstrip()
        for prefix in patterns.get("bash", ()):
            if command.startswith(prefix):
                return True
    if name in ("Edit", "Write", "MultiEdit", "NotebookEdit") and isinstance(inp, dict):
        path = str(inp.get("file_path") or inp.get("notebook_path") or "")
        for suffix in patterns.get("paths", ()):
            if path == suffix or path.endswith("/" + suffix):
                return True
    return False


def required_skill_for(skill_name):
    """The qualified skill a bare Skill call should have been, or None when the bare name is
    not one of the plugin skills the brief pins. `code-review` maps to `ce-code-review`."""
    if not skill_name or skill_name.startswith(contracts.SKILL_PREFIX):
        return None
    bare = skill_name.split(":")[-1]
    for required in contracts.REQUIRED_SKILLS:
        short = required[3:] if required.startswith("ce-") else required
        if bare in (required, short) or "ce-" + bare == required:
            return contracts.SKILL_PREFIX + required
    return None


KEY_LINE_RE = re.compile(r"^[ \t]*(?:[-*]\s*)?[`*]*[A-Za-z_]+[`*]*\s*:")


def _list_after(block, key):
    """Loose list parse: `key: a` inline, `key:` followed by `- item` lines, or `key:` followed
    by a plain paragraph, which is one item per line. The paragraph case came from the first
    live run: the process wrote its blocker as prose under `blockers:` and the record read "no
    blocker text in the envelope" while the text sat one line below. The list ends at the next
    `key:` line, or at the first blank line once something has been collected."""
    match = re.search(r"^[ \t]*(?:[-*]\s*)?[`*]*%s[`*]*\s*:[ \t]*(.*)$" % re.escape(key), block, re.M)
    if not match:
        return []
    inline = match.group(1).strip().strip("`*")
    if inline and inline.lower() not in ("none", "[]", "null", "n/a", "-"):
        return [inline]
    items = []
    for line in block[match.end():].splitlines():
        stripped = line.strip()
        if stripped.startswith(("- ", "* ")):
            items.append(stripped[2:].strip().strip("`"))
        elif stripped == "":
            if items:
                break
        elif KEY_LINE_RE.match(line):
            break
        else:
            items.append(stripped.strip("`"))
    return items


def parse_envelope(text):
    """KTD8: fenced `relay-envelope` block first, else a line anchored scan of the whole text
    taking the last status match. Returns None when no status is found."""
    fenced = FENCE_RE.findall(text or "")
    block = fenced[-1] if fenced else None
    if block is not None:
        matches = STATUS_RE.findall(block)
    else:
        block = text or ""
        matches = STATUS_RE.findall(block)
    if not matches:
        return None
    plan = PLAN_PATH_RE.search(block)
    return {
        "status": matches[-1].lower(),
        "fenced": bool(fenced),
        "blockers": _list_after(block, contracts.ENVELOPE_BLOCKERS_KEY),
        "changed_files": _list_after(block, contracts.ENVELOPE_CHANGED_FILES_KEY),
        "plan_path": plan.group(1) if plan else None,
        "learnings": _list_after(block, contracts.ENVELOPE_LEARNINGS_KEY),
    }


def read_transcript(path):
    """Parse the file line by line. Malformed lines are counted and skipped, never fatal."""
    lines = []
    malformed = 0
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
    return lines, malformed


def classify(transcript_path, launch_result, write_tool_patterns=None):
    """Signature from plan U7. `launch_result` needs `timed_out` and `exit_code` attributes.
    Returns a plain dict the run loop writes to the record and to digests/<id>.json."""
    timed_out = bool(getattr(launch_result, "timed_out", False))
    exit_code = getattr(launch_result, "exit_code", None)
    result = {
        "transcript_path": transcript_path,
        "transcript_present": False,
        "exit_code": exit_code,
        "timed_out": timed_out,
        "line_count": 0,
        "malformed_lines": 0,
        "tool_calls": 0,
        "findings": [],
        "envelope": None,
        "last_message": None,
        "halt_class": None,
        "routable": False,
    }
    try:
        lines, malformed = read_transcript(transcript_path)
        result["transcript_present"] = True
    except (OSError, TypeError):
        lines, malformed = [], 0
    result["line_count"] = len(lines)
    result["malformed_lines"] = malformed

    tool_uses = {}
    last_text = None
    for number, obj in lines:
        kind = obj.get("type")
        message = obj.get("message") or {}
        content = message.get("content")
        if kind == contracts.TRANSCRIPT_TYPE_ASSISTANT and isinstance(content, list):
            texts = []
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_use":
                    result["tool_calls"] += 1
                    tool_uses[block.get("id")] = dict(block, _line=number)
                    if block.get("name") == "Skill":
                        skill = str((block.get("input") or {}).get("skill", ""))
                        required = required_skill_for(skill)
                        if required:
                            result["findings"].append({
                                "class": contracts.HALT_SKILL_SUBSTITUTION,
                                "name": skill,
                                "required": required,
                                "line": number,
                            })
                elif block.get("type") == "text":
                    texts.append(str(block.get("text", "")))
            if texts and not obj.get("isSidechain"):
                last_text = "\n".join(texts)
        elif kind == contracts.TRANSCRIPT_TYPE_USER and isinstance(content, list):
            for block in content:
                if not isinstance(block, dict) or block.get("type") != "tool_result":
                    continue
                body = _text_of(block.get("content"))
                match = contracts.DENIAL_REGEX.match(body.strip())
                if not (block.get("is_error") and match):
                    continue
                use = tool_uses.get(block.get("tool_use_id"), {})
                tool = use.get("name") or match.group(1)
                target = _target_of(use) if use else ""
                finding = {
                    "class": contracts.HALT_DENIED_TOOL,
                    "tool": tool,
                    "target": target,
                    "line": number,
                    "tool_use_line": use.get("_line"),
                }
                file_path = str((use.get("input") or {}).get("file_path") or (use.get("input") or {}).get("notebook_path") or "") if isinstance(use.get("input"), dict) else ""
                if contracts.CLAUDE_DIR_PATH_REGEX.search(file_path):
                    finding["class"] = contracts.HALT_PATH_GATE
                elif use and matches_write_pattern(use, write_tool_patterns):
                    finding["class"] = contracts.HALT_TRACKER_WRITE_DENIED
                result["findings"].append(finding)

    result["last_message"] = (last_text or "")[:LAST_MESSAGE_CHARS] or None
    # The tail is for the closeout's ending contract, whose terminal line is the last line of a
    # message that can be longer than the head above. First live run: a closeout that explained
    # its skip before printing `Documentation skipped` read as unfinished, because the parser
    # was handed the first 200 characters and the line lived past them.
    result["last_message_tail"] = (last_text or "")[-LAST_MESSAGE_CHARS:] or None
    envelope = parse_envelope(last_text) if last_text else None
    result["envelope"] = envelope

    # Precedence (KTD6): timeout beats all; then the envelope; a path_gate finding on a blocked
    # or absent envelope is the more specific cause. A complete envelope leaves the class to
    # verify, so halt_class stays None and routable is True.
    has_path_gate = any(f["class"] == contracts.HALT_PATH_GATE for f in result["findings"])
    if timed_out:
        result["halt_class"] = contracts.HALT_TIMEOUT
    elif envelope and envelope["status"] == contracts.ENVELOPE_STATUS_COMPLETE:
        result["routable"] = True
    elif envelope:
        result["halt_class"] = contracts.HALT_PATH_GATE if has_path_gate else contracts.HALT_BLOCKED_ENVELOPE
    else:
        result["halt_class"] = contracts.HALT_PATH_GATE if has_path_gate else contracts.HALT_NO_ENVELOPE
        # Kept as a finding even under path_gate, so the summary says the envelope was missing.
        result["findings"].append({
            "class": contracts.HALT_NO_ENVELOPE,
            "last_message": result["last_message"],
        })
    return result


def finding_line(finding):
    """One sentence per finding, for the closeout brief's Other findings bullets.

    Rendered by `summary.cause_line`, so the closeout brief and the run summary share one
    renderer of `HALT_LINES`. A second copy with its own hand written defaults is how a template
    that gained a field degraded what reached a tracker card while the summary printed fine.
    """
    return summary.cause_line(finding.get("class"), finding)


def write_digest(result, path):
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2, sort_keys=True)
