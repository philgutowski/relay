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
# Backward-compatible Claude pin. New terminal records use the per-backend values in
# BACKEND_PINS, which are the single source of truth for all three CLIs.
CLI_VERSION_TESTED = "2.1.250"

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
# Relay's own addition to the envelope, not part of the plugin's return-to-caller contract the
# four keys above pin against (docs/backlog.md line two).
ENVELOPE_LEARNINGS_KEY = "learnings"
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

# Skill names. The brief pins every plugin skill in its backend's own invocation form, and the
# classifier flags a Skill call outside that form as a substitution (R43). The 2026-08-25 proof
# run invoked the harness `code-review` twice when the brief said `/ce-code-review`. The forms
# themselves live on BACKEND_PINS below as `skill_form`, one per backend, and are read only
# through `backends.qualify_skill`; there is deliberately no module-level prefix constant here,
# because a second copy of claude's form is a copy that can drift from the pin.
REQUIRED_SKILLS = ("ce-plan", "ce-work", "ce-simplify-code", "ce-code-review", "ce-compound", "lfg")

# CLI contracts, observed on CLI_VERSION_TESTED and documented nowhere.
# A denied tool call is a `user` transcript line whose tool_result content begins with this.
# Backends U6 found a real Bash denial reads "Permission to use Bash with command <cmd> has
# been denied.", naming the command between the tool and the verdict; a Jira or other named-tool
# denial has no such clause. `.*` (not anchored immediately after the tool name) covers both,
# proven against tests/fixtures/backends/claude/denial-refusal.jsonl, a real capture this
# anchored-immediately form never matched.
DENIAL_REGEX = re.compile(r"^Permission to use (\w+)\b.*has been denied")
# Under dontAsk an Edit or Write on a path under .claude/ is denied regardless of the allowlist.
CLAUDE_DIR_PATH_REGEX = re.compile(r"(^|/)\.claude/")
# The pre-flight scan form from the solutions doc: catches the path inside prose, quotes,
# and markdown wrappers (a link's `[`, and an asterisk for bold, italic, or a list marker).
CLAUDE_DIR_SCAN_REGEX = re.compile(r"(^|[\s\"'`(/\[*])\.claude/", re.MULTILINE)

# Transcript line types (the session jsonl the CLI writes). Only these three carry evidence.
TRANSCRIPT_TYPE_ASSISTANT = "assistant"
TRANSCRIPT_TYPE_USER = "user"
TRANSCRIPT_TYPE_LAST_PROMPT = "last-prompt"

# Claude's argv flag set (R10, outer loop KTD7). Other backends' flags live on BACKEND_PINS
# and in the backend modules. The stub accepts this set.
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
FORBIDDEN_PERMISSION_MODE = "bypassPermissions"
OUTPUT_FORMAT = "stream-json"

# Per-backend launch facts, every one observed in U1 by running the installed CLI against
# ~/Documents/PhilAI/relay-proof/target on 2026-08-28. Nothing here is read from documentation.
# Pins are the producer. backends.Capability is the frozen view U4 copies.
# Do not restate these values elsewhere.
#
# The fixtures these were taken from are in tests/fixtures/backends/, one directory per backend,
# and tests/fixtures/backends/README.md names which task produced which file.
BACKEND_PINS = {
    "claude": {
        "binary": "claude",
        "version_tested": "2.1.250",
        # `claude --version` leads with the number, so the leading-digit parse works here and
        # nowhere else. See the two entries below.
        "version_output_sample": "2.1.250 (Claude Code)",
        "plugin_version": "3.23.4",
        "plugin_query": ("claude", "plugin", "list"),
        # `claude plugin list` prints `Version:` and `Status:` as separate lines in the same
        # entry; a disabled plugin still reports its installed version on the `Version:` line.
        # The interior scan is bounded to this entry (never crossing the next `❯` bullet) so a
        # disabled compound-engineering entry cannot borrow a later, unrelated plugin's enabled
        # status. skills-relay-contracts-129-disabled-plugin-ready. Not covered: two entries for
        # this same plugin at different scopes (e.g. a disabled project-scope install followed by
        # an enabled user-scope one) would let re.search retry past the first and match the
        # second; unconfirmed whether the real CLI can list the same plugin twice.
        "plugin_version_pattern": (r"(?ims)^\s*❯\s*compound-engineering@compound-engineering-plugin\s+"
                                   r"Version:\s*(?P<version>\d+(?:\.\d+)+)"
                                   r"(?:(?!❯).)*?Status:\s*(?:\S+\s+)?enabled\b"),
        "headless_flag": "-p",
        "session_id_choosable": True,
        "permission_mode": "dontAsk",
        "forbidden_permission_modes": ("bypassPermissions",),
        "output_format": ("--output-format", "stream-json", "--verbose"),
        "allow_flag": "--allowedTools",
        "deny_flag": "--disallowedTools",
        # Demonstrated, not assumed (R25): tests/fixtures/backends/claude/denial-refusal.jsonl
        # holds a refused `rm -rf`, and the target file was still present afterwards.
        "enforces_at_launch": True,
        "skill_form": "compound-engineering:%s",
        "evidence": "session jsonl under ~/.claude/projects/<slug>/<session-id>.jsonl",
        "credential_prefixes": ("ANTHROPIC_", "CLAUDE_"),
        "credential_file": "~/.claude/.credentials.json",
        "nesting_markers": ("CLAUDECODE", "CLAUDE_CODE_"),
        "writes_into_worktree": False,
        "extra_writable_dirs": (),
        "config_overrides": (),
        "strict_config": False,
    },
    "codex": {
        "binary": "codex",
        "version_tested": "0.149.0",
        # Leads with a name token, so the leading-digit parse returns None. KTD8 is why parsing
        # is per-backend rather than one regex.
        "version_output_sample": "codex-cli 0.149.0",
        "plugin_version": "3.23.4",
        "plugin_query": ("codex", "plugin", "list"),
        "plugin_version_pattern": (r"(?im)^compound-engineering@compound-engineering-plugin\s+"
                                   r"installed,\s+enabled\s+(?P<version>\d+(?:\.\d+)+)\s+"),
        "headless_flag": "exec",
        # Codex assigns its own thread id, so the runner names the evidence instead (KTD4).
        "session_id_choosable": False,
        "permission_mode": "workspace-write",
        "forbidden_permission_modes": ("danger-full-access",
                                       "--dangerously-bypass-approvals-and-sandbox"),
        "output_format": ("--json",),
        "allow_flag": None,
        "deny_flag": None,
        # No per-tool deny flag exists, so no refusal can be demonstrated and R25 records it as
        # not enforcing. R19's acceptance sentence, R21's landing bound, and R24's audit are the
        # compensating controls. Codex does refuse some `rm -f` shapes on its own, but that is
        # its own built-in judgment and not something the manifest's disallow list can reach.
        #
        # Until 2026-09-01 the sandbox's absent network was also holding shut, on this backend
        # alone, every remote reaching pattern in DISALLOWED_TOOLS and all of
        # CLOSEOUT_DISALLOWED_EXTRA: with no deny flag they never reach the argv, so a push
        # simply could not complete. `config_overrides` below removes that, and nothing detects a
        # push from a Task or Closeout here yet. Issue #60.
        "enforces_at_launch": False,
        "skill_form": "$%s",
        "evidence": "stdout log plus --output-last-message file; session jsonl at "
                    "~/.codex/sessions/YYYY/MM/DD/rollout-<timestamp>-<thread-id>.jsonl",
        "credential_prefixes": ("CODEX_", "OPENAI_"),
        "credential_file": "~/.codex/auth.json",
        "nesting_markers": ("CODEX_SANDBOX", "CODEX_HOME"),
        "writes_into_worktree": False,
        # U1 finding, not in the plan. Under `--sandbox workspace-write` every write beneath
        # .git/ is refused ("Unable to create '.../.git/index.lock': Operation not permitted"),
        # so a Task cannot branch or commit at all. Passing the repository's own .git as an
        # extra writable directory is what makes the sandbox usable for Relay's purposes.
        "extra_writable_dirs": ("<repo>/.git",),
        # Issue #51, observed on codex-cli 0.151.0 on 2026-09-01. Under `--sandbox
        # workspace-write` the sandbox blocks network by default, so `gh` cannot reach
        # api.github.com and a Task cannot move or comment its own card. Every codex task cost a
        # hand landing. This override restores the reach; the session header then reads
        # "(network access enabled)".
        #
        # The grant is all or nothing. `sandbox_workspace_write` takes four fields,
        # writable_roots, network_access, exclude_tmpdir_env_var, exclude_slash_tmp, and no host
        # allowlist: `-c 'sandbox_workspace_write.allowed_domains=["api.github.com"]'` is refused
        # with "unknown configuration field". So a codex Task reaches every host, not just the
        # tracker, and run._unenforced_scalar says so on the record.
        "config_overrides": ("sandbox_workspace_write.network_access=true",),
        # Rides with the override rather than standing on its own. Without it, a key this CLI
        # does not recognize is accepted and ignored: the process runs, the sandbox stays fenced,
        # and the only symptom is the blocked halt the override exists to remove. Neither the
        # suite nor the stub can see that, because both derive the argv from this same pin. With
        # it, the same key fails before launch with "Error loading config.toml: unknown
        # configuration field ... in -c/--config override". The cost is that it also validates the
        # operator's own ~/.codex/config.toml, so a field this codex version rejects there fails
        # every launch, loudly, which is the trade this pin accepts.
        "strict_config": True,
    },
    "grok": {
        "binary": "grok",
        "version_tested": "1.0.5",
        "version_output_sample": "grok 1.0.5 (5115b46bc909) [stable]",
        "plugin_version": "3.23.4",
        "plugin_query": ("grok", "plugin", "list", "--json"),
        # `grok plugin list --json` carries only a `"status": "installed"` field, unchanged by
        # `grok plugin disable`/`enable` as of the version tested; there is no field this pattern
        # can require to exclude a disabled plugin the way the claude and codex patterns do.
        "plugin_version_pattern": (r'(?s)\{(?=[^{}]*"name"\s*:\s*"compound-engineering")'
                                   r'(?=[^{}]*"version"\s*:\s*"(?P<version>\d+(?:\.\d+)+)")[^{}]*\}'),
        "headless_flag": "-p",
        "session_id_choosable": True,
        # U1 finding, and a correction to the plan's Assumptions and KTD6. Grok accepts
        # `dontAsk` at launch and then cancels every tool call the task makes, reporting
        # "User cancelled the execution for tool `run_terminal_command`" with no human present
        # to have cancelled anything. Reproduced five times: two full pipeline runs that died
        # partway through planning, and three single-command probes. `auto` is the mode that
        # runs the task AND still refuses a denied call, so it is the non-bypass posture here.
        "permission_mode": "auto",
        "forbidden_permission_modes": ("bypassPermissions", "dontAsk"),
        "output_format": ("--output-format", "streaming-json"),
        "allow_flag": "--allow",
        "deny_flag": "--deny",
        # Demonstrated (R25): tests/fixtures/backends/grok/denial-refusal.jsonl holds
        # "Denied by permission policy: deny rule on bash matching \"rm -rf*\"", captured with
        # the target directory still present afterwards. A malformed rule is refused at launch
        # ("malformed rule: missing closing parenthesis") rather than silently accepted, and a
        # bare `Skill` entry, which closeout.BASE_TOOLS carries, is accepted.
        "enforces_at_launch": True,
        # U1 finding, resolving the plan's open question. Grok registers plugin skills under
        # bare names, with no plugin namespace, so the Claude prefix would not resolve.
        "skill_form": "/%s",
        "evidence": "~/.grok/sessions/<url-encoded-cwd>/<session-id>/updates.jsonl",
        "credential_prefixes": ("GROK_", "XAI_"),
        "credential_file": "~/.grok/auth.json",
        "nesting_markers": ("GROK_SANDBOX",),
        "writes_into_worktree": False,
        "extra_writable_dirs": (),
        "config_overrides": (),
        "strict_config": False,
    },
}

# Round six #40: a task chasing a hung unittest child ran `kill -9 <pids...>` and swept in the
# Runner's own PID and its caffeinate wrapper. kill/pkill/killall had no disallow entry, so
# nothing at the permission layer stopped it on an enforcing backend. Named separately so
# classify.scan_self_kill (KTD2) can check a matched command against exactly these globs without
# re-deriving them.
KILL_LIKE_TOOLS = (
    "Bash(kill*)",
    "Bash(pkill*)",
    "Bash(killall*)",
)

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
) + KILL_LIKE_TOOLS


def disallow_inner(pattern):
    """The glob inside a `Bash(...)` rule, or the pattern unchanged."""
    if pattern.startswith("Bash(") and pattern.endswith(")"):
        return pattern[5:-1]
    return pattern


# Named subset of DISALLOWED_TOOLS. A match on an unenforced backend refuses the landing
# rather than only annotating it. Force push, hard reset, and recursive delete. git clean
# and git checkout -- .* stay in the parent tuple and land with a finding.
DESTRUCTIVE_TOOLS = (
    "Bash(git push --force*)",
    "Bash(git push -f*)",
    "Bash(git push --force-with-lease*)",
    "Bash(git push * +*)",
    "Bash(git reset --hard*)",
    "Bash(rm -rf*)",
    "Bash(rm -fr*)",
    "Bash(rm -r *)",
    "Bash(rm -R *)",
)

# The closeout commits and the runner pushes for it (KTD15). A push from inside the closeout
# would put a commit on the remote before the runner's scope check could bound it, and a local
# reset cannot undo that, so the closeout's disallow list refuses every push spelling. The task
# process keeps the ordinary list, because in pr_terminal mode it has to push its own branch.
CLOSEOUT_DISALLOWED_EXTRA = (
    "Bash(git push*)",
    "Bash(git -C * push*)",
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
# The case KTD6's table did not cover: a defect or a library exception the runner did not
# anticipate. Without a class for it the run loop had no way to stop the way it promises to,
# so an unexpected error became a traceback and left the record reading running forever.
HALT_UNEXPECTED_ERROR = "unexpected_error"

# Findings the closeout raises (U9). Neither halts a run: the runner's own verify decides
# landing, and a card that went uncommented is a checklist line for the operator, not a stop.
CLOSEOUT_UNFINISHED = "closeout_unfinished"
BLOCKED_UNRECORDED = "blocked_unrecorded"
# A disallowed call that ran on a backend that does not enforce at launch. Finding only:
# landing refusal for the destructive subset is unexpected_error on the record.
UNENFORCED_DISALLOWED = "unenforced_disallowed"
# Round six #40: a stale-lease reclaim whose crashed task's own stdout log named the previous
# Runner's PID in a kill/pkill/killall command (classify.scan_self_kill). Finding only: the
# record's own halt_class stays runner_crashed (KTD6's closed set), this just says why.
RUNNER_SELF_KILL = "runner_self_kill"
# Round six #49: a task whose last message reads as waiting on background work that will not
# resume headless ("standing by", "will resume", "once the run finishes"). Finding only: the
# record's own halt_class stays whatever classify or the git-tree check already assigned (often
# no_envelope or unclean_exit), this just says the mechanism instead of leaving the Cause line
# to read only the downstream symptom.
WAITING_LAST_MESSAGE = "waiting_last_message"

# The .claude/ backstop's operator sentence. HALT_LINES[path_gate] is {detail}; this
# raiser and classify's path_gate promotion fill it so the Cause line stays true.
PATH_GATE_CLAUDE_DIR = (
    "edit under .claude/ denied by the task's permission posture; "
    "apply attended, see solutions doc"
)

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
    HALT_UNEXPECTED_ERROR,
)

# Classes that always stop the whole run (issue #15). Each puts something outside the failing
# task in question, so no later task's assumptions hold. Every other class is a candidate for
# continuing past when the manifest opts in, decided from the repo's state after the halt by
# gitwrite.resume_disposition rather than from the class name: the same class can leave the
# repo usable (a gate command that failed on the task branch) or not (a push that failed after
# the merge, leaving the default ahead of origin), and only the repo can tell them apart.
RUN_SCOPED_HALT_CLASSES = (
    HALT_REMOTE_ADVANCED,   # origin moved under the runner; every later baseline is suspect
    HALT_RUNNER_CRASHED,    # the lease was lost; another runner may be live in this repo
    HALT_UNEXPECTED_ERROR,  # a defect or library error with unknown blast radius
)

# Classes that mean the Closeout process itself just misbehaved. Distinct from
# RUN_SCOPED_HALT_CLASSES (neither stops the whole run, per _continue_past): the run.py halt
# comment (KTD3, R5) skips relaunching Closeout on these, since relaunching the exact mechanism
# that just went out of scope would trust it again on the strength of the trust that just failed.
# HALT_TRACKER_WRITE_DENIED is deliberately absent: it is a FINDING_CLASSES member, attached to a
# record rather than ever raised as a _Halt's own class, so it can never reach this check.
CLOSEOUT_MISBEHAVED_HALT_CLASSES = (
    HALT_CLOSEOUT_OUT_OF_SCOPE,
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
    UNENFORCED_DISALLOWED,
    RUNNER_SELF_KILL,
    WAITING_LAST_MESSAGE,
)

# Every class that can reach a summary line: the closed halt class set of KTD6, plus the
# findings that are never a record's own class but still have to print.
LINE_CLASSES = HALT_CLASSES + (
    CLOSEOUT_UNFINISHED, BLOCKED_UNRECORDED, UNENFORCED_DISALLOWED, RUNNER_SELF_KILL,
    WAITING_LAST_MESSAGE,
)

HALT_LINES = {
    HALT_LANDED: "landed at {ref}",
    HALT_BLOCKED_ENVELOPE: "blocked: {blocker}",
    HALT_NO_ENVELOPE: "exited without a return envelope; last message: {last_message}",
    HALT_DENIED_TOOL: "{tool} denied by the task's permission posture on {target}",
    HALT_PATH_GATE: "{detail}",
    HALT_TRACKER_WRITE_DENIED: "code landed, card unmoved: {tool} denied",
    HALT_REMOTE_ADVANCED: "remote moved during the task; merge aborted at {sha}",
    HALT_CLOSEOUT_OUT_OF_SCOPE: "closeout changed {path} outside {allowed}",
    # `status_before`, not `status`: the record is a rendering source too and carries its
    # own post crash `status`, which used to shadow the evidence and print "during halted"
    # for every crash. The tree is deliberately absent: the reclaim path that raises this
    # most often runs in a later process that never saw the repository.
    HALT_RUNNER_CRASHED: "runner died during {status_before} on {branch}",
    HALT_SKILL_SUBSTITUTION: "ran {name} instead of {required}",
    HALT_GATE_REFUSED: "gate refused {branch} at {sha}; output in {log}",
    HALT_PARTIAL_LANDING: "landed at {sha} but card reads {card_status}",
    HALT_TIMEOUT: "timed out after {active_minutes} active minutes ({wall_minutes} wall); tree {tree} on {branch}",
    HALT_UNCLEAN_EXIT: "left the tree dirty on {branch}",
    HALT_CI_UNDECIDED: "PR {url} open, CI undecided after {minutes} minutes",
    HALT_UNEXPECTED_ERROR: "the runner hit an unexpected {error_type} on {task}: {error}",
    CLOSEOUT_UNFINISHED: "the closeout ended without a terminal line; last message: {last_message}",
    BLOCKED_UNRECORDED: "blocked and the card carries no new comment; check {task} by hand",
    UNENFORCED_DISALLOWED: "{tool} ran {argument} at line {line} matching {pattern}",
    RUNNER_SELF_KILL: "self-kill: {command} named the runner's own pid {victim_pid} among {pids}",
    WAITING_LAST_MESSAGE: "ended the turn waiting on background work that does not resume headless: {last_message}",
}

# The digest classify.classify() (U7) guarantees, read by run.py and closeout.py via
# digest.get(...). tests/test_contracts.py asserts both readers stay inside this set and that
# classify keeps setting every key either reader uses.
DIGEST_KEYS = frozenset((
    "transcript_path",
    "transcript_present",
    "exit_code",
    "timed_out",
    "line_count",
    "malformed_lines",
    "tool_calls",
    "findings",
    # R13, KTD5: True when the evidence source could not be read, so `findings` is None rather
    # than empty. A reader that cannot tell "we looked and found none" from "we could not look"
    # reports a runner fault as the task's silence.
    "findings_unavailable",
    "envelope",
    "last_message",
    "last_message_tail",
    "halt_class",
    "routable",
    # Backends U6, R5: halt-class constants this backend's evidence cannot show, so a reader can
    # tell "not checked" from "checked, none found" per finding class.
    "undetectable",
))

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
DEFAULT_TASK_BRANCH_PREFIX = "relay/"

# A push can run the project's gate inside a pre-push hook, so it is bounded by the gate's own
# timeout plus the network transfer, never by gitread's read timeout. See
# docs/solutions/ for why: a 120 second read bound killed a push whose hook was running a 216
# second suite, and the runner reported it as an unexpected error.
PUSH_NETWORK_MARGIN_SECONDS = 120
DEFAULT_DOCS_ROOT = "docs"
CONCEPTS_FILE = "CONCEPTS.md"

# Lease timing (KTD10). The TTL must stay shorter than any task timeout.
LEASE_HEARTBEAT_SECONDS = 60
LEASE_TTL_SECONDS = 600

STATE_SCHEMA_VERSION = 2


def slug_for(path):
    """The CLI's project slug (KTD7): the absolute path with every character outside
    [A-Za-z0-9] replaced by a hyphen. Verified against the directories under ~/.claude/projects.
    Callers pass a realpath, because macOS temp dirs are symlinks and both sides must agree."""
    return re.sub(r"[^A-Za-z0-9]", "-", path)


def transcript_path(home, cwd_realpath, session_id):
    """Where the CLI writes the session transcript for a process started in cwd."""
    import os

    return os.path.join(home, ".claude", "projects", slug_for(cwd_realpath), session_id + ".jsonl")
