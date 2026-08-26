"""Tracker adapters, read side only (U4, KTD16).

An adapter is the whole of what Relay knows about a tracker. Everything project specific and
tracker specific lives behind this interface, which is what keeps the runner project agnostic
(R1) and keeps `mcp__atlassian__` and `gh` out of the classifier and the closeout template.

No method here writes. That is the point of R19: the runner reads the tracker to decide whether
a task landed, and every write goes through a Claude process instead, so a defect in the runner
can never move a card. The shared test suite asserts that the public surface of every adapter is
exactly the eight methods below.

The interface, with the shapes each method returns:

    candidates()                  -> [{"id", "title", "description", "status"}, ...]
    read(id)                      -> {"id", "title", "description", "status"}
    status(id)                    -> {"status", "terminal", "reference", "skipped"}
    comments_since(id, baseline)  -> [{"id", "body", "created"}, ...] newer than baseline, in order
    closing_reference(id, ref)    -> the comment id naming ref, else None
    write_tool_patterns()         -> {"tools": (...), "bash": (...), "paths": (...)}
    closeout_allowed_tools()      -> (tool name, ...) explicit, never a wildcard
    closeout_instructions(outcome)-> the duty one text for the closeout brief

`status` returning a `skipped` reason rather than raising is deliberate: a tracker that cannot be
read must never be mistaken for either a landing or a failure to land, so verify turns a skip
into a blocking unknown and the run halts with something an operator can act on. Every network
or subprocess call is bounded by NETWORK_TIMEOUT_SECONDS.

Every adapter takes an injectable transport, so tests run against recorded fixtures and never
touch a network or invoke `gh`: an opener for Jira, a `run` callable for `gh`, and the git read
wrapper for markdown.
"""

import re

NETWORK_TIMEOUT_SECONDS = 30

# A commit sha as a comment body abbreviates it. Seven characters is git's own short sha floor.
SHA_TOKEN_RE = re.compile(r"\b[0-9a-f]{7,40}\b")

INTERFACE = (
    "candidates",
    "read",
    "status",
    "comments_since",
    "closing_reference",
    "write_tool_patterns",
    "closeout_allowed_tools",
    "closeout_instructions",
)

OUTCOME_LANDED = "landed"
OUTCOME_BLOCKED = "blocked"


class ConfigurationError(ValueError):
    """A manifest or environment problem the operator must fix before any run. Raised at
    construction, before a single request, so `relay validate` names the missing variable."""


def reference_hit(body, ref):
    """True when a body names a landing reference: the full string (a PR URL), or a commit sha
    the body abbreviated (`abc1234` in the body for a full sha on the record). Shared by all
    three adapters because it is a property of git and of URLs, not of any one tracker."""
    if not ref or not body:
        return False
    if ref in body:
        return True
    return any(ref.startswith(token) for token in SHA_TOKEN_RE.findall(body))


def skipped(reason):
    """The status shape for a read that could not be completed."""
    return {"status": None, "terminal": False, "reference": None, "skipped": str(reason)}


def task_tracker_steps(manifest, branch):
    """The two places the task brief tells the process to touch the tracker: the review step
    before the envelope, and the comment when it cannot finish. Resolved by adapter name rather
    than by building the adapter, so a brief renders without a tracker credential.

    The markdown adapter is the reason this exists (first live run, 2026-08-26). Its tracker is
    a file the closeout edits and the runner reads at the remote head, so a task told to "move
    the card to in review" had no card to move and blocked on that step with the code done and
    the gate green. Under markdown the task makes no tracker write at all.
    """
    name = manifest.tracker.adapter
    in_review = manifest.tracker.in_review_status or "its in review status"
    if name == "markdown":
        path = manifest.tracker.file or "the tracker file"
        return {
            "review_step": ("There is no tracker write for you to make. This project's tracker is `%s` "
                            "in the repository, which the runner's own closeout process edits after "
                            "you exit. Do not edit `%s` yourself; confirm the head of `%s` is "
                            "committed and go to the next step." % (path, path, branch)),
            "blocked_step": ("Do not edit `%s`; the runner records the blocker there after you exit. "
                             "Print the envelope with `status: blocked` and the blockers listed."
                             % path),
        }
    return {
        "review_step": ("Move the tracker card to `%s` and comment the head commit of `%s` on it. "
                        "This is the last tracker write you make; the runner launches a separate "
                        "process to close the card once the merge exists." % (in_review, branch)),
        "blocked_step": ("Comment the blocker on the tracker card, then print the envelope with "
                         "`status: blocked` and the blockers listed."),
    }


def build(manifest, env=None, opener=None, run=None, read=None):
    """The adapter the manifest names. Imports are local so a machine without one tracker's
    dependencies can still use the others."""
    name = manifest.tracker.adapter
    if name == "jira":
        from .jira import JiraAdapter

        return JiraAdapter(manifest, opener=opener, env=env)
    if name == "github":
        from .github import GitHubAdapter

        return GitHubAdapter(manifest, run=run)
    if name == "markdown":
        from .markdown import MarkdownAdapter

        return MarkdownAdapter(manifest, read=read)
    raise ConfigurationError("unknown tracker.adapter %r; expected jira, github, or markdown" % name)
