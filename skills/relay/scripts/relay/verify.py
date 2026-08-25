"""Verify-landed (U8, KTD13): the runner's own verdict on whether a task landed.

This is the module that makes Relay trustworthy. A headless task process has every incentive to
report success, so its exit code, its printed result, and its return envelope are all excluded
here by design (R20). The only inputs are git, the tracker read through an adapter, and the
baseline the runner recorded before launch (R17).

It is a pure function over those inputs, which is what lets `relay verify <task>` re-run the
same verdict on a halted record days later, and lets the summary carry evidence a reader can
re-check by hand. Two scopes (KTD13): `code` runs the git checks only and backs the pre-closeout
verify in R50; `full` adds the mirror and the tracker checks and backs the final verify, the
`verify` verb, and startup re-verify. Only a `full` pass moves a record to landed.

Each check answers `pass`, `fail`, or `skipped`, with evidence. A `skipped` check carries a
`blocking` flag, because two very different things are called skipped: a check that does not
apply in this shipping mode (`pr_open` in local merge mode) is not blocking, while a check the
runner could not read (a tracker call that failed, a missing landing reference) is blocking, so
an unreadable tracker can never be mistaken for a landing.

The tracker adapter interface is the one KTD16 and plan U4 define. This module uses three of its
methods and expects plain data back:

    status(task_id)            -> {"status": name, "terminal": bool, "reference": ref or None,
                                   "skipped": reason or None}
    closing_reference(id, ref) -> a comment id when the card names the landing reference, else None
    comments_since(id, base)   -> the comments newer than a baseline id (used by U9, not here)

`pr_probe` is the PR terminal mode seam: a callable taking the task branch and returning
`{"url", "number", "state", "ci"}`, built by the run loop from gitwrite.find_pr and
gitwrite.poll_ci. Without one, the PR checks are blocking skips rather than silent passes.
"""
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from . import contracts, gitread, gitwrite

SCOPE_CODE = "code"
SCOPE_FULL = "full"

PASS = "pass"
FAIL = "fail"
SKIPPED = "skipped"

CODE_CHECKS = ("tree_clean", "on_default", "head_equals_remote", "new_commit_since_baseline",
               "pr_open", "ci_green")
TRACKER_CHECKS = ("card_terminal", "closing_reference")
FULL_CHECKS = CODE_CHECKS + ("mirror_equals_head",) + TRACKER_CHECKS

LOCAL_MERGE = "local_merge"
PR_TERMINAL = "pr_terminal"


@dataclass
class Verdict:
    scope: str
    checks: dict = field(default_factory=dict)
    landed: bool = False
    halt_class: str | None = None
    at: str | None = None
    evidence: dict = field(default_factory=dict)

    def failed(self):
        return [name for name, check in self.checks.items() if check["result"] == FAIL]

    def blocking_skips(self):
        return [name for name, check in self.checks.items()
                if check["result"] == SKIPPED and check.get("blocking")]

    def as_dict(self):
        return {
            "scope": self.scope,
            "checks": self.checks,
            "landed": self.landed,
            "halt_class": self.halt_class,
            "at": self.at,
            "evidence": self.evidence,
        }


def _check(result, evidence=None, blocking=False):
    entry = {"result": result, "evidence": evidence or {}}
    if result == SKIPPED:
        entry["blocking"] = bool(blocking)
    return entry


def _skip(reason, blocking=False):
    return _check(SKIPPED, {"reason": reason}, blocking)


class _GitReadFailed(Exception):
    """A git read the verdict needed could not be completed."""


def _safe(fn, *args, **kwargs):
    """Every git read in this module goes through here. A ref that no longer resolves, a repo
    an operator moved, or a permission failure becomes a blocking skip rather than an exception,
    which is what lets a later session re-run the verdict on a repaired repo without crashing."""
    try:
        return fn(*args, **kwargs)
    except gitread.GitError as exc:
        raise _GitReadFailed(str(exc))


def default_branch_of(manifest):
    return (manifest.project.default_branch
            or gitread.default_branch(manifest.project.repo)
            or "main")


def _tracker_findings(record):
    return [finding for finding in (record.get("findings") or [])
            if finding.get("class") == contracts.HALT_TRACKER_WRITE_DENIED]


def verify(manifest, record, adapter, scope=SCOPE_FULL, pr_probe=None, do_fetch=False,
           env=None, now=time.time):
    """The verdict for one task record. Never raises for a tracker or git read that fails; an
    unreadable input becomes a blocking skip so the caller sees `not landed`, not a crash."""
    repo = manifest.project.repo
    default = default_branch_of(manifest)
    mode = manifest.shipping_mode
    task_id = record.get("id")
    branch = gitwrite.task_branch_for(task_id) if task_id else None
    checks = {}

    if do_fetch:
        try:
            gitread.fetch(repo, env=env)
        except gitread.GitError:
            pass

    # Git checks.
    try:
        porcelain = _safe(gitread.status_porcelain, repo)
        checks["tree_clean"] = _check(PASS if not porcelain.strip() else FAIL,
                                      {"tree": porcelain.strip().splitlines()[:10]})
    except _GitReadFailed as exc:
        checks["tree_clean"] = _skip("could not read the working tree: %s" % exc, blocking=True)

    try:
        current = _safe(gitread.current_branch, repo)
        checks["on_default"] = _check(PASS if current == default else FAIL,
                                      {"branch": current, "default_branch": default})
    except _GitReadFailed as exc:
        checks["on_default"] = _skip("could not read the current branch: %s" % exc, blocking=True)

    local_sha = gitread.rev_parse(repo, default)
    remote_sha = gitread.rev_parse(repo, "origin/" + default)
    evidence = {"local_sha": local_sha, "remote_sha": remote_sha}
    if remote_sha is None:
        checks["head_equals_remote"] = _skip("origin/%s does not resolve" % default, blocking=True)
    else:
        checks["head_equals_remote"] = _check(PASS if local_sha == remote_sha else FAIL, evidence)

    baseline = record.get("baseline_sha")
    if mode == PR_TERMINAL:
        checks["new_commit_since_baseline"] = _skip("shipping mode is pr_terminal; Relay does not merge")
    elif not baseline:
        checks["new_commit_since_baseline"] = _skip("no baseline_sha on the record", blocking=True)
    else:
        try:
            commits = _safe(gitread.log_oneline, repo, baseline, default) if local_sha else []
            checks["new_commit_since_baseline"] = _check(
                PASS if commits else FAIL,
                {"baseline_sha": baseline, "head_sha": local_sha, "commits": len(commits)},
            )
        except _GitReadFailed as exc:
            # The usual cause is a baseline the operator's repair rewrote away. Not landed,
            # and not a crash: the record says why and the run can halt cleanly.
            checks["new_commit_since_baseline"] = _skip(
                "could not compare against the baseline %s: %s" % (baseline, exc), blocking=True)

    # PR terminal checks.
    if mode != PR_TERMINAL:
        reason = "shipping mode is %s" % mode
        checks["pr_open"] = _skip(reason)
        checks["ci_green"] = _skip(reason)
    elif pr_probe is None:
        reason = "no PR probe supplied to verify"
        checks["pr_open"] = _skip(reason, blocking=True)
        checks["ci_green"] = _skip(reason, blocking=True)
    else:
        try:
            probe = pr_probe(branch) or {}
        except Exception as exc:  # a probe shells out to gh; a failure is evidence, not a crash
            probe = {"error": str(exc)}
        if probe.get("error") or not probe.get("url"):
            checks["pr_open"] = _check(FAIL, probe)
            checks["ci_green"] = _skip("no open PR to check", blocking=True)
        else:
            checks["pr_open"] = _check(PASS if probe.get("state", "OPEN") == "OPEN" else FAIL, probe)
            checks["ci_green"] = _check(PASS if probe.get("ci") == "pass" else FAIL,
                                        {"ci": probe.get("ci"), "url": probe.get("url")})

    if scope == SCOPE_CODE:
        return _finish(Verdict(scope, checks, at=_stamp(now)), record)

    # Mirror (R6, R50: pushed only after the closeout, so it is a full scope check).
    target = gitwrite.mirror_target(manifest.project.mirror)
    if target is None and not manifest.project.mirror:
        checks["mirror_equals_head"] = _skip("no project.mirror in the manifest")
    elif target is None:
        checks["mirror_equals_head"] = _skip(
            "project.mirror %r names no remote and destination the runner can read back"
            % (list(manifest.project.mirror),), blocking=True)
    else:
        remote, destination = target
        mirror_sha = gitread.rev_parse(repo, "%s/%s" % (remote, destination))
        checks["mirror_equals_head"] = _check(
            PASS if mirror_sha and mirror_sha == local_sha else FAIL,
            {"mirror_sha": mirror_sha, "head_sha": local_sha, "mirror": list(manifest.project.mirror)},
        )

    # Tracker checks (R22). Both halves are required: code that merged while the card stayed put
    # is a partial landing, not a landing.
    try:
        card = adapter.status(task_id) or {}
    except Exception as exc:
        card = {"skipped": "adapter.status raised: %s" % exc}
    if card.get("skipped"):
        checks["card_terminal"] = _skip(card["skipped"], blocking=True)
    else:
        checks["card_terminal"] = _check(PASS if card.get("terminal") else FAIL,
                                         {"status": card.get("status"), "terminal": bool(card.get("terminal"))})

    landing_ref = record.get("landing_ref")
    if not landing_ref:
        checks["closing_reference"] = _skip("no landing_ref on the record yet", blocking=True)
    else:
        try:
            comment_id = adapter.closing_reference(task_id, landing_ref)
            checks["closing_reference"] = _check(PASS if comment_id else FAIL,
                                                 {"ref": landing_ref, "comment_id": comment_id})
        except Exception as exc:
            checks["closing_reference"] = _skip(
                "adapter.closing_reference raised: %s" % exc, blocking=True)

    return _finish(Verdict(scope, checks, at=_stamp(now)), record)


def _stamp(now):
    return datetime.fromtimestamp(now(), tz=timezone.utc).isoformat()


def _finish(verdict, record):
    """Landing and the halt class, from the checks alone.

    Landed means every applicable full check passed and both tracker halves passed. A code side
    that passed while a tracker check failed is `partial_landing` (AE1): the code is on the
    remote and the card is not, which halts the run rather than continuing past it. A blocking
    skip yields neither, because the runner did not read enough to say either way; the caller's
    own evidence (a refused gate, a moved remote) names that class.
    """
    verdict.evidence = {
        "landing_ref": record.get("landing_ref"),
        "baseline_sha": record.get("baseline_sha"),
        "findings": _tracker_findings(record),
    }
    if verdict.scope != SCOPE_FULL:
        return verdict
    failed = verdict.failed()
    blocking = verdict.blocking_skips()
    tracker_pass = all(verdict.checks[name]["result"] == PASS for name in TRACKER_CHECKS)
    if not failed and not blocking and tracker_pass:
        verdict.landed = True
        verdict.halt_class = contracts.HALT_LANDED
        return verdict
    code_side_failed = [name for name in failed if name not in TRACKER_CHECKS]
    tracker_failed = [name for name in failed if name in TRACKER_CHECKS]
    if tracker_failed and not code_side_failed and not blocking:
        verdict.halt_class = contracts.HALT_PARTIAL_LANDING
    return verdict


def startup_reverify(manifest, store, adapter, pr_probe=None, env=None, now=time.time):
    """R48 and R55: on startup, abort a merge the previous runner left half done, then re-run
    the full verdict on every halted record and promote the ones that now pass. A repair the
    operator made by hand between runs is exactly what this catches. Blocked, excluded, and
    landed records are left alone."""
    gitwrite.abort_dangling_merge(manifest.project.repo, env=env)
    promoted = []
    for task_id, record in sorted(store.records().items()):
        if record.get("status") != contracts.STATUS_HALTED:
            continue
        verdict = verify(manifest, record, adapter, scope=SCOPE_FULL, pr_probe=pr_probe,
                         env=env, now=now)
        if not verdict.landed:
            store.upsert(task_id, verify=verdict.as_dict())
            continue
        store.upsert(task_id, status=contracts.STATUS_LANDED, halt_class=contracts.HALT_LANDED,
                     verify=verdict.as_dict())
        promoted.append(task_id)
    return promoted
