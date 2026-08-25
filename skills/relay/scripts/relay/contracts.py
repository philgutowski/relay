"""Every string Relay depends on from outside itself, pinned in one place.

A contract here is a fact about another program: the compound-engineering plugin, the Claude
Code CLI, or the transcript the CLI writes. Each pin names its source so a version bump is one
diff and tests/test_contracts.py can grep the installed plugin for the string. Relay's own
vocabulary (halt classes, record statuses, the envelope fence tag) lives here too so classify,
verify, and summary share one set.
"""
import re

# The plugin and CLI versions these pins were read from.
PLUGIN_NAME = "compound-engineering"
PLUGIN_MIN_VERSION = "3.23.4"
CLI_VERSION_TESTED = "2.1.245"

# Source paths are relative to the installed plugin root, for the pin check test.
# Each entry: (constant name, string that must appear in the source, source path).
PLUGIN_PINS = (
    # lfg prints this token when the whole pipeline is complete. skills/lfg/SKILL.md step 10.
    ("LFG_TERMINAL_TOKEN", "<promise>DONE</promise>", "skills/lfg/SKILL.md"),
    # ce-work enters return-to-caller mode on this leading token followed by a plan path.
    ("CE_WORK_RETURN_MODE", "mode:return-to-caller", "skills/ce-work/references/input-triage.md"),
    # The envelope's status field and its three values.
    ("ENVELOPE_STATUS_KEY", "`status`", "skills/ce-work/references/return-to-caller.md"),
    ("ENVELOPE_STATUS_COMPLETE", "`complete`", "skills/ce-work/references/return-to-caller.md"),
    ("ENVELOPE_STATUS_BLOCKED", "`blocked`", "skills/ce-work/references/return-to-caller.md"),
    ("ENVELOPE_STATUS_FAILED", "`failed`", "skills/ce-work/references/return-to-caller.md"),
    ("ENVELOPE_BLOCKERS_KEY", "`blockers`", "skills/ce-work/references/return-to-caller.md"),
    ("ENVELOPE_CHANGED_FILES_KEY", "`changed_files`", "skills/ce-work/references/return-to-caller.md"),
    ("ENVELOPE_PLAN_PATH_KEY", "`plan_path`", "skills/ce-work/references/return-to-caller.md"),
    # ce-compound non-interactive grammar and its two terminal lines.
    ("COMPOUND_NON_INTERACTIVE", "mode:non-interactive", "skills/ce-compound/SKILL.md"),
    ("COMPOUND_DEPTH_LIGHTWEIGHT", "depth:lightweight", "skills/ce-compound/SKILL.md"),
    ("COMPOUND_DEPTH_FULL", "depth:full", "skills/ce-compound/SKILL.md"),
    ("COMPOUND_COMPLETE_LINE", "Documentation complete", "skills/ce-compound/references/report.md"),
    ("COMPOUND_SKIPPED_LINE", "Documentation skipped", "skills/ce-compound/references/report.md"),
    # ce-plan runs its own non-interactive document review, so a brief adds no ce-doc-review step.
    ("CE_PLAN_RUNS_DOC_REVIEW", "Document review is mandatory for a Durable plan", "skills/ce-plan/SKILL.md"),
    # ce-code-review agent mode and its verdict strings.
    ("CODE_REVIEW_AGENT_MODE", "mode:agent", "skills/ce-code-review/references/modes-and-output.md"),
    ("CODE_REVIEW_VERDICT_READY", "Ready to merge", "skills/ce-code-review/references/finish-review.md"),
    ("CODE_REVIEW_VERDICT_FIXES", "Ready with fixes", "skills/ce-code-review/references/finish-review.md"),
    ("CODE_REVIEW_VERDICT_NOT_READY", "Not ready", "skills/ce-code-review/references/finish-review.md"),
)

LFG_TERMINAL_TOKEN = "<promise>DONE</promise>"
CE_WORK_RETURN_MODE = "mode:return-to-caller"
ENVELOPE_STATUS_KEY = "status"
ENVELOPE_STATUS_COMPLETE = "complete"
ENVELOPE_STATUS_BLOCKED = "blocked"
ENVELOPE_STATUS_FAILED = "failed"
ENVELOPE_STATUSES = (ENVELOPE_STATUS_COMPLETE, ENVELOPE_STATUS_BLOCKED, ENVELOPE_STATUS_FAILED)
ENVELOPE_BLOCKERS_KEY = "blockers"
ENVELOPE_CHANGED_FILES_KEY = "changed_files"
ENVELOPE_PLAN_PATH_KEY = "plan_path"
COMPOUND_NON_INTERACTIVE = "mode:non-interactive"
COMPOUND_DEPTH_LIGHTWEIGHT = "depth:lightweight"
COMPOUND_DEPTH_FULL = "depth:full"
COMPOUND_COMPLETE_LINE = "Documentation complete"
COMPOUND_SKIPPED_LINE = "Documentation skipped"
COMPOUND_TERMINAL_LINES = (COMPOUND_COMPLETE_LINE, COMPOUND_SKIPPED_LINE)
CE_PLAN_RUNS_DOC_REVIEW = True
CODE_REVIEW_AGENT_MODE = "mode:agent"
CODE_REVIEW_VERDICT_READY = "Ready to merge"
CODE_REVIEW_VERDICT_FIXES = "Ready with fixes"
CODE_REVIEW_VERDICT_NOT_READY = "Not ready"
CODE_REVIEW_VERDICTS = (CODE_REVIEW_VERDICT_READY, CODE_REVIEW_VERDICT_FIXES, CODE_REVIEW_VERDICT_NOT_READY)

# Relay's own envelope convention (KTD8): the brief asks for the envelope inside a fenced block
# with this tag, so a quoted `status:` elsewhere in the final message cannot be mistaken for it.
ENVELOPE_FENCE_TAG = "relay-envelope"

# Skill names. The brief pins every plugin skill with this prefix, and the classifier flags a
# Skill call whose name lacks it as a substitution (R43). The 2026-08-25 proof run invoked the harness
# `code-review` twice when the brief said `/ce-code-review`.
SKILL_PREFIX = "compound-engineering:"
REQUIRED_SKILLS = ("ce-plan", "ce-work", "ce-simplify-code", "ce-code-review", "ce-compound", "lfg")

# CLI contracts, observed on CLI_VERSION_TESTED and documented nowhere.
# A denied tool call is a `user` transcript line whose tool_result content begins with this.
DENIAL_REGEX = re.compile(r"^Permission to use (\w+) has been denied")
# Under dontAsk an Edit or Write on a path under .claude/ is denied regardless of the allowlist.
CLAUDE_DIR_PATH_REGEX = re.compile(r"(^|/)\.claude/")
# The pre-flight scan form from the solutions doc: catches the path inside prose and quotes.
CLAUDE_DIR_SCAN_REGEX = re.compile(r"(^|[\s\"'`(/])\.claude/", re.MULTILINE)

# Transcript line types (the session jsonl the CLI writes). Only these three carry evidence.
TRANSCRIPT_TYPE_ASSISTANT = "assistant"
TRANSCRIPT_TYPE_USER = "user"
TRANSCRIPT_TYPE_LAST_PROMPT = "last-prompt"

# The flag set the runner passes to `claude -p` (R10, KTD7). The stub accepts the same set.
CLI_FLAGS = (
    "--session-id",
    "--model",
    "--effort",
    "--permission-mode",
    "--allowedTools",
    "--disallowedTools",
    "--output-format",
    "--verbose",
)
PERMISSION_MODE = "dontAsk"
FORBIDDEN_PERMISSION_MODE = "bypassPermissions"
OUTPUT_FORMAT = "stream-json"

# R10 disallow list with every variant spelling. Defence in depth; landing safety rests on the
# runner owning merge and push.
DISALLOWED_TOOLS = (
    "Bash(git push --force*)",
    "Bash(git push -f*)",
    "Bash(git push --force-with-lease*)",
    "Bash(git push * +*)",
    "Bash(git reset --hard*)",
    "Bash(git checkout -- .*)",
    "Bash(git clean*)",
    "Bash(rm -rf*)",
    "Bash(rm -fr*)",
    "Bash(rm -r *)",
    "Bash(rm -R *)",
)

# Task record statuses (the state machine in the plan's design section).
STATUS_PENDING = "pending"
STATUS_EXCLUDED = "excluded"
STATUS_RUNNING = "running"
STATUS_MERGING = "merging"
STATUS_BLOCKED = "blocked"
STATUS_HALTED = "halted"
STATUS_LANDED = "landed"
RECORD_STATUSES = (
    STATUS_PENDING,
    STATUS_EXCLUDED,
    STATUS_RUNNING,
    STATUS_MERGING,
    STATUS_BLOCKED,
    STATUS_HALTED,
    STATUS_LANDED,
)
# Statuses a reclaimed stale lease turns into halted with class runner_crashed (R55).
IN_FLIGHT_STATUSES = (STATUS_RUNNING, STATUS_MERGING)

# Halt classes (KTD6). `HALT_LINES` are the summary cause line templates, filled from evidence.
HALT_LANDED = "landed"
HALT_BLOCKED_ENVELOPE = "blocked_envelope"
HALT_NO_ENVELOPE = "no_envelope"
HALT_DENIED_TOOL = "denied_tool"
HALT_PATH_GATE = "path_gate"
HALT_TRACKER_WRITE_DENIED = "tracker_write_denied"
HALT_REMOTE_ADVANCED = "remote_advanced"
HALT_CLOSEOUT_OUT_OF_SCOPE = "closeout_out_of_scope"
HALT_RUNNER_CRASHED = "runner_crashed"
HALT_SKILL_SUBSTITUTION = "skill_substitution"
HALT_GATE_REFUSED = "gate_refused"
HALT_PARTIAL_LANDING = "partial_landing"
HALT_TIMEOUT = "timeout"
HALT_UNCLEAN_EXIT = "unclean_exit"
HALT_CI_UNDECIDED = "ci_undecided"

# Findings the closeout raises (U9). Neither halts a run: the runner's own verify decides
# landing, and a card that went uncommented is a checklist line for the operator, not a stop.
CLOSEOUT_UNFINISHED = "closeout_unfinished"
BLOCKED_UNRECORDED = "blocked_unrecorded"

HALT_CLASSES = (
    HALT_LANDED,
    HALT_BLOCKED_ENVELOPE,
    HALT_NO_ENVELOPE,
    HALT_DENIED_TOOL,
    HALT_PATH_GATE,
    HALT_TRACKER_WRITE_DENIED,
    HALT_REMOTE_ADVANCED,
    HALT_CLOSEOUT_OUT_OF_SCOPE,
    HALT_RUNNER_CRASHED,
    HALT_SKILL_SUBSTITUTION,
    HALT_GATE_REFUSED,
    HALT_PARTIAL_LANDING,
    HALT_TIMEOUT,
    HALT_UNCLEAN_EXIT,
    HALT_CI_UNDECIDED,
)

# Classes that are findings attached to a record rather than the record's own class.
FINDING_CLASSES = (
    HALT_DENIED_TOOL,
    HALT_PATH_GATE,
    HALT_TRACKER_WRITE_DENIED,
    HALT_SKILL_SUBSTITUTION,
    HALT_NO_ENVELOPE,
    CLOSEOUT_UNFINISHED,
    BLOCKED_UNRECORDED,
)

# Every class that can reach a summary line: the closed halt class set of KTD6, plus the two
# closeout findings, which are never a record's own class but still have to print.
LINE_CLASSES = HALT_CLASSES + (CLOSEOUT_UNFINISHED, BLOCKED_UNRECORDED)

HALT_LINES = {
    HALT_LANDED: "landed at {ref}",
    HALT_BLOCKED_ENVELOPE: "blocked: {blocker}",
    HALT_NO_ENVELOPE: "exited without a return envelope; last message: {last_message}",
    HALT_DENIED_TOOL: "{tool} denied under dontAsk on {target}",
    HALT_PATH_GATE: "edit under .claude/ denied under dontAsk; apply attended, see solutions doc",
    HALT_TRACKER_WRITE_DENIED: "code landed, card unmoved: {tool} denied",
    HALT_REMOTE_ADVANCED: "remote moved during the task; merge aborted at {sha}",
    HALT_CLOSEOUT_OUT_OF_SCOPE: "closeout changed {path} outside {allowed}",
    HALT_RUNNER_CRASHED: "runner died during {status}; tree {tree} on {branch}",
    HALT_SKILL_SUBSTITUTION: "ran {name} instead of {required}",
    HALT_GATE_REFUSED: "gate refused {branch} at {sha}; output in {log}",
    HALT_PARTIAL_LANDING: "landed at {sha} but card reads {card_status}",
    HALT_TIMEOUT: "timed out after {active_minutes} active minutes ({wall_minutes} wall); tree {tree} on {branch}",
    HALT_UNCLEAN_EXIT: "left the tree dirty on {branch}",
    HALT_CI_UNDECIDED: "PR {url} open, CI undecided after {minutes} minutes",
    CLOSEOUT_UNFINISHED: "the closeout ended without a terminal line; last message: {last_message}",
    BLOCKED_UNRECORDED: "blocked and the card carries no new comment; check {task} by hand",
}

# Terminal record run statuses (R30, U3 step 6).
RUN_COMPLETED = "completed"
RUN_HALTED = "halted"
RUN_CRASHED = "crashed"

# Defaults from KTD11, applied by name in validate so nothing is silent.
DEFAULT_TASK_TIMEOUT_MINUTES = 120
DEFAULT_CI_POLL_MINUTES = 30
DEFAULT_CLOSEOUT_TIMEOUT_MINUTES = 20
DEFAULT_CLOSEOUT_MODEL = "sonnet"
DEFAULT_CLOSEOUT_EFFORT = "medium"
DEFAULT_GATE_TIMEOUT_MINUTES = 30
DEFAULT_DOCS_ROOT = "docs"
CONCEPTS_FILE = "CONCEPTS.md"

# Lease timing (KTD10). The TTL must stay shorter than any task timeout.
LEASE_HEARTBEAT_SECONDS = 60
LEASE_TTL_SECONDS = 600

STATE_SCHEMA_VERSION = 1


def slug_for(path):
    """The CLI's project slug (KTD7): the absolute path with every character outside
    [A-Za-z0-9] replaced by a hyphen. Verified against the directories under ~/.claude/projects.
    Callers pass a realpath, because macOS temp dirs are symlinks and both sides must agree."""
    return re.sub(r"[^A-Za-z0-9]", "-", path)


def transcript_path(home, cwd_realpath, session_id):
    """Where the CLI writes the session transcript for a process started in cwd."""
    import os

    return os.path.join(home, ".claude", "projects", slug_for(cwd_realpath), session_id + ".jsonl")
