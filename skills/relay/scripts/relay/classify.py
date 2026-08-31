"""Exit classifier (U7): read a session transcript after exit and say what happened.

The transcript is the jsonl file the CLI writes, one JSON object per line. Three line types
carry evidence. An `assistant` line holds the model's content blocks: `text`, or `tool_use`
with an id, a name, and an input. A `user` line holds either the prompt or `tool_result`
blocks, each naming the `tool_use_id` it answers, with `is_error` set on a denial. The last
`assistant` line with a text block is the final message, where the return envelope lives.

Two joins do the work (KTD6). A denial is a `tool_result` whose content matches the denial
regex; joined by id to its `tool_use` it yields the tool name and the path or argument it was
denied on. A substitution is a `Skill` tool_use whose `input.skill` is not this backend's own
qualified form of one of the plugin skills the brief pins; `required_skill_for` decides that,
and the test is per backend rather than one prefix, because two of the three CLIs spell a skill
with a bare sigil every skill on them shares.
Classes assigned here are the ones the transcript alone can decide: timeout (from the launch
result), blocked_envelope, no_envelope, path_gate, and unexpected_error when the transcript
itself would not open, which is the runner's fault and never the task's silence (KTD5).
gate_refused, partial_landing,
tracker_write_denied as a class, remote_advanced, closeout_out_of_scope, and ci_undecided need
git or tracker evidence and are assigned by verify (U8); the findings here attach to them.
"""
import fnmatch
import json
import re

from . import backends, contracts, summary

# Codex wraps the real command in `/bin/zsh -lc '...'`. The inner script, and each
# `&&` / `||` / `;` / newline segment of it, is what DISALLOWED_TOOLS globs against.
_SHELL_WRAP = re.compile(
    r"^(?:/(?:usr/)?bin/)?(?:[\w.+-]+/)*(?:ba)?sh(?:\s+-l)?\s+-[lc]\s+(.*)\Z",
    re.DOTALL | re.IGNORECASE,
)

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


def _unwrap_command(command):
    """Strip a `Bash(...)` wrapper and a `zsh -lc` / `bash -c` wrapper. Inner quotes stay off."""
    text = (command or "").strip()
    if text.startswith("Bash(") and text.endswith(")"):
        text = text[5:-1]
    match = _SHELL_WRAP.match(text)
    if not match:
        return text
    inner = match.group(1).strip()
    if len(inner) >= 2 and inner[0] == inner[-1] and inner[0] in "'\"":
        inner = inner[1:-1]
    return inner


def _command_candidates(command):
    unwrapped = _unwrap_command(command)
    parts = [p for p in re.split(r"\s*(?:&&|\|\||;|\n)\s*", unwrapped) if p]
    seen = []
    for candidate in (command, unwrapped) + tuple(parts):
        if candidate and candidate not in seen:
            seen.append(candidate)
    return seen


def matches_disallow_pattern(command, pattern):
    """True when `command` (Codex-shaped or bare) matches one DISALLOWED_TOOLS glob."""
    inner = contracts.disallow_inner(pattern)
    for candidate in _command_candidates(command):
        if fnmatch.fnmatch(candidate, inner) or fnmatch.fnmatch(candidate, pattern):
            return True
        bash_form = "Bash(%s)" % candidate
        if fnmatch.fnmatch(bash_form, pattern):
            return True
    return False


def required_skill_for(skill_name, backend="claude"):
    """The qualified skill a Skill call should have been in this backend's own form, or None when
    it was already qualified or names nothing the brief pins. `code-review` maps to
    `ce-code-review`.

    Already qualified is a two-part test (backends KTD3), not a prefix check. The form is
    `compound-engineering:%s` on claude but a bare `$%s` and `/%s` on codex and grok, sigils every
    skill on those CLIs shares, so the prefix alone would accept `$code-review`, the harness skill
    this exists to catch, as the plugin's. The remainder has to be a skill the brief actually
    pins. This is the one site that reads `skill_form` rather than calling `qualify_skill`,
    because no interface callable answers "is this string already in your form"."""
    if not skill_name:
        return None
    module = backends.build(backend)
    prefix, _, suffix = module.CAPABILITY.skill_form.partition("%s")
    # `prefix or suffix`, not `prefix` alone: a form with only a suffix would otherwise skip the
    # test entirely and report every correctly qualified call as a substitution requiring itself.
    if (prefix or suffix) and skill_name.startswith(prefix) and skill_name.endswith(suffix):
        stripped = skill_name[len(prefix):]
        if suffix:
            stripped = stripped[:-len(suffix)]
        if stripped in contracts.REQUIRED_SKILLS:
            return None
    bare = skill_name.split(":")[-1].lstrip("$/")
    for required in contracts.REQUIRED_SKILLS:
        short = required[3:] if required.startswith("ce-") else required
        if bare in (required, short) or "ce-" + bare == required:
            return module.qualify_skill(required)
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


def classify(transcript_path, launch_result, write_tool_patterns=None, backend="claude",
             disallow_patterns=None):
    """Signature from plan U7, extended by Backends U6 with `backend`. `launch_result` needs
    `timed_out` and `exit_code` attributes; U6 also reads its `log_path` when present, since a
    backend's evidence can span more than one file (Codex's last-message file plus its stdout
    log). Returns a plain dict the run loop writes to the record and to digests/<id>.json."""
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
        "findings_unavailable": False,
        "undetectable": [],
        "envelope": None,
        "last_message": None,
        "halt_class": None,
        "routable": False,
    }
    module = backends.build(backend)
    log_path = getattr(launch_result, "log_path", None)
    evidence = module.normalize_transcript(transcript_path, log_path=log_path)
    result["line_count"] = len(evidence.lines)
    result["malformed_lines"] = evidence.malformed_lines
    result["undetectable"] = sorted(evidence.undetectable)
    if module.readable(transcript_path, evidence):
        result["transcript_present"] = True
    audit = (
        bool(disallow_patterns)
        and result["transcript_present"]
        and not module.CAPABILITY.enforces_at_launch
    )
    lines = evidence.lines

    tool_uses = {}
    last_text = None
    for number, obj in lines:
        kind = obj.get("type")
        message = obj.get("message")
        # A `system` line's own `message` field is sometimes a plain string (its own notice
        # text, not a role/content envelope), never a `tool_use`/`tool_result` carrier this
        # loop reads; treat anything that is not a dict as carrying no content, the same as an
        # absent one, rather than crashing on `.get()`.
        content = message.get("content") if isinstance(message, dict) else None
        if kind == contracts.TRANSCRIPT_TYPE_ASSISTANT and isinstance(content, list):
            texts = []
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_use":
                    result["tool_calls"] += 1
                    tool_uses[block.get("id")] = dict(block, _line=number)
                    if audit:
                        command = ""
                        inp = block.get("input")
                        if isinstance(inp, dict):
                            command = str(inp.get("command") or "")
                        if command:
                            for pattern in disallow_patterns:
                                if matches_disallow_pattern(command, pattern):
                                    result["findings"].append({
                                        "class": contracts.UNENFORCED_DISALLOWED,
                                        "tool": block.get("name") or "Bash",
                                        "argument": command,
                                        "line": number,
                                        "pattern": pattern,
                                    })
                                    break
                    if block.get("name") == "Skill":
                        skill = str((block.get("input") or {}).get("skill", ""))
                        required = required_skill_for(skill, backend)
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
                    finding["detail"] = contracts.PATH_GATE_CLAUDE_DIR
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

    # Precedence (KTD6): timeout beats all; then an evidence source that would not open; then
    # the envelope; a path_gate finding on a blocked or absent envelope is the more specific
    # cause. A complete envelope leaves the class to verify, so halt_class stays None and
    # routable is True.
    has_path_gate = any(f["class"] == contracts.HALT_PATH_GATE for f in result["findings"])
    if timed_out:
        # KTD4: a killed process that never wrote its evidence is the timeout class, not a
        # runner fault, so this stays ahead of the unreadable branch below.
        result["halt_class"] = contracts.HALT_TIMEOUT
    elif not result["transcript_present"]:
        # R13, R20, KTD5. An absent evidence source is not an empty one. Falling through to
        # no_envelope claimed the process ran and printed nothing, which is a statement about
        # the task that nobody observed, and it handed run._routable the one class its rescue
        # route merges on. The fault is the runner's, so the class is the runner fault one and
        # the transcript derived findings are unavailable rather than none found.
        result["halt_class"] = contracts.HALT_UNEXPECTED_ERROR
        result["findings"] = None
        result["findings_unavailable"] = True
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
