"""The run loop (U10): one manifest, one task at a time, in the fixed sequence of R50.

Everything else in Relay is a piece; this is where they are ordered, and the order is the
product. The sequence after a task process exits is not negotiable and not conditional on what
that process said about itself: classify the exit from the transcript, gate the branch head,
merge, push, verify the code scope, run the closeout, check what it committed, push that, mirror,
verify the full scope, and only then delete the branch and move on. A task is landed when the
runner's own verify says so and never before (R20).

Three properties of the loop are worth naming because they are easy to lose in a refactor.

Nothing crosses between tasks except the manifest, git, and the tracker (R15). No transcript, no
summary, and no memory of a prior task reaches a later brief, and there is no variable in this
module that carries one.

Every stop is a named class with evidence, not an exception (R25, R44). The operator repairs by
hand and re-runs; the loop resumes at the first record that did not land (R32) and re-verifies
the ones that halted (R48), so a repair made between runs is picked up rather than redone.

The runner never writes to the tracker (R19). Every tracker write in this file happens inside a
closeout process the runner launched; the runner reads the result back and decides from it.
"""
import time
from dataclasses import dataclass, field, replace

from . import (adapters, backends, brief, classify, closeout, contracts, gitread, gitwrite,
               launch, manifest as manifest_module, state, summary, verify)

EXIT_OK = 0
EXIT_CONFIG = 1
EXIT_HALTED = 2
EXIT_LEASE = 3


@dataclass
class _Run:
    """The values that are fixed for a whole run. Gathered once so a per task function takes a
    task rather than a parameter list, and so the tail's context is built by expanding this."""
    manifest: object
    adapter: object
    store: object
    repo: str
    default: str
    env: dict
    base_env: dict
    home: str
    stream: object
    retry_blocked: bool
    overrides: dict
    launch_kwargs: dict
    now: object
    allowed_paths: tuple
    used_backends: set = field(default_factory=set)


@dataclass
class RunOutcome:
    exit_code: int
    halt_task: str | None = None
    halt_class: str | None = None
    message: str | None = None
    store: object = None
    records: dict = field(default_factory=dict)


class _Halt(Exception):
    """A named stop. Carries the task, the class, and the line the summary prints."""

    def __init__(self, task_id, halt_class, message, evidence=None):
        super().__init__(message)
        self.task_id = task_id
        self.halt_class = halt_class
        self.message = message
        self.evidence = evidence or {}


def _baseline_comment_id(adapter, task_id):
    """R17: the newest comment id, which is what `comments_since` measures from. The markdown
    adapter numbers its comments from one, so its newest id is also its count and the same rule
    works for all three adapters."""
    try:
        comments = adapter.comments_since(task_id, None)
    except Exception:
        return None
    return comments[-1]["id"] if comments else None


def _routable(manifest, adapter, digest, repo, branch, baseline_sha):
    """KTD6's second route: a missing envelope is still routable when git and the tracker carry
    a stronger completion signal than the last paragraph of a long context, which is commits on
    the task branch plus the card sitting in the manifest's in review status. A missing envelope
    with an unmoved card is stranded, never merged."""
    has_commits = (gitread.branch_exists(repo, branch)
                   and bool(gitread.log_oneline(repo, baseline_sha, branch)))
    if digest.get("routable"):
        # A complete envelope is still only a claim (R20). With nothing on the branch there is
        # nothing to merge, and the runner says so rather than trusting the claim.
        return has_commits, (None if has_commits else
                             "the envelope read complete but the branch carries no commits")
    if digest.get("findings_unavailable"):
        # R20, KTD5. The route reads the card and the branch as a stronger signal than a silent
        # process. Evidence the runner could not read is not a silent process, it is a runner
        # fault, and classifying it as one is not enough on its own: the check has to be here
        # too, or a card someone moved by hand merges work nobody ever observed.
        return False, "the evidence could not be read, so the no envelope route does not apply"
    if digest.get("halt_class") != contracts.HALT_NO_ENVELOPE:
        return False, None
    if not has_commits:
        return False, "no envelope and no commits on the branch"
    wanted = manifest.tracker.in_review_status
    try:
        card = adapter.status(digest.get("task_id") or "") or {}
    except Exception:
        card = {}
    if wanted and str(card.get("status") or "").lower() == str(wanted).lower():
        return True, "no envelope, routed on commits plus the card in %s" % wanted
    return False, "no envelope and the card did not move"


def run(manifest, adapter=None, store=None, home=None, base_env=None, stream=print,
        retry_blocked=False, timeout_overrides=None, launch_kwargs=None, now=time.time):
    """Drive one manifest to completion or to a named halt. Returns a RunOutcome; never raises
    for a task level failure, because every one of those is a class an operator can act on."""
    repo = manifest.project.repo
    default = verify.default_branch_of(manifest)
    overrides = timeout_overrides or {}
    launch_kwargs = dict(launch_kwargs or {})
    env = launch.child_env(manifest, base_env, home)

    try:
        adapter = adapter or adapters.build(manifest, env=base_env)
    except adapters.ConfigurationError as exc:
        return RunOutcome(EXIT_CONFIG, message=str(exc))
    store = store or state.StateStore(manifest.path, repo, home=home)

    acquired = store.acquire()
    if acquired.code == state.LOCKED:
        holder = acquired.holder or {}
        where = acquired.other_manifest or holder.get("manifest")
        return RunOutcome(EXIT_LEASE, message=(
            "another runner holds the lease: pid %s on %s, manifest %s, heartbeat %.0f seconds old"
            % (holder.get("holder_pid"), holder.get("hostname"), where, acquired.age_seconds or 0)))

    if stream is not None and acquired.code == state.STALE_RECLAIMED:
        stream("reclaimed a stale lease from pid %s; %d record(s) marked %s"
               % ((acquired.previous_holder or {}).get("holder_pid"),
                  len(acquired.reclaimed_ids), contracts.HALT_RUNNER_CRASHED))

    # Resolved once, here, from the repo as it stands before any task has touched it. Reading
    # it per closeout would let a task's own merge move the bound its closeout is checked
    # against (R53, KTD15).
    allowed_paths = tuple(manifest_module.completed_allowed_paths(
        manifest, manifest_module.docs_root_for(repo)))
    config = _Run(manifest, adapter, store, repo, default, env, base_env, home, stream,
                  retry_blocked, overrides, launch_kwargs, now, allowed_paths)
    outcome = RunOutcome(EXIT_OK, store=store)
    wrote_terminal = False
    try:
        store.validate()
        verify.startup_reverify(manifest, store, adapter, env=env, now=now)
        for index, task in enumerate(manifest.tasks):
            try:
                _one_task(config, task)
            except _Halt as exc:
                # Rebound deliberately: Python unbinds the `as` name at the end of an except
                # block, so the handler below could not see it.
                halt = exc
            except gitread.GitError as exc:
                halt = _Halt(task.id, contracts.HALT_UNCLEAN_EXIT,
                             "a git command failed while handling %s: %s" % (task.id, exc),
                             {"task": task.id,
                              "branch": gitwrite.task_branch_for(task.id, config.manifest.project.branch_prefix),
                              **_git_error_fields(exc)})
            except Exception as exc:
                # A defect or an unanticipated library error. It still stops the way every
                # other stop does, because an operator cannot act on a traceback.
                halt = _Halt(task.id, contracts.HALT_UNEXPECTED_ERROR,
                             "the runner hit an unexpected %s on %s: %s"
                             % (type(exc).__name__, task.id, exc),
                             {"task": task.id, "error_type": type(exc).__name__,
                              "error": str(exc)[:500]})
            else:
                store.set_cursor(index + 1)
                continue
            # Issue #15: a halt contained to one task need not stop the rest. Decided from
            # the repo, not the class, because the same class covers a failed gate command
            # (default untouched) and a failed push after the merge (default ahead of origin).
            continued = _continue_past(config, halt)
            # The message is the raiser's own sentence, kept beside the class's template
            # line. First live run: a retry refused under R48 halted as unclean_exit and the
            # summary said "left the tree dirty" about a clean tree, because the sentence that
            # explained the refusal was printed to stdout and never written down.
            # Fill the backend only when the record has none (a halt before anything
            # launched), and never overwrite one: the record wins rule in _one_task may have
            # swapped the running task onto the record's backend, and this outer handler holds
            # the manifest's un swapped task.
            recorded = (store.get(halt.task_id) or {}).get("backend")
            store.upsert(halt.task_id, status=contracts.STATUS_HALTED,
                         halt_class=halt.halt_class, halt_evidence=halt.evidence,
                         halt_message=halt.message, continued_past=continued,
                         backend=recorded or task.backend)
            if continued:
                if stream is not None:
                    stream("%s halted with class %s; continuing past it"
                           % (halt.task_id, halt.halt_class))
                store.set_cursor(index + 1)
                continue
            _write_terminal(store, env, contracts.RUN_HALTED, halt.task_id, halt.halt_class,
                            config.used_backends)
            wrote_terminal = True
            return RunOutcome(EXIT_HALTED, halt.task_id, halt.halt_class, halt.message,
                              store, store.records())
        _write_terminal(store, env, contracts.RUN_COMPLETED, used_backends=config.used_backends)
        wrote_terminal = True
        outcome.records = store.records()
        return outcome
    finally:
        # The lease is released on the way out either way, and status_word reads a crash from a
        # surviving lease. So a run that reached neither terminal write has to say so itself,
        # or `relay status` reports the previous run's outcome as if it were this one.
        if not wrote_terminal:
            try:
                _write_terminal(store, env, contracts.RUN_CRASHED,
                                used_backends=config.used_backends)
            except Exception:
                pass
        store.release()


def _git_error_fields(exc):
    """The evidence a GitError contributes wherever one is recorded."""
    return {"args": exc.args_list, "returncode": exc.returncode,
            "stderr": (exc.stderr or "")[-2000:]}


def _write_terminal(store, env, run_status, halt_task=None, halt_class=None, used_backends=()):
    """Write terminal version evidence for only the CLIs this invocation actually launched."""
    used = sorted(used_backends)
    pinned = {name: backends.build(name).CAPABILITY.version_tested for name in used}
    observed = {name: launch.cli_version(env, backend=name) for name in used}
    return store.write_terminal(run_status, halt_task, halt_class, pinned, observed)


def _continue_past(cfg, halt):
    """Whether the run goes on past this halt (issue #15). True only when the manifest opted
    in, the class is not run scoped, and the repo, returned to the default branch, is one the
    next task's pre flight would accept. A refusal is recorded on the halt's evidence under
    `resume` so the record says why the run stopped rather than continuing.

    A failure inside the disposition is itself a stop: the evidence names it beside the
    original halt, and the class stays the original's, because that is what the operator has
    to repair first. Any exception, not only GitError: this runs outside the per task handler
    that turns the unexpected into a named class, and a hung checkout raising TimeoutExpired
    here would otherwise escape the loop as a traceback.

    Two refusals never reach the disposition. A `no_task_branch` pre-flight refusal is this
    same task's own branch from an earlier continued-past halt still in the way; nothing here
    deletes it (Scope Boundaries), so allowing continuation would repeat the identical refusal
    on every later run while the record keeps reading `continued_past` and the run keeps
    reading `completed` -- a review finding on the first draft of this function caught it
    passing on that exact case. And a lease already lost means the checkout below would mutate
    a repo another runner may hold, the one mutation in this path with no heartbeat guard the
    way `_merge_route`'s tail already has one."""
    if not cfg.manifest.on_halt.continue_past_task_halt:
        return False
    if halt.halt_class in contracts.RUN_SCOPED_HALT_CLASSES:
        return False
    if halt.evidence.get("check") == "no_task_branch":
        halt.evidence["resume"] = {"check": "no_task_branch"}
        return False
    if not cfg.store.heartbeat():
        halt.evidence["resume"] = {"check": "lease_lost"}
        return False
    try:
        result = gitwrite.resume_disposition(cfg.repo, cfg.default, ops=cfg.store,
                                             task_id=halt.task_id, env=cfg.env)
    except gitread.GitError as exc:
        halt.evidence["resume"] = {"check": "git_error", **_git_error_fields(exc)}
        return False
    except Exception as exc:
        halt.evidence["resume"] = {"check": "unexpected_error",
                                   "error_type": type(exc).__name__, "error": str(exc)[:500]}
        return False
    if result.ok:
        return True
    halt.evidence["resume"] = dict(result.evidence, check=result.failed)
    return False


# Round eight #54, from relay proof T-65. The Brief forbids these operations "however this CLI
# spells it", and the audit after exit matches command spellings. T-65 ran a disallowed operation
# by a spelling the pattern does not match, and the record said only that the operations went
# unenforced, next to an empty findings list. Read together those two read as a clean run.
#
# Every clause below is load bearing, and a code review earned each one.
#
# Present tense, because the scalar is written off `enforces_at_launch` alone, before the launch
# error and timeout branches. A past tense sentence would claim an audit ran for a Task whose
# binary was missing or that was killed at the timeout with no readable evidence.
#
# "the Task process's own log recorded", because the audit walks only completed calls the log
# decoded. The Closeout is a second contributor to the same findings list and `closeout.run`
# passes no disallow patterns at all, so the sentence names the process it actually describes.
#
# "a restriction naming a tool other than a command", because `classify` reads `input.command`
# and skips a tool_use without one. A manifest may disallow an `Edit(...)` or `Write(...)`
# pattern, and for that entry the honest word is unaudited rather than bounded by spelling.
#
# The destructive clause, because `_destructive_finding` filters the findings
# `classify.matches_disallow_pattern` produced. The refusal an operator trusts most rests on the
# same match this sentence has just called evadable, so it cannot be left implied.
#
# Positive form, because "no finding is not proof" inverts on a fast read, and defeating a fast
# misread is the whole job.
UNENFORCED_BOUND = (
    ". The Brief carries these to the Task process as instructions naming operations, and the "
    "audit after exit matches command spellings against the commands that process's own log "
    "recorded. An absent finding therefore does not prove the operation was avoided. Another "
    "spelling, a restriction naming a tool other than a command, and a call the log never "
    "recorded all reach the same empty result, and the refusal of a destructive landing rests "
    "on that same match."
)


def _unenforced_scalar(manifest, capability):
    """One plain string naming the unenforced disallow patterns, the bound on what the audit that
    follows them can prove, and any sandbox network grant the backend launches with.

    The network clause is written off the capability's own `config_overrides`, not off
    `enforces_at_launch`, so a backend that enforces nothing and reaches no network never
    inherits a sentence that is false for it. It belongs on the record and not only in
    `SKILL.md`, because the skill speaks when a manifest is authored: an operator running a
    manifest written before the grant existed would otherwise never be told, and `validate` only
    checks that `permissions.unenforced_acceptance` is non empty (issue #51).

    Single line, and the newline ban is not a style preference: `summary.line_fields` hoists
    every non-container record field into the namespace `cause_line` formats halt templates
    against, so this value has to stay Cause-line-safe even though no template names it today.
    """
    inners = []
    for pattern in manifest_module.resolved_disallowed(manifest):
        inner = contracts.disallow_inner(pattern)
        if inner not in inners:
            inners.append(inner)
    scalar = "disallowed tools not enforced at launch: " + ", ".join(inners) + UNENFORCED_BOUND
    if any("network" in token for token in capability.config_overrides):
        scalar += (" This Task also launches with its sandbox network turned on, so it reaches "
                   "every host and not only the tracker, because the sandbox takes no host "
                   "allowlist.")
    return scalar


def _destructive_finding(findings):
    for finding in findings:
        if (finding.get("class") == contracts.UNENFORCED_DISALLOWED
                and finding.get("pattern") in contracts.DESTRUCTIVE_TOOLS):
            return finding
    return None


def _one_task(cfg, task):
    manifest, adapter, store = cfg.manifest, cfg.adapter, cfg.store
    repo, default, env = cfg.repo, cfg.default, cfg.env
    stream = cfg.stream
    record = store.get(task.id) or state.new_record(task.id)
    if record.get("backend") and record["backend"] != task.backend:
        task = replace(task, backend=record["backend"])
    status = record.get("status")

    if task.excluded:
        store.upsert(task.id, status=contracts.STATUS_EXCLUDED, excluded_reason=task.reason)
        return
    if status == contracts.STATUS_LANDED:
        return
    if status == contracts.STATUS_EXCLUDED and record.get("excluded_reason"):
        return
    branch = gitwrite.task_branch_for(task.id, cfg.manifest.project.branch_prefix)
    if status == contracts.STATUS_BLOCKED:
        if not cfg.retry_blocked:
            return
        # Prefer the name recorded when the task blocked. A later prefix edit must not hide
        # a stranded branch that still carries commits.
        _clear_blocked_branch(store, task, repo, record, env, record.get("branch") or branch)

    # Pre-flight (R16). A failure here is a halt: the repo is not in the state a task process
    # can start from, and no launch may happen until the operator has looked.
    preflight = gitwrite.preflight(repo, default, branch, env=env)
    if not preflight.ok:
        raise _Halt(task.id, contracts.HALT_UNCLEAN_EXIT,
                    "pre flight refused before launching %s on check %s"
                    % (task.id, preflight.failed),
                    {"branch": branch, "check": preflight.failed,
                     "evidence": preflight.evidence})

    # Baseline (R17): what the runner will compare against when it decides landing.
    card = adapter.read(task.id)
    if card.get("skipped"):
        reason = "the tracker card could not be read: %s" % card["skipped"]
        store.upsert(task.id, status=contracts.STATUS_EXCLUDED, excluded_reason=reason)
        if stream is not None:
            stream("%s skipped: %s" % (task.id, reason))
        return
    baseline_sha = gitread.rev_parse(repo, default)
    card_status = adapter.status(task.id)
    if card_status.get("terminal"):
        # Startup re-verify runs before this and promotes a task that landed by hand. A card
        # that is terminal and was not promoted was closed elsewhere, and a task process given
        # it has nothing to do. The first Cratekit run relaunched a closed issue this way.
        reason = ("the card already reads %s, which is terminal; nothing to run"
                  % card_status.get("status"))
        store.upsert(task.id, status=contracts.STATUS_EXCLUDED, excluded_reason=reason)
        if stream is not None:
            stream("%s skipped: %s" % (task.id, reason))
        return
    baseline_comment_id = _baseline_comment_id(adapter, task.id)

    # Brief and the pre-flight scan (R7, R41, R43).
    brief_text = brief.render(manifest, task, card)
    hits = brief.scan(card, brief_text)
    if hits:
        store.upsert(task.id, status=contracts.STATUS_EXCLUDED,
                     excluded_reason=brief.exclusion_reason(hits))
        if stream is not None:
            stream("%s skipped: %s" % (task.id, brief.exclusion_reason(hits)))
        return
    brief_path, brief_sha = brief.write(store, task.id, brief_text)

    store.upsert(task.id, status=contracts.STATUS_RUNNING, baseline_sha=baseline_sha,
                 baseline_tracker_status=card_status.get("status"),
                 baseline_comment_id=baseline_comment_id, branch=branch,
                 brief_sha256=brief_sha, started_at=None, halt_class=None, findings=[],
                 continued_past=False, backend=task.backend)

    launched = launch.launch(
        manifest, task, brief_text, store.path("logs", task.id + ".stdout.log"),
        cfg.overrides.get("task_seconds") or manifest.timeouts.task_minutes * 60,
        home=cfg.home, base_env=cfg.base_env, stream=stream,
        heartbeat=store.heartbeat, on_release=store.release, **cfg.launch_kwargs)
    if not launched.launch_error:
        cfg.used_backends.add(task.backend)

    capability = backends.build(task.backend).CAPABILITY
    disallow = (manifest_module.resolved_disallowed(manifest)
                if not capability.enforces_at_launch else None)
    digest = classify.classify(launched.transcript_path, launched,
                               adapter.write_tool_patterns(), backend=task.backend,
                               disallow_patterns=disallow)
    digest["task_id"] = task.id
    raw_findings = digest.get("findings")
    findings = list(raw_findings or [])
    if raw_findings is not None:
        digest["findings"] = findings
    classify.write_digest(digest, store.path("digests", task.id + ".json"))
    extra = {}
    if not capability.enforces_at_launch:
        extra["unenforced_restrictions"] = _unenforced_scalar(manifest, capability)
    store.upsert(task.id, session_id=launched.session_id,
                 transcript_path=launched.transcript_path, wall_seconds=launched.wall_seconds,
                 active_seconds=launched.active_seconds, findings=findings,
                 binary_path=launched.binary_path, args=launched.args, **extra)

    context = _Context(task=task, card=card, branch=branch, baseline_sha=baseline_sha,
                       baseline_comment_id=baseline_comment_id, digest=digest,
                       launched=launched, findings=findings, **vars(cfg))

    # From here on, every raise is a halt on a task whose process has already launched and whose
    # brief already told it to move the card (R1). The wrap makes that halt visible on the card
    # too (R4): _note_halt runs once, right where the halt is classified, and never changes what
    # gets raised (KTD4).
    try:
        if launched.launch_error:
            raise _Halt(task.id, contracts.HALT_UNEXPECTED_ERROR,
                        "%s could not be launched: %s" % (task.id, launched.launch_error),
                        {"task": task.id, "error": launched.launch_error,
                         "error_type": "launch failure"})

        if launched.lease_lost:
            raise _Halt(task.id, contracts.HALT_RUNNER_CRASHED,
                        "the lease was lost while %s was running; another runner may hold it"
                        % task.id,
                        {"status_before": contracts.STATUS_RUNNING, "branch": branch})

        destructive = _destructive_finding(findings)
        if destructive is not None:
            line = summary.cause_line(contracts.UNENFORCED_DISALLOWED, destructive)
            raise _Halt(task.id, contracts.HALT_UNEXPECTED_ERROR, line,
                        {"task": task.id, "error_type": "destructive_call", "error": line})

        if not capability.enforces_at_launch and digest.get("findings_unavailable"):
            raise _Halt(task.id, contracts.HALT_UNEXPECTED_ERROR,
                        "%s evidence could not be read; unenforced restrictions were not audited"
                        % task.id,
                        {"task": task.id, "error_type": "findings_unavailable",
                         "error": "unenforced restrictions were not audited"})

        if digest.get("halt_class") == contracts.HALT_TIMEOUT:
            return _timeout_route(context)

        routable, note = _routable(manifest, adapter, digest, repo, branch, baseline_sha)
        if note and stream is not None:
            stream("%s: %s" % (task.id, note))
        if routable:
            return _merge_route(context)
        if digest.get("routable"):
            # The process claimed complete and produced nothing the runner can merge. That is not
            # a blocked task, which leaves the repo as it found it deliberately; it is an exit the
            # runner cannot act on, so it halts for a human.
            raise _Halt(task.id, contracts.HALT_UNCLEAN_EXIT,
                        "%s reported status complete but left no commits on %s" % (task.id, branch),
                        {"branch": branch, "baseline_sha": baseline_sha})
        return _blocked_route(context, digest.get("halt_class") or contracts.HALT_BLOCKED_ENVELOPE)
    except _Halt as halt:
        _note_halt(context, halt)
        raise


@dataclass
class _Context(_Run):
    """One task's tail: the run wide values plus what this task produced, so each route below
    reads as the sequence R50 names rather than as parameter threading."""
    task: object = None
    card: dict = None
    branch: str = None
    baseline_sha: str = None
    baseline_comment_id: object = None
    digest: dict = None
    launched: object = None
    findings: list = None


def _clear_blocked_branch(store, task, repo, record, env, branch):
    """R48: `--retry-blocked` may delete a stranded branch only when it carries nothing past the
    baseline. Work that exists only on that branch is the operator's to keep or discard."""
    if not gitread.branch_exists(repo, branch):
        return
    baseline = record.get("baseline_sha")
    if baseline and gitread.log_oneline(repo, baseline, branch):
        raise _Halt(task.id, contracts.HALT_UNCLEAN_EXIT,
                    "retry refused: %s carries commits past the baseline; keep or discard them "
                    "by hand first" % branch,
                    {"branch": branch, "baseline_sha": baseline})
    gitwrite.delete_branch(repo, branch, ops=store, task_id=task.id, env=env)


def _timeout_route(ctx):
    """R35 and R50. A clean tree takes the blocked path with a digest naming the timeout, so the
    run continues past a task that ran long. A dirty tree halts, because nobody can tell from
    here whether the half written state is safe to build on."""
    disposition = gitwrite.timeout_disposition(ctx.repo, ctx.default, ctx.branch)
    # Both units on purpose. The seconds are the measurement and the minutes are what the
    # cause line names; deriving the minutes at render time would put a unit conversion in the
    # summary, which is the one place that must not compute anything.
    ctx.digest["timeout"] = {
        "tree": disposition.tree, "branch": disposition.branch,
        "active_seconds": ctx.launched.active_seconds, "wall_seconds": ctx.launched.wall_seconds,
        "active_minutes": round((ctx.launched.active_seconds or 0) / 60.0),
        "wall_minutes": round((ctx.launched.wall_seconds or 0) / 60.0),
    }
    if disposition.action == "halt":
        verdict = verify.verify(ctx.manifest, ctx.store.get(ctx.task.id), ctx.adapter,
                                scope=verify.SCOPE_CODE, env=ctx.env, now=ctx.now)
        ctx.store.upsert(ctx.task.id, verify=verdict.as_dict())
        raise _Halt(ctx.task.id, contracts.HALT_TIMEOUT,
                    "%s timed out after %.0f active seconds and left the tree dirty on %s"
                    % (ctx.task.id, ctx.launched.active_seconds, disposition.branch),
                    ctx.digest["timeout"])
    return _blocked_route(ctx, contracts.HALT_TIMEOUT)


def _merge_route(ctx):
    """R50, local merge, routable to merge. Every step before the closeout is the runner's own,
    and every one of them can refuse."""
    if ctx.manifest.shipping_mode in manifest_module.UNIMPLEMENTED_SHIPPING_MODES:
        # Unreachable through the CLI, which validates first and refuses the mode there. Kept as
        # a backstop for a caller that builds a manifest by hand, and deliberately not
        # ci_undecided: that class tells an operator to wait for CI, and there is no pull
        # request being checked.
        raise _Halt(ctx.task.id, contracts.HALT_UNEXPECTED_ERROR,
                    "shipping.mode %s is not implemented" % ctx.manifest.shipping_mode,
                    {"task": ctx.task.id, "error_type": "unimplemented shipping mode",
                     "error": "%s has no sequence in the run loop; relay validate refuses it"
                              % ctx.manifest.shipping_mode})

    if not backends.build(ctx.task.backend).CAPABILITY.enforces_at_launch:
        allowed = manifest_module.task_allowed_paths(ctx.manifest)
        if allowed is not None:
            offenders = gitwrite.task_scope_offenders(
                ctx.repo, ctx.baseline_sha, ctx.branch, allowed)
            if offenders:
                detail = "commit on %s touched %s outside the Task path bound" % (
                    ctx.branch, ", ".join(offenders))
                evidence = {"detail": detail, "branch": ctx.branch,
                            "paths": ", ".join(offenders)}
                raise _Halt(ctx.task.id, contracts.HALT_PATH_GATE, detail, evidence)

    ctx.store.upsert(ctx.task.id, status=contracts.STATUS_MERGING)
    # The gate is the longest thing the runner does without a child process to heartbeat for
    # it, so the tail carries its own heartbeat and refuses to merge or push once the lease is
    # no longer ours (R31, R47).
    beat = launch._Heartbeat(ctx.store.heartbeat,
                             ctx.launch_kwargs.get("heartbeat_interval",
                                                   contracts.LEASE_HEARTBEAT_SECONDS))
    beat.start()
    try:
        tail = gitwrite.local_merge_tail(
            ctx.repo, ctx.task.id, ctx.default, ctx.baseline_sha, list(ctx.manifest.gate.command),
            ctx.store.path("gate", ctx.task.id + ".log"), ops=ctx.store, env=ctx.env,
            gate_timeout_seconds=ctx.overrides.get("gate_seconds"),
            still_ours=lambda: not beat.lost,
            branch=ctx.branch)
    finally:
        beat.stop()
    if not tail.ok:
        ctx.store.upsert(ctx.task.id, halt_evidence=tail.evidence)
        raise _Halt(ctx.task.id, tail.halt_class,
                    summary.cause_line(tail.halt_class, tail.evidence),
                    tail.evidence)

    ctx.store.upsert(ctx.task.id, landing_ref=tail.merge_sha)
    verdict = verify.verify(ctx.manifest, ctx.store.get(ctx.task.id), ctx.adapter,
                            scope=verify.SCOPE_CODE, env=ctx.env, now=ctx.now)
    ctx.store.upsert(ctx.task.id, verify=verdict.as_dict())
    if verdict.failed():
        raise _Halt(ctx.task.id, contracts.HALT_GATE_REFUSED,
                    "the code scope verify failed for %s on %s"
                    % (ctx.task.id, ", ".join(verdict.failed())),
                    {"branch": ctx.default, "sha": tail.merge_sha,
                     "log": ctx.store.path("gate", ctx.task.id + ".log"),
                     "checks": verdict.checks})

    gate_summary = {"ok": True, "returncode": 0,
                    "log": ctx.store.path("gate", ctx.task.id + ".log")}
    _run_closeout(ctx, closeout.OUTCOME_LANDED, landing_ref=tail.merge_sha,
                  commit_range="%s..%s" % (ctx.baseline_sha[:7], (tail.merge_sha or "")[:7]),
                  gate=gate_summary)

    if ctx.manifest.project.mirror:
        pushed = gitwrite.mirror_push(ctx.repo, list(ctx.manifest.project.mirror), ops=ctx.store,
                                      task_id=ctx.task.id, env=ctx.env,
                                      timeout=ctx.overrides.get("gate_seconds"))
        if not pushed.ok:
            raise _Halt(ctx.task.id, contracts.HALT_GATE_REFUSED,
                        "the mirror push was refused for %s" % ctx.task.id,
                        {"branch": ctx.default, "sha": tail.merge_sha,
                         "log": ctx.store.path("gate", ctx.task.id + ".log"),
                         "push_output": pushed.output})

    final = verify.verify(ctx.manifest, ctx.store.get(ctx.task.id), ctx.adapter,
                          scope=verify.SCOPE_FULL, do_fetch=True, env=ctx.env, now=ctx.now)
    ctx.store.upsert(ctx.task.id, verify=final.as_dict())
    if not final.landed:
        raise _Halt(ctx.task.id, final.halt_class or contracts.HALT_PARTIAL_LANDING,
                    "%s did not verify as landed: %s"
                    % (ctx.task.id, ", ".join(final.failed() + final.blocking_skips())),
                    {"sha": tail.merge_sha, "card_status": verify.card_status_of(final),
                     "branch": ctx.default, "checks": final.checks})

    if gitread.branch_exists(ctx.repo, ctx.branch):
        gitwrite.delete_branch(ctx.repo, ctx.branch, ops=ctx.store, task_id=ctx.task.id,
                               env=ctx.env)
    ctx.store.upsert(ctx.task.id, status=contracts.STATUS_LANDED,
                     halt_class=contracts.HALT_LANDED, branch=None)


def _blocked_route(ctx, halt_class):
    """R50, blocked. The branch is stranded rather than merged, the closeout comments the card,
    and the run continues. A blocked task is a normal outcome (R23)."""
    stranded = gitwrite.blocked_path(ctx.repo, ctx.default, ctx.branch, ops=ctx.store,
                                     task_id=ctx.task.id)
    _run_closeout(ctx, closeout.OUTCOME_BLOCKED, branch=stranded["branch"])

    finding = closeout.confirm_blocked_comment(ctx.adapter, ctx.task.id, ctx.baseline_comment_id)
    if finding:
        ctx.findings.append(finding)
    # The class arrives from the digest, so the evidence has to cover every class that can
    # reach here: blocked_envelope wants the blocker, no_envelope the last message, timeout the
    # tree and the minutes. Recording only the stranded head left each of them a placeholder.
    envelope = ctx.digest.get("envelope") or {}
    blockers = envelope.get("blockers") or []
    evidence = {
        "stranded_head": stranded["head"],
        "branch": stranded["branch"] or ctx.branch,
        "blocker": blockers[0] if blockers else "no blocker text in the envelope",
        "last_message": ctx.digest.get("last_message") or "(no final message)",
    }
    if ctx.digest.get("findings_unavailable"):
        # unexpected_error reaches here now that unreadable evidence is a runner fault (KTD5),
        # and its line asks for fields no transcript could have supplied.
        evidence.update({
            "task": ctx.task.id,
            "error_type": "unreadable evidence",
            "error": "the transcript at %s could not be read"
                     % (ctx.digest.get("transcript_path") or "an unknown path"),
        })
    evidence.update(ctx.digest.get("timeout") or {})
    ctx.store.upsert(ctx.task.id, status=contracts.STATUS_BLOCKED, halt_class=halt_class,
                     branch=stranded["branch"], findings=ctx.findings,
                     halt_evidence=evidence)


def _run_closeout(ctx, outcome, landing_ref=None, branch=None, commit_range=None, gate=None,
                  halt_class=None, cause_line=None):
    """Launch the closeout, then bound what it committed before anything is pushed (R53).

    The order matters: the check runs against the local head before the push, so a commit
    outside the allowed paths is reset rather than reported after the fact (KTD15).

    `halt_class`/`cause_line` are set only for `closeout.OUTCOME_HALTED`, from `_note_halt`.
    """
    allowed_paths = list(ctx.allowed_paths)
    pre_closeout_head = gitread.rev_parse(ctx.repo, "HEAD")
    try:
        comments = ctx.adapter.comments_since(ctx.task.id, ctx.baseline_comment_id)
    except Exception:
        comments = []
    envelope = ctx.digest.get("envelope") or {}

    result = closeout.run(
        ctx.manifest, ctx.card, outcome, ctx.digest, comments, ctx.adapter, ctx.store,
        allowed_paths, backend=ctx.task.backend, task_model=ctx.task.model,
        landing_ref=landing_ref, branch=branch or ctx.branch,
        commit_range=commit_range, plan_path=envelope.get("plan_path"), gate=gate,
        wall_seconds=ctx.launched.wall_seconds, active_seconds=ctx.launched.active_seconds,
        halt_class=halt_class, cause_line=cause_line,
        timeout_seconds=ctx.overrides.get("closeout_seconds"),
        home=ctx.home, base_env=ctx.base_env, stream=ctx.stream, heartbeat=ctx.store.heartbeat,
        on_release=ctx.store.release, **ctx.launch_kwargs)
    ctx.findings.extend(result.findings)
    ctx.store.upsert(ctx.task.id, closeout=result.result, findings=ctx.findings)

    if getattr(result.launch_result, "lease_lost", False):
        raise _Halt(ctx.task.id, contracts.HALT_RUNNER_CRASHED,
                    "the lease was lost while the closeout for %s was running; nothing was "
                    "pushed" % ctx.task.id,
                    {"stage": "closeout", "status_before": contracts.STATUS_MERGING,
                     "branch": ctx.branch})

    scope = gitwrite.closeout_scope_check(ctx.repo, pre_closeout_head, allowed_paths,
                                          ops=ctx.store, task_id=ctx.task.id, env=ctx.env)
    if not scope.ok:
        # Two classes reach here. A path outside the allowed set is out of scope; a tree the
        # closeout left dirty entirely inside the allowed set is an unclean exit. Both reset to
        # the pre closeout head, and neither pushes.
        evidence = {"branch": ctx.default, "reset_to": scope.reset_to,
                    "offending": scope.offending, "untracked": scope.untracked,
                    "path": ", ".join(scope.offending) or "nothing outside the allowed paths",
                    "allowed": ", ".join(allowed_paths) or "nothing outside the default branch"}
        raise _Halt(ctx.task.id, scope.halt_class,
                    summary.cause_line(scope.halt_class, evidence), evidence)

    if gitread.rev_parse(ctx.repo, "HEAD") != pre_closeout_head:
        pushed = gitwrite.push(ctx.repo, ["origin", ctx.default], ops=ctx.store,
                               task_id=ctx.task.id, env=ctx.env,
                               timeout=ctx.overrides.get("gate_seconds"))
        if not pushed.ok:
            raise _Halt(ctx.task.id, contracts.HALT_GATE_REFUSED,
                        "the push of the closeout commit was refused for %s" % ctx.task.id,
                        {"branch": ctx.default, "sha": gitread.rev_parse(ctx.repo, "HEAD"),
                         "log": ctx.store.path("gate", ctx.task.id + ".log"),
                         "push_output": pushed.output})
    return result


def _note_halt(ctx, halt):
    """R4: make a halt visible on the tracker card without changing its status, by launching the
    Closeout with `outcome=halted`, the same mechanism a landed or blocked outcome already uses.
    Best effort: everything below is wrapped so a failure anywhere in it, one of the checks, the
    checkout, or the Closeout launch itself, is logged and swallowed rather than propagating,
    leaving the halt already raised (`halt`) as the run's only record of what happened, per KTD4
    (this repo's own CLAUDE.md: "every stop is a named class with evidence, not an exception").
    An unguarded check that raised would otherwise escape `_one_task`'s `except _Halt` handler
    and reach `run()`'s own except-Exception path, which fabricates a new halt from whatever
    broke, exactly the masking this function exists to prevent. This is the same reason
    `_continue_past` wraps its own mutation in a `try` rather than trusting each check to stay
    side-effect free.

    Gate 1 also excludes a halt whose evidence carries `reset_to`: that key is unique to
    `gitwrite.closeout_scope_check`'s own `HALT_UNCLEAN_EXIT` raise (a Closeout that left
    in-scope work uncommitted), which already reset the tree before raising, so the tree-clean
    check alone would not catch it, and `HALT_UNCLEAN_EXIT` is too general a class to exclude
    outright (most of its raise sites are unrelated to Closeout and still want the comment).

    Four checks gate the launch, each closing a specific gap KTD3 names:

    1. The halt class is run-scoped, or means the Closeout mechanism itself just misbehaved
       (`CLOSEOUT_MISBEHAVED_HALT_CLASSES`, or the `reset_to` case above): the repository, a
       second runner, or Closeout's own trustworthiness is uncertain, so no second process is
       launched onto it.
    2. The tree is dirty: that state is the operator's own evidence to inspect, not a workspace
       to launch a process into (mirrors R16's pre-flight refusal for the task process).
    3. The default branch is not in sync with `origin/<default>` (`gitwrite.head_equals_remote`,
       the same check pre-flight and the resume disposition already share): `local_merge_tail`'s
       push step can fail after the merge already applied locally, and a commit the halted
       closeout makes on top would otherwise carry that unverified merge to origin alongside it.
    4. The lease can no longer be confirmed as this runner's: the same freshness check
       `_continue_past` already applies before its own repository mutation.

    When this task's record already carries a `landing_ref` (a halt raised after its own landed
    closeout already ran, from a mirror push refusal or a failing final verify), that reference
    is passed through so the rendered comment reads as "landed, then this later step failed"
    instead of an undifferentiated halt on a card that already shows landed.
    """
    if (halt.halt_class in contracts.RUN_SCOPED_HALT_CLASSES
            or halt.halt_class in contracts.CLOSEOUT_MISBEHAVED_HALT_CLASSES
            or halt.evidence.get("reset_to") is not None):
        return
    try:
        if not gitread.is_clean(ctx.repo):
            if ctx.stream is not None:
                ctx.stream("%s: tree left dirty on %s; skipping the halt comment"
                           % (halt.task_id, gitread.current_branch(ctx.repo)))
            return
        if not gitwrite.head_equals_remote(ctx.repo, ctx.default, {}):
            if ctx.stream is not None:
                ctx.stream("%s: %s is not in sync with origin/%s; skipping the halt comment"
                           % (halt.task_id, ctx.default, ctx.default))
            return
        if not ctx.store.heartbeat():
            if ctx.stream is not None:
                ctx.stream("%s: lease no longer confirmed; skipping the halt comment"
                           % halt.task_id)
            return
        gitwrite.blocked_path(ctx.repo, ctx.default, ctx.branch, ops=ctx.store,
                              task_id=halt.task_id, env=ctx.env)
        record = ctx.store.get(halt.task_id) or {}
        _run_closeout(ctx, closeout.OUTCOME_HALTED, landing_ref=record.get("landing_ref"),
                     halt_class=halt.halt_class, cause_line=halt.message)
    except Exception as exc:
        if ctx.stream is not None:
            ctx.stream("%s: could not comment halt %s on the tracker: %s"
                       % (halt.task_id, halt.halt_class, exc))
