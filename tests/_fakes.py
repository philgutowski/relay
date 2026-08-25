"""In test doubles for the seams U8 depends on but does not own.

`FakeAdapter` satisfies the tracker adapter interface KTD16 and plan U4 define, so verify and
the git tail can be tested before the real Jira, GitHub, and markdown adapters land in U4. It is
a data holder: every method answers from a dict the test set up, and none of them touch a
network, `gh`, or git. When U4 lands, the same scenarios should also run against the real
adapters; this fake proves the contract, not any adapter's behavior.

`FakeRun` stands in for the `run` callable an adapter injects for `gh` (KTD16), so the PR mode
helpers can be driven without `gh` installed.
"""


class FakeAdapter:
    """The KTD16 interface with canned answers.

    statuses: {task id: {"status": name, "terminal": bool, "reference": ref or None}}
              or {"skipped": "reason"} to model a read that failed and must not crash a caller.
    comments: {task id: [{"id": ..., "body": ...}, ...]} in order, oldest first.
    references: {(task id, ref): comment id} for closing_reference hits.
    """

    def __init__(self, statuses=None, comments=None, references=None, candidates=None,
                 write_patterns=None, closeout_tools=("Bash",), instructions=None):
        self.statuses = dict(statuses or {})
        self.comments = dict(comments or {})
        self.references = dict(references or {})
        self._candidates = list(candidates or [])
        self._write_patterns = write_patterns or {"tools": ("fake_tracker__",), "bash": (), "paths": ()}
        self._closeout_tools = tuple(closeout_tools)
        self._instructions = dict(instructions or {})
        self.calls = []

    def candidates(self):
        self.calls.append(("candidates",))
        return list(self._candidates)

    def read(self, task_id):
        self.calls.append(("read", task_id))
        entry = self.statuses.get(task_id, {})
        return {
            "id": task_id,
            "title": entry.get("title", "task %s" % task_id),
            "description": entry.get("description", ""),
            "status": entry.get("status"),
        }

    def status(self, task_id):
        self.calls.append(("status", task_id))
        entry = self.statuses.get(task_id)
        if entry is None:
            return {"status": None, "terminal": False, "reference": None, "skipped": "unknown task %s" % task_id}
        return {
            "status": entry.get("status"),
            "terminal": bool(entry.get("terminal")),
            "reference": entry.get("reference"),
            "skipped": entry.get("skipped"),
        }

    def comments_since(self, task_id, baseline_comment_id):
        self.calls.append(("comments_since", task_id, baseline_comment_id))
        entries = list(self.comments.get(task_id, []))
        if baseline_comment_id is None:
            return entries
        ids = [entry.get("id") for entry in entries]
        if baseline_comment_id in ids:
            return entries[ids.index(baseline_comment_id) + 1:]
        return entries

    def closing_reference(self, task_id, ref):
        self.calls.append(("closing_reference", task_id, ref))
        for (key, prefix), comment_id in self.references.items():
            if key == task_id and ref and (ref.startswith(prefix) or prefix.startswith(ref)):
                return comment_id
        return None

    def write_tool_patterns(self):
        return dict(self._write_patterns)

    def closeout_allowed_tools(self):
        return tuple(self._closeout_tools)

    def closeout_instructions(self, outcome):
        return self._instructions.get(outcome, "record the %s outcome on the card" % outcome)


class FakeRun:
    """A `run(args, timeout=None)` callable returning queued results, oldest first.

    Each queued entry is (returncode, stdout, stderr). The last entry repeats once the queue is
    spent, so a poll loop can be driven by one never deciding answer.
    """

    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def __call__(self, args, timeout=None):
        self.calls.append(list(args))
        entry = self.results.pop(0) if len(self.results) > 1 else self.results[0]
        code, out, err = entry
        return _Completed(code, out, err)


class _Completed:
    def __init__(self, returncode, stdout, stderr):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class RecordingOps:
    """Stands in for the state store's git op recorder, so a test can assert that every
    mutating call left an intent entry and a result entry (plan U8 verification)."""

    def __init__(self):
        self.entries = []

    def record_git_op(self, task_id, op, phase, detail=None):
        entry = {"task": task_id, "op": op, "phase": phase, "detail": detail}
        self.entries.append(entry)
        return entry

    def ops(self):
        return [(entry["op"], entry["phase"]) for entry in self.entries]
