"""Closeout process (U9, KTD4).

One short process runs after every task process exit except a dirty timeout, with two ordered
duties: write the task's outcome to the tracker, then judge whether the task produced a learning
worth keeping and write it if so.

It is a separate process for two reasons that are not interchangeable. The runner cannot do duty
one because the runner never writes to a tracker (R19), and that rule is what makes a runner
defect unable to move a card. The task process cannot do it either, because it exits before the
merge commit that duty one has to name. Duty two is here rather than in the task process because
a process at the end of a long context is the worst available judge of its own learning, and
because a blocked task deserves the same pass; the 2026-08-25 proof run's best learning came from
its blocker, not from its code.

The closeout is also the one process that commits without the runner's local gate in front of it,
so the brief names the paths it may touch and forbids a push. The runner checks the commit
against that list before pushing (U8's scope check, R53, KTD15). A bound checked before the push
is a guard; the same bound checked after is a report.

Its ending is a contract, not a judgement call: the last line is `Documentation complete` or
`Documentation skipped`, and anything else is a finding on the record rather than a halt, because
the runner's own verify decides landing and does not need this process's opinion.
"""
import os
import string
from dataclasses import dataclass, field

from . import backends, brief, classify, contracts, launch, manifest as manifest_module, state

OUTCOME_LANDED = "landed"
OUTCOME_BLOCKED = "blocked"
OUTCOME_HALTED = "halted"

RESULT_COMPLETE = "complete"
RESULT_SKIPPED = "skipped"
RESULT_UNFINISHED = "unfinished"

# The closeout's own allowlist floor. Narrower than a task's: it reads, edits docs, commits, and
# calls one skill. The adapter adds what its tracker write needs and the manifest may add more.
BASE_TOOLS = ("Read", "Edit", "Write", "Bash", "Grep", "Glob", "Skill")

TEMPLATE = "brief-closeout.md"

DATA_HEADER = brief.DATA_HEADER
DATA_BEGIN = brief.DATA_BEGIN
DATA_END = brief.DATA_END

NONE_LINE = "none"

# Findings that make the compound judgment worth a full pass rather than a lightweight one
# (the plan's Assumptions): something went wrong in a way a future session could repeat.
FULL_DEPTH_FINDINGS = (
    contracts.HALT_DENIED_TOOL,
    contracts.HALT_PATH_GATE,
    contracts.HALT_TRACKER_WRITE_DENIED,
    contracts.HALT_SKILL_SUBSTITUTION,
    contracts.UNENFORCED_DISALLOWED,
)


@dataclass
class CloseoutResult:
    result: str
    findings: list = field(default_factory=list)
    digest: dict = field(default_factory=dict)
    launch_result: object = None
    brief_path: str | None = None
    brief_sha256: str | None = None


def depth_for(digest):
    """`full` when something went wrong that a future session could repeat, `lightweight`
    otherwise. The closeout process still decides whether to run it at all."""
    for finding in (digest or {}).get("findings") or []:
        if finding.get("class") in FULL_DEPTH_FINDINGS:
            return contracts.COMPOUND_DEPTH_FULL
    return contracts.COMPOUND_DEPTH_LIGHTWEIGHT


def allowed_tools(manifest, adapter):
    """The base set, plus what the adapter's tracker write needs, plus the manifest's additions.
    Order is stable and duplicates are dropped, so the same manifest renders the same flag."""
    tools = list(BASE_TOOLS)
    for extra in tuple(adapter.closeout_allowed_tools()) + tuple(manifest.closeout.allowed_tools):
        if extra not in tools:
            tools.append(extra)
    return tuple(tools)


def _bullets(items, empty=NONE_LINE):
    """One item per line, defanged and flattened. Flattening matters: a denied Bash command or
    a last message can contain a newline, and a bullet that spans lines is a bullet a reader
    (or a model) can mistake for the surrounding instructions."""
    flattened = []
    for item in items:
        one_line = " ".join(str(item).split())
        if one_line:
            flattened.append(brief.defang(one_line))
    if not flattened:
        return empty
    return "\n".join("- " + item for item in flattened)


def _denial_lines(digest):
    lines = []
    for finding in (digest or {}).get("findings") or []:
        if finding.get("class") not in (contracts.HALT_DENIED_TOOL, contracts.HALT_PATH_GATE,
                                        contracts.HALT_TRACKER_WRITE_DENIED):
            continue
        lines.append("%s denied on %s" % (finding.get("tool") or "?", finding.get("target") or "?"))
    return lines


def _other_findings(digest):
    lines = []
    for finding in (digest or {}).get("findings") or []:
        if finding.get("class") in (contracts.HALT_DENIED_TOOL, contracts.HALT_PATH_GATE,
                                    contracts.HALT_TRACKER_WRITE_DENIED):
            continue
        lines.append(classify.finding_line(finding))
    return lines


def _comment_lines(comments):
    return [("%s: %s" % (entry.get("id"), entry.get("body", ""))).strip()
            for entry in comments or []]


def _gate_line(gate):
    if not gate:
        return "not run for this outcome"
    return "%s (exit %s), output in %s" % ("passed" if gate.get("ok") else "refused",
                                           gate.get("returncode"), gate.get("log"))


def _timing_line(digest, wall_seconds=None, active_seconds=None):
    if wall_seconds is None and active_seconds is None:
        return "not recorded"
    return "%.0f seconds active, %.0f seconds wall" % (active_seconds or 0, wall_seconds or 0)


def compound_command(depth, hint, backend):
    """The exact invocation the brief pins, so the process cannot drift into interactive mode.

    `backend` is required, not defaulted (backends KTD2). Only `run()` may default it: a second
    independent default here is how the brief's invocation and the CLI that reads it drift apart,
    which is the failure backends KTD15 exists to prevent."""
    return "%s %s %s %s" % (backends.build(backend).qualify_skill("ce-compound"),
                            contracts.COMPOUND_NON_INTERACTIVE, depth, hint)


def render(manifest, card, outcome, digest, comments, adapter, allowed_paths, backend,
           landing_ref=None, branch=None, commit_range=None, plan_path=None, gate=None,
           wall_seconds=None, active_seconds=None, halt_class=None, cause_line=None):
    """The closeout brief. Deterministic from its inputs, like the task brief, and it never
    receives the task process transcript (R27), only the digest the runner composed from it.

    `backend` is required for the same reason `compound_command`'s is. `halt_class`/`cause_line`
    are set only for `OUTCOME_HALTED`, the runner's own values for the halt already raised (R4);
    `cause_line` is defanged because, unlike a landing sha, it can carry task-influenced text (a
    denied call's captured argument, a dirty tree's file list) that must not close the data block
    or forge a runner instruction (R56). `landing_ref` is also passed for `OUTCOME_HALTED` when
    the task's own landed closeout already ran before this halt (a mirror push refusal or a
    failing final verify), naming it keeps the comment from reading as an undifferentiated halt
    on a card the runner already moved to a terminal status."""
    task_id = card.get("id")
    depth = depth_for(digest)
    envelope = (digest or {}).get("envelope") or {}
    landing_line = ""
    if outcome == OUTCOME_HALTED:
        cause_text = "Halt class: %s\nCause: %s" % (
            halt_class or "unknown", brief.defang(cause_line or "no cause line recorded"))
        landing_line = ("Landed at %s, but the run then halted.\n%s" % (landing_ref, cause_text)
                        if landing_ref else cause_text)
    elif outcome == OUTCOME_LANDED and landing_ref:
        landing_line = "Landing reference: %s" % landing_ref
    elif outcome == OUTCOME_LANDED:
        landing_line = "Landing reference: not recorded"
    else:
        landing_line = "No landing reference: this task did not land."
    if commit_range:
        landing_line += "\nCommit range: %s" % commit_range

    hint = "relay task %s, outcome %s" % (task_id, outcome)
    values = {
        "task_id": task_id,
        "outcome": outcome,
        "landing_line": landing_line,
        "branch": branch or "none",
        "plan_path": plan_path or envelope.get("plan_path") or "none recorded",
        "timing": _timing_line(digest, wall_seconds, active_seconds),
        "gate": _gate_line(gate),
        "blockers": _bullets(envelope.get("blockers") or []),
        "learnings": _bullets(envelope.get("learnings") or []),
        "denials": _bullets(_denial_lines(digest)),
        "findings": _bullets(_other_findings(digest)),
        "data_header": DATA_HEADER,
        "data_begin": DATA_BEGIN,
        "data_end": DATA_END,
        "title": brief.defang(str(card.get("title") or "")).strip(),
        "description": brief.defang(str(card.get("description") or "")).strip(),
        "comments": brief.defang(_bullets(_comment_lines(comments))),
        "duty_one": adapter.closeout_instructions(outcome),
        "compound_command": compound_command(depth, hint, backend),
        "compound_skill": backends.build(backend).qualify_skill("ce-compound"),
        "allowed_paths": _bullets(allowed_paths),
        "complete_line": contracts.COMPOUND_COMPLETE_LINE,
        "skipped_line": contracts.COMPOUND_SKIPPED_LINE,
    }
    path = os.path.join(brief.TEMPLATE_DIR, TEMPLATE)
    try:
        with open(path, encoding="utf-8") as handle:
            template = string.Template(handle.read())
    except OSError as exc:
        raise brief.BriefError("closeout template could not be read: %s" % exc)
    try:
        return template.substitute(values)
    except KeyError as exc:
        raise brief.BriefError("closeout template names an unknown placeholder %s" % exc)


def parse(last_message):
    """The ending contract. The terminal line must be the last thing in the message, so a run
    that kept going after printing it is unfinished rather than complete."""
    text = (last_message or "").strip()
    if not text:
        return RESULT_UNFINISHED
    last = ""
    for line in reversed(text.splitlines()):
        if line.strip():
            last = line.strip().strip("*`_ ")
            break
    if last == contracts.COMPOUND_COMPLETE_LINE:
        return RESULT_COMPLETE
    if last == contracts.COMPOUND_SKIPPED_LINE:
        return RESULT_SKIPPED
    return RESULT_UNFINISHED


def _closeout_task(manifest, task_id, backend, task_model=None):
    """A task record shaped for the launcher, carrying the closeout's own model and effort
    (R29): two bounded jobs that need judgement, not depth. `backend` is required for the same
    reason `compound_command`'s is.

    The manifest's closeout model is claude vocabulary. On any other backend it is not a model
    that CLI serves (U14 found codex refusing `sonnet` with a 400 and its Closeout dying without
    a terminal line), so a non claude Closeout runs on the Task's own model, which the operator
    already chose for that backend."""
    model = manifest.closeout.model
    if backend != manifest_module.DEFAULT_BACKEND and task_model:
        model = task_model
    return manifest_module.Task(id=task_id, model=model,
                                effort=manifest.closeout.effort, excluded=False, reason=None,
                                backend=backend)


def run(manifest, card, outcome, digest, comments, adapter, store, allowed_paths,
        backend, task_model=None,
        landing_ref=None, branch=None, commit_range=None, plan_path=None, gate=None,
        wall_seconds=None, active_seconds=None, halt_class=None, cause_line=None,
        timeout_seconds=None,
        **launch_kwargs):
    """Render, launch, and read the ending. Returns what happened; it changes no git state and
    writes nothing to the tracker itself. The caller runs the scope check and the push.

    The caller supplies the Task backend. It feeds all three consumers: the
    rendered brief, the launched CLI, and the normalizer that reads what that CLI wrote."""
    task_id = card.get("id")
    text = render(manifest, card, outcome, digest, comments, adapter, allowed_paths, backend,
                  landing_ref=landing_ref, branch=branch, commit_range=commit_range,
                  plan_path=plan_path, gate=gate, wall_seconds=wall_seconds,
                  active_seconds=active_seconds, halt_class=halt_class, cause_line=cause_line)
    brief_path = store.path("briefs", task_id + ".closeout.md")
    with open(brief_path, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.chmod(brief_path, 0o600)

    if timeout_seconds is None:
        timeout_seconds = manifest.timeouts.closeout_minutes * 60
    launch_result = launch.launch(
        manifest, _closeout_task(manifest, task_id, backend, task_model=task_model), text,
        store.path("logs", task_id + ".closeout.stdout.log"), timeout_seconds,
        allowed=allowed_tools(manifest, adapter),
        disallowed=contracts.CLOSEOUT_DISALLOWED_EXTRA, **launch_kwargs)

    # U7 runs over the closeout transcript too (R44). AE1's denied tracker write most often
    # happens here, after the code has already merged. The backend goes through: a closeout
    # launched on one CLI whose evidence is normalized as another decodes nothing, so `parse()`
    # sees no terminal line and every run appends a CLOSEOUT_UNFINISHED finding.
    closeout_digest = classify.classify(launch_result.transcript_path, launch_result,
                                        adapter.write_tool_patterns(), backend=backend)
    findings = [finding for finding in closeout_digest.get("findings") or []
                if finding.get("class") != contracts.HALT_NO_ENVELOPE]
    result = RESULT_UNFINISHED if launch_result.timed_out else parse(closeout_digest.get("last_message_tail"))
    if result == RESULT_UNFINISHED:
        findings.append({
            "class": contracts.CLOSEOUT_UNFINISHED,
            "task": task_id,
            "last_message": closeout_digest.get("last_message") or "(no final message)",
        })
    return CloseoutResult(result, findings, closeout_digest, launch_result,
                          brief_path, state.sha256_of(text))


def confirm_blocked_comment(adapter, task_id, baseline_comment_id):
    """R42: a blocked task is only legible to an operator who was not watching if the blocker
    reached the card. Returns a finding when it did not, or when the tracker could not be read,
    so the summary prints the card to check by hand. Never a halt: the run continues."""
    try:
        newer = adapter.comments_since(task_id, baseline_comment_id)
    except Exception as exc:
        return {"class": contracts.BLOCKED_UNRECORDED, "task": task_id,
                "evidence": "the tracker could not be read to confirm the comment: %s" % exc}
    if newer:
        return None
    return {"class": contracts.BLOCKED_UNRECORDED, "task": task_id,
            "evidence": "no comment newer than %r after the closeout" % baseline_comment_id}
