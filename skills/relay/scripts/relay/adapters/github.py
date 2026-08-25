"""GitHub Projects adapter, read side (U4).

Reads go through `gh`, which already holds the operator's authentication, so Relay carries no
secret of its own here. The `run` callable is injectable for the same reason as Jira's opener:
tests use recorded fixtures, and `gh` may be absent from the machine running them.

Terminal means one of two things, per the plan: the issue is CLOSED, or the item's status in the
project board equals the status field the manifest names. The second is what lets a project whose
"Done" column matters more than the issue state still land a task.
"""
import json
import subprocess

from . import NETWORK_TIMEOUT_SECONDS, OUTCOME_LANDED, reference_hit, skipped

ISSUE_FIELDS = "title,body,state,comments"
CLOSED_STATE = "CLOSED"

WRITE_BASH_PREFIXES = ("gh issue", "gh project item-edit")
CLOSEOUT_TOOLS = ("Bash",)


def make_run(cwd):
    """The default transport: `gh` in the target repo, bounded, output captured."""

    def run(args, timeout=NETWORK_TIMEOUT_SECONDS):
        return subprocess.run(list(args), cwd=cwd, capture_output=True, text=True,
                              timeout=timeout, stdin=subprocess.DEVNULL)

    return run


class GitHubAdapter:
    def __init__(self, manifest, run=None):
        self._owner = manifest.tracker.owner
        self._project_number = manifest.tracker.project_number
        self._status_field = manifest.tracker.status_field
        self._run = run or make_run(manifest.project.repo)

    # Transport.
    def _gh(self, args):
        """Returns (payload, None) or (None, reason). A nonzero exit is a reason, not a crash."""
        try:
            proc = self._run(list(args), timeout=NETWORK_TIMEOUT_SECONDS)
        except (OSError, subprocess.SubprocessError) as exc:
            return None, "gh could not run: %s" % exc
        if proc.returncode != 0:
            return None, "gh exited %d: %s" % (proc.returncode, (proc.stderr or "").strip())
        try:
            return json.loads(proc.stdout or "null"), None
        except ValueError as exc:
            return None, "gh returned output that is not JSON: %s" % exc

    def _items(self):
        payload, reason = self._gh([
            "gh", "project", "item-list", str(self._project_number),
            "--owner", str(self._owner), "--format", "json",
        ])
        if payload is None:
            return [], reason
        return payload.get("items") or [], None

    def _issue(self, task_id):
        return self._gh(["gh", "issue", "view", str(task_id), "--json", ISSUE_FIELDS])

    def _project_status(self, task_id):
        items, _ = self._items()
        for item in items:
            content = item.get("content") or {}
            if str(content.get("number")) == str(task_id):
                return item.get("status")
        return None

    def _comments(self, task_id):
        payload, reason = self._issue(task_id)
        if payload is None:
            return [], reason
        return [{"id": str(entry.get("id")), "body": entry.get("body") or "",
                 "created": entry.get("createdAt")} for entry in payload.get("comments") or []], None

    # Interface.
    def candidates(self):
        items, _ = self._items()
        found = []
        for item in items:
            content = item.get("content") or {}
            if content.get("number") is None:
                continue
            found.append({
                "id": str(content.get("number")),
                "title": content.get("title") or "",
                "description": content.get("body") or "",
                "status": item.get("status"),
            })
        return found

    def read(self, task_id):
        payload, reason = self._issue(task_id)
        if payload is None:
            return {"id": str(task_id), "title": "", "description": "", "status": None, "skipped": reason}
        return {
            "id": str(task_id),
            "title": payload.get("title") or "",
            "description": payload.get("body") or "",
            "status": payload.get("state"),
        }

    def status(self, task_id):
        payload, reason = self._issue(task_id)
        if payload is None:
            return skipped(reason)
        state = payload.get("state")
        if state == CLOSED_STATE:
            return {"status": state, "terminal": True, "reference": None, "skipped": None}
        board = self._project_status(task_id) if self._status_field else None
        terminal = bool(board) and str(board).lower() == str(self._status_field).lower()
        return {"status": board or state, "terminal": terminal, "reference": None, "skipped": None}

    def comments_since(self, task_id, baseline_comment_id):
        entries, _ = self._comments(task_id)
        if baseline_comment_id is None:
            return entries
        ids = [entry["id"] for entry in entries]
        baseline = str(baseline_comment_id)
        if baseline in ids:
            return entries[ids.index(baseline) + 1:]
        return entries

    def closing_reference(self, task_id, ref):
        entries, _ = self._comments(task_id)
        for entry in entries:
            if reference_hit(entry["body"], ref):
                return entry["id"]
        return None

    def write_tool_patterns(self):
        """`gh pr create` is deliberately absent: in PR terminal mode the task process opens the
        pull request, and reading that as a denied tracker write would misclassify the run."""
        return {"tools": (), "bash": WRITE_BASH_PREFIXES, "paths": ()}

    def closeout_allowed_tools(self):
        return CLOSEOUT_TOOLS

    def closeout_instructions(self, outcome):
        if outcome == OUTCOME_LANDED:
            return ("Close the issue with `gh issue close <number>` and add one comment naming the "
                    "landing reference below, or move its project item to the terminal status.")
        return ("Add one comment carrying the blocker digest below with `gh issue comment`. Do not "
                "close the issue and do not move its project item: a blocked task stays open.")
