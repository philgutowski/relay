"""Mutating git calls and the runner's git tail (U8).

Everything in this module can move the target repo. That is why it is a separate module from
gitread: a reader of the run loop can see at a glance which calls change something. Every
mutating wrapper takes an `ops` recorder (the state store, or anything with the same
`record_git_op(task_id, op, phase, detail)` method) and writes one `intent` entry before the
call and one `result` entry after, so a crash between them is a named state rather than a
mystery (R55, the plan's System-Wide Impact note).

The tail is the part of the pipeline the task process does not own (KTD5). In local merge mode
the task process exits on `relay/<task-id>`, and the runner then runs the project's gate on that
branch head, merges to the default branch, and pushes. A gate refusal strands the branch instead
of diverging the default branch, which is the whole reason the merge lives here.

Nothing here writes to a tracker (R19), and nothing here decides whether a task landed. That
verdict is verify.py, from git and the tracker alone.
"""
import os
import subprocess
import time
from dataclasses import dataclass, field

from . import contracts, gitread

TASK_BRANCH_PREFIX = "relay/"
MERGE_MESSAGE = "Merge relay task {task_id} from {branch}"

# `gh pr checks` exit codes, observed on the gh CLI: 0 every check passed, 8 checks still
# pending, anything else a failure or an error. Pending is the only code that keeps polling.
GH_CHECKS_PENDING = 8
DEFAULT_CI_POLL_INTERVAL_SECONDS = 60
SIGKILL_GRACE_SECONDS = 15


def _kill_group(proc, grace_seconds):
    """SIGTERM the whole group, then SIGKILL what is left."""
    import os as _os
    import signal as _signal

    try:
        _os.killpg(_os.getpgid(proc.pid), _signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        return False
    try:
        proc.wait(timeout=grace_seconds)
        return True
    except subprocess.TimeoutExpired:
        pass
    try:
        _os.killpg(_os.getpgid(proc.pid), _signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        pass
    try:
        proc.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        pass
    return True


def task_branch_for(task_id):
    return TASK_BRANCH_PREFIX + task_id


def _record(ops, task_id, op, phase, detail=None):
    if ops is not None:
        ops.record_git_op(task_id, op, phase, detail)


def _mutate(repo, op, args, ops=None, task_id=None, env=None, check=False):
    """Run one mutating git command between an intent entry and a result entry."""
    _record(ops, task_id, op, "intent", {"args": list(args)})
    proc = gitread.run(repo, args, check=check, env=env)
    output = (proc.stdout or "") + (proc.stderr or "")
    _record(ops, task_id, op, "result", {"returncode": proc.returncode, "output": output[-2000:]})
    return proc


@dataclass
class PushResult:
    ok: bool
    returncode: int
    output: str


@dataclass
class MergeResult:
    ok: bool
    returncode: int
    output: str
    sha: str | None = None
    conflict: bool = False


@dataclass
class GateResult:
    ok: bool
    returncode: int | None
    log_path: str
    output_tail: str = ""
    timed_out: bool = False


@dataclass
class PreflightResult:
    ok: bool
    failed: str | None
    evidence: dict = field(default_factory=dict)


@dataclass
class ScopeResult:
    ok: bool
    offending: list = field(default_factory=list)
    changed: list = field(default_factory=list)
    halt_class: str | None = None
    reset_to: str | None = None
    # Untracked offenders survive the reset, because a reset moves tracked files and nothing
    # else. Named separately so the halt can tell the operator what is still on disk.
    untracked: list = field(default_factory=list)


@dataclass
class TimeoutDisposition:
    action: str
    tree: str
    branch: str


@dataclass
class CiResult:
    state: str
    elapsed_seconds: float
    polls: int
    output: str = ""
    halt_class: str | None = None


@dataclass
class TailResult:
    ok: bool
    halt_class: str | None = None
    stage: str | None = None
    merge_sha: str | None = None
    gate: GateResult | None = None
    evidence: dict = field(default_factory=dict)


# Mutating wrappers.

def fetch(repo, remote="origin", ops=None, task_id=None, env=None):
    return _mutate(repo, "fetch", ["fetch", "--quiet", remote], ops, task_id, env, check=True)


def checkout(repo, ref, ops=None, task_id=None, env=None):
    return _mutate(repo, "checkout", ["checkout", "--quiet", ref], ops, task_id, env, check=True)


def merge_no_ff(repo, branch, task_id, ops=None, env=None):
    """Merge the task branch into whatever is checked out, with a message naming the task."""
    message = MERGE_MESSAGE.format(task_id=task_id, branch=branch)
    proc = _mutate(repo, "merge", ["merge", "--no-ff", "--no-edit", "-m", message, branch],
                   ops, task_id, env)
    output = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode == 0:
        return MergeResult(True, 0, output, sha=gitread.rev_parse(repo, "HEAD"))
    return MergeResult(False, proc.returncode, output, conflict=gitread.merge_head_exists(repo))


def merge_abort(repo, ops=None, task_id=None, env=None):
    return _mutate(repo, "merge_abort", ["merge", "--abort"], ops, task_id, env)


def abort_dangling_merge(repo, ops=None, task_id=None, env=None):
    """R55: a reclaimed lease may find a merge half done. True when one was aborted."""
    if not gitread.merge_head_exists(repo):
        return False
    merge_abort(repo, ops=ops, task_id=task_id, env=env)
    return True


def push(repo, args, ops=None, task_id=None, env=None):
    proc = _mutate(repo, "push", ["push"] + list(args), ops, task_id, env)
    output = (proc.stdout or "") + (proc.stderr or "")
    return PushResult(proc.returncode == 0, proc.returncode, output)


def mirror_push(repo, mirror, ops=None, task_id=None, env=None):
    """R6: the mirror rule is an argument list the manifest supplies, run after closeout."""
    proc = _mutate(repo, "mirror_push", ["push"] + list(mirror), ops, task_id, env)
    output = (proc.stdout or "") + (proc.stderr or "")
    return PushResult(proc.returncode == 0, proc.returncode, output)


def mirror_target(mirror):
    """The remote and destination branch a mirror argument list pushes to, so verify can read
    the mirror ref back. `["origin", "main:release"]` and `["origin", "release"]` both name
    `origin` and `release`. None when the list is empty or names no destination."""
    if not mirror:
        return None
    positional = [arg for arg in mirror if not arg.startswith("-")]
    if len(positional) < 2:
        return None
    remote = positional[0]
    destination = positional[-1].split(":")[-1]
    if destination.startswith("refs/heads/"):
        destination = destination[len("refs/heads/"):]
    return remote, destination


def delete_branch(repo, name, ops=None, task_id=None, env=None):
    """Delete the local task branch. Only called after a full verify has passed, so the commits
    are already on the remote default branch and `-d` refusing would be a real signal."""
    return _mutate(repo, "delete_branch", ["branch", "-D", name], ops, task_id, env)


def reset_hard(repo, ref, ops=None, task_id=None, env=None):
    return _mutate(repo, "reset_hard", ["reset", "--hard", ref], ops, task_id, env, check=True)


# Pre-flight (R16).

PREFLIGHT_CHECKS = ("tree_clean", "on_default", "head_equals_remote", "no_task_branch")


def preflight(repo, default_branch, task_branch, env=None):
    """R16: a task process starts from a clean tree on the default branch, in sync with the
    remote, with no pre-existing task branch. Returns the name of the first check that failed,
    which is what the summary prints and what the halted record carries."""
    evidence = {}
    porcelain = gitread.status_porcelain(repo)
    evidence["tree"] = porcelain.strip().splitlines()[:10]
    if porcelain.strip():
        return PreflightResult(False, "tree_clean", evidence)
    branch = gitread.current_branch(repo)
    evidence["branch"] = branch
    if branch != default_branch:
        return PreflightResult(False, "on_default", evidence)
    local = gitread.rev_parse(repo, default_branch)
    remote = gitread.rev_parse(repo, "origin/" + default_branch)
    evidence.update(local_sha=local, remote_sha=remote)
    if local is None or remote is None or local != remote:
        return PreflightResult(False, "head_equals_remote", evidence)
    if gitread.branch_exists(repo, task_branch):
        evidence["task_branch"] = task_branch
        return PreflightResult(False, "no_task_branch", evidence)
    return PreflightResult(True, None, evidence)


# The gate and its backstop.

def claude_dir_backstop(repo, baseline_sha, branch):
    """R41's second half: after the task process exits, a branch diff touching `.claude/` is
    refused before the gate. The pre-flight scan catches the intent in the brief; this catches
    what actually landed on the branch."""
    paths = gitread.diff_name_only(repo, baseline_sha, branch)
    return [path for path in paths if contracts.CLAUDE_DIR_PATH_REGEX.search("/" + path)]


def run_gate(repo, command, log_path, timeout_seconds=contracts.DEFAULT_GATE_TIMEOUT_MINUTES * 60,
             env=None):
    """R24: run the manifest's gate argument list in the repo and capture it to the log the
    summary points at. Never a shell string (R9); a nonzero exit strands the branch."""
    os.makedirs(os.path.dirname(log_path) or ".", exist_ok=True)
    try:
        proc = subprocess.Popen(list(command), cwd=repo, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, env=env,
                                stdin=subprocess.DEVNULL, start_new_session=True)
    except OSError as exc:
        text = "gate command could not run: %s" % exc
        with open(log_path, "w", encoding="utf-8") as handle:
            handle.write(text)
        return GateResult(False, None, log_path, text[-2000:])
    try:
        captured = proc.communicate(timeout=timeout_seconds)[0]
    except subprocess.TimeoutExpired:
        # The gate builds, so it spawns compilers and test runners. Killing only the process
        # named in the manifest would leave those running into the next task.
        _kill_group(proc, SIGKILL_GRACE_SECONDS)
        captured = ""
        try:
            captured = proc.communicate(timeout=5)[0] or ""
        except (subprocess.TimeoutExpired, ValueError):
            pass
        text = "gate timed out after %d seconds\n%s" % (timeout_seconds, captured)
        with open(log_path, "w", encoding="utf-8") as handle:
            handle.write(text)
        return GateResult(False, None, log_path, text[-2000:], timed_out=True)
    except OSError as exc:
        text = "gate command could not run: %s" % exc
        with open(log_path, "w", encoding="utf-8") as handle:
            handle.write(text)
        return GateResult(False, None, log_path, text[-2000:])
    text = captured or ""
    with open(log_path, "w", encoding="utf-8") as handle:
        handle.write(text)
    return GateResult(proc.returncode == 0, proc.returncode, log_path, text[-2000:])


# The tail.

def local_merge_tail(repo, task_id, default_branch, baseline_sha, gate_command, gate_log_path,
                     ops=None, env=None, gate_timeout_seconds=None, still_ours=None):
    """The fixed local merge sequence of R50, from the task process's exit to a pushed default
    branch. Stops at the first refusal and names the halt class; every stop leaves the task
    branch in place so the operator can repair by hand and resume.
    """
    branch = task_branch_for(task_id)
    if gate_timeout_seconds is None:
        gate_timeout_seconds = contracts.DEFAULT_GATE_TIMEOUT_MINUTES * 60

    # A process can report success and leave nothing behind. The runner decides from git, so a
    # missing branch is a named refusal here rather than an exception out of the checkout.
    if not gitread.branch_exists(repo, branch):
        return TailResult(False, contracts.HALT_UNCLEAN_EXIT, "branch",
                          evidence={"branch": branch,
                                    "reason": "the task branch does not exist; nothing to merge"})

    checkout(repo, branch, ops=ops, task_id=task_id, env=env)

    hits = claude_dir_backstop(repo, baseline_sha, branch)
    if hits:
        return TailResult(False, contracts.HALT_PATH_GATE, "backstop",
                          evidence={"paths": hits, "branch": branch})

    gate = run_gate(repo, gate_command, gate_log_path, gate_timeout_seconds, env=env)
    if not gate.ok:
        return TailResult(False, contracts.HALT_GATE_REFUSED, "gate", gate=gate,
                          evidence={"branch": branch, "sha": gitread.rev_parse(repo, branch),
                                    "log": gate.log_path, "returncode": gate.returncode})
    if not gitread.is_clean(repo):
        return TailResult(False, contracts.HALT_UNCLEAN_EXIT, "gate", gate=gate,
                          evidence={"branch": branch,
                                    "tree": gitread.status_porcelain(repo).strip().splitlines()[:10]})

    if still_ours is not None and not still_ours():
        return TailResult(False, contracts.HALT_RUNNER_CRASHED, "lease",
                          evidence={"branch": branch, "status_before": contracts.STATUS_MERGING,
                                    "reason": "the lease was lost while the gate ran"})

    fetch(repo, ops=ops, task_id=task_id, env=env)
    remote_sha = gitread.rev_parse(repo, "origin/" + default_branch)
    if remote_sha != baseline_sha:
        return TailResult(False, contracts.HALT_REMOTE_ADVANCED, "fetch", gate=gate,
                          evidence={"remote_sha": remote_sha, "baseline_sha": baseline_sha,
                                    "sha": remote_sha, "branch": branch})

    checkout(repo, default_branch, ops=ops, task_id=task_id, env=env)
    merge = merge_no_ff(repo, branch, task_id, ops=ops, env=env)
    if not merge.ok:
        if merge.conflict:
            merge_abort(repo, ops=ops, task_id=task_id, env=env)
        return TailResult(False, contracts.HALT_REMOTE_ADVANCED, "merge", gate=gate,
                          evidence={"sha": gitread.rev_parse(repo, default_branch),
                                    "conflict": merge.conflict, "merge_output": merge.output,
                                    "branch": branch, "baseline_sha": baseline_sha,
                                    "remote_sha": remote_sha})

    if still_ours is not None and not still_ours():
        return TailResult(False, contracts.HALT_RUNNER_CRASHED, "lease", merge_sha=merge.sha,
                          evidence={"branch": default_branch,
                                    "status_before": contracts.STATUS_MERGING,
                                    "reason": "the lease was lost before the push"})

    pushed = push(repo, ["origin", default_branch], ops=ops, task_id=task_id, env=env)
    if not pushed.ok:
        return TailResult(False, contracts.HALT_GATE_REFUSED, "push", merge_sha=merge.sha, gate=gate,
                          evidence={"branch": default_branch, "sha": merge.sha,
                                    "log": gate.log_path, "push_output": pushed.output})
    return TailResult(True, None, "pushed", merge_sha=merge.sha, gate=gate,
                      evidence={"branch": default_branch, "sha": merge.sha})


def blocked_path(repo, default_branch, branch, ops=None, task_id=None, env=None):
    """R50's blocked route: return to the default branch and leave the task branch stranded,
    recording its name and head so the summary can point the operator at it."""
    head = gitread.rev_parse(repo, branch) if gitread.branch_exists(repo, branch) else None
    if gitread.current_branch(repo) != default_branch:
        checkout(repo, default_branch, ops=ops, task_id=task_id, env=env)
    return {"branch": branch if head else None, "head": head}


def timeout_disposition(repo, default_branch, branch):
    """R35 and R50: after a timeout kill, a clean tree on the task branch or the default branch
    takes the blocked path and the run continues; a dirty tree halts."""
    tree = "clean" if gitread.is_clean(repo) else "dirty"
    current = gitread.current_branch(repo)
    if tree == "clean" and current in (branch, default_branch):
        return TimeoutDisposition("blocked", tree, current)
    return TimeoutDisposition("halt", tree, current)


def path_allowed(path, allowed_paths):
    """An entry ending in `/` is a directory prefix; anything else is an exact file path."""
    for entry in allowed_paths:
        if entry.endswith("/"):
            if path == entry.rstrip("/") or path.startswith(entry):
                return True
        elif path == entry:
            return True
    return False


def closeout_scope_check(repo, pre_closeout_head, allowed_paths, ops=None, task_id=None, env=None):
    """R53 and KTD15: the closeout commits docs and never pushes, so the runner checks what it
    produced before its own push. A path outside the allowed set resets the branch to the
    pre-closeout head, which is why this runs before the push and not after it.

    The working tree counts, not only the commits. A closeout that edited a file and left it
    uncommitted, or dropped an untracked file, changed the repository just as much as one that
    committed; reading the commit diff alone let both through, and the next task's pre flight
    then refused on a tree this check had already called clean.
    """
    committed = gitread.diff_name_only(repo, pre_closeout_head, "HEAD")
    working, untracked = gitread.status_paths(repo)
    changed = committed + [path for path in working if path not in committed]
    offending = [path for path in changed if not path_allowed(path, allowed_paths)]
    if offending:
        reset_hard(repo, pre_closeout_head, ops=ops, task_id=task_id, env=env)
        return ScopeResult(False, offending, changed, contracts.HALT_CLOSEOUT_OUT_OF_SCOPE,
                           reset_to=pre_closeout_head,
                           untracked=[path for path in untracked if path in offending])
    if working:
        # In scope but uncommitted, which is a different failure and gets the class that names
        # it. Nothing here would have been pushed, and leaving it in place refuses the next
        # task at pre flight, so the tree goes back to where the closeout found it.
        reset_hard(repo, pre_closeout_head, ops=ops, task_id=task_id, env=env)
        return ScopeResult(False, [], changed, contracts.HALT_UNCLEAN_EXIT,
                           reset_to=pre_closeout_head, untracked=untracked)
    return ScopeResult(True, [], changed)


# PR terminal mode (R12). Both helpers take the adapter's injectable run callable, so no test
# needs `gh` installed.

def find_pr(run, branch, timeout=30):
    """The open pull request for the task branch, or None."""
    import json

    proc = run(["gh", "pr", "list", "--head", branch, "--json", "url,number"], timeout=timeout)
    if proc.returncode != 0:
        return None
    try:
        entries = json.loads(proc.stdout or "[]")
    except ValueError:
        return None
    return entries[0] if entries else None


def poll_ci(run, branch, bound_seconds, interval_seconds=DEFAULT_CI_POLL_INTERVAL_SECONDS,
            sleep=time.sleep, monotonic=time.monotonic, timeout=30):
    """R12: poll `gh pr checks` until it decides or the bound expires. The clock is monotonic,
    which excludes host sleep (KTD10), and both the clock and the runner are injectable so the
    undecided case is a fast test rather than a real wait."""
    started = monotonic()
    deadline = started + bound_seconds
    polls = 0
    output = ""
    while True:
        proc = run(["gh", "pr", "checks", branch], timeout=timeout)
        polls += 1
        output = (proc.stdout or "") + (proc.stderr or "")
        if proc.returncode == 0:
            return CiResult("pass", monotonic() - started, polls, output)
        if proc.returncode != GH_CHECKS_PENDING:
            return CiResult("fail", monotonic() - started, polls, output)
        if monotonic() >= deadline:
            return CiResult("undecided", monotonic() - started, polls, output,
                            contracts.HALT_CI_UNDECIDED)
        sleep(interval_seconds)
