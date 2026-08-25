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

NETWORK_TIMEOUT_SECONDS = 30

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


def skipped(reason):
    """The status shape for a read that could not be completed."""
    return {"status": None, "terminal": False, "reference": None, "skipped": str(reason)}


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
