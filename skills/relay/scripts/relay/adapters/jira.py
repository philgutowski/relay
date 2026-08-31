"""Jira adapter, read side (U4, KTD9).

Reads go straight to the Jira REST API rather than through a model. A read that routes through a
`claude -p` call is a report of the thing being verified, not the thing itself, and landing is
exactly the claim Relay refuses to take on trust.

Credentials are read from the environment once, at construction, and never leave this object.
The launcher scrubs the same variables out of every child process env, so no task process, gate,
or push ever sees the token.
"""
import base64
import json
import urllib.error
import urllib.parse
import urllib.request

from . import (ConfigurationError, NETWORK_TIMEOUT_SECONDS, OUTCOME_HALTED, OUTCOME_LANDED,
               reference_hit, skipped)

ISSUE_FIELDS = "summary,description,status,comment"
# The issue endpoint is the one the plan pins. Enhanced search is the current Jira Cloud path for
# a JQL query and backs `validate --list` only, so a change there degrades to a skipped listing
# rather than a wrong landing verdict.
ISSUE_PATH = "/rest/api/3/issue/%s"
SEARCH_PATH = "/rest/api/3/search/jql"

WRITE_TOOL_PREFIX = "mcp__atlassian__"
CLOSEOUT_TOOLS = (
    "mcp__atlassian__getJiraIssue",
    "mcp__atlassian__getTransitionsForJiraIssue",
    "mcp__atlassian__transitionJiraIssue",
    "mcp__atlassian__addCommentToJiraIssue",
)


def _adf_text(node):
    """Flatten Atlassian Document Format, the nested JSON Jira stores rich text in, to plain
    text. The brief passes a description verbatim, so it has to be text, not a document tree."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "".join(_adf_text(item) for item in node)
    if isinstance(node, dict):
        kind = node.get("type")
        if kind == "text":
            return str(node.get("text", ""))
        if kind == "hardBreak":
            return "\n"
        inner = _adf_text(node.get("content"))
        if kind in ("paragraph", "heading", "listItem", "blockquote", "codeBlock", "rule"):
            return inner + "\n"
        return inner
    return ""


class JiraAdapter:
    def __init__(self, manifest, opener=None, env=None):
        import os

        env = os.environ if env is None else env
        tracker = manifest.tracker
        self._site = (tracker.site or "").rstrip("/")
        self._project_key = tracker.project_key
        self._done = tuple(str(name).lower() for name in tracker.done_statuses)
        token = env.get(tracker.token_env)
        email = env.get(tracker.email_env)
        if not token:
            raise ConfigurationError(
                "the jira adapter needs an API token in %s and it is not set" % tracker.token_env)
        if not email:
            raise ConfigurationError(
                "the jira adapter needs an account email in %s and it is not set" % tracker.email_env)
        pair = ("%s:%s" % (email, token)).encode("utf-8")
        self._authorization = "Basic " + base64.b64encode(pair).decode("ascii")
        self._opener = opener or urllib.request.build_opener()

    # Transport.
    def _get(self, path, params=None):
        """Returns (payload, None) or (None, reason). Never raises for a transport failure."""
        url = "https://%s%s" % (self._site, path)
        if params:
            url += "?" + urllib.parse.urlencode(params)
        request = urllib.request.Request(url, headers={
            "Authorization": self._authorization,
            "Accept": "application/json",
        })
        try:
            with self._opener.open(request, timeout=NETWORK_TIMEOUT_SECONDS) as body:
                return json.loads(body.read().decode("utf-8")), None
        except urllib.error.HTTPError as exc:
            return None, "jira returned %s for %s" % (exc.code, path)
        except (OSError, ValueError) as exc:
            return None, "jira read failed: %s" % exc

    def _issue(self, task_id):
        return self._get(ISSUE_PATH % task_id, {"fields": ISSUE_FIELDS})

    def _comments(self, task_id):
        payload, reason = self._issue(task_id)
        if payload is None:
            return [], reason
        raw = ((payload.get("fields") or {}).get("comment") or {}).get("comments") or []
        return [{"id": str(entry.get("id")), "body": _adf_text(entry.get("body")).strip(),
                 "created": entry.get("created")} for entry in raw], None

    # Interface.
    def candidates(self):
        payload, reason = self._get(SEARCH_PATH, {
            "jql": "project = %s ORDER BY created ASC" % self._project_key,
            "fields": "summary,status",
        })
        if payload is None:
            return []
        found = []
        for issue in payload.get("issues") or []:
            fields = issue.get("fields") or {}
            found.append({
                "id": issue.get("key"),
                "title": fields.get("summary") or "",
                "description": "",
                "status": (fields.get("status") or {}).get("name"),
            })
        return found

    def read(self, task_id):
        payload, reason = self._issue(task_id)
        if payload is None:
            return {"id": task_id, "title": "", "description": "", "status": None, "skipped": reason}
        fields = payload.get("fields") or {}
        return {
            "id": payload.get("key") or task_id,
            "title": fields.get("summary") or "",
            "description": _adf_text(fields.get("description")).strip(),
            "status": (fields.get("status") or {}).get("name"),
        }

    def status(self, task_id):
        payload, reason = self._issue(task_id)
        if payload is None:
            return skipped(reason)
        name = ((payload.get("fields") or {}).get("status") or {}).get("name")
        return {
            "status": name,
            "terminal": bool(name) and name.lower() in self._done,
            "reference": None,
            "skipped": None,
        }

    def comments_since(self, task_id, baseline_comment_id):
        entries, _ = self._comments(task_id)
        if baseline_comment_id is None:
            return entries
        ids = [entry["id"] for entry in entries]
        baseline = str(baseline_comment_id)
        if baseline in ids:
            return entries[ids.index(baseline) + 1:]
        return []

    def closing_reference(self, task_id, ref):
        entries, _ = self._comments(task_id)
        for entry in entries:
            if reference_hit(entry["body"], ref):
                return entry["id"]
        return None

    def write_tool_patterns(self):
        return {"tools": (WRITE_TOOL_PREFIX,), "bash": (), "paths": ()}

    def closeout_allowed_tools(self):
        return CLOSEOUT_TOOLS

    def closeout_instructions(self, outcome):
        if outcome == OUTCOME_LANDED:
            return ("Transition the card to its terminal status, then add one comment naming the "
                    "landing reference below. Use the Jira tools on your allowlist and nothing else.")
        if outcome == OUTCOME_HALTED:
            return ("Add one comment naming the halt class and the cause line below. Do not "
                    "transition the card: a halted task keeps its current status.")
        return ("Add one comment carrying the blocker digest below. Do not transition the card: a "
                "blocked task keeps its current status so the board still shows it as open.")
