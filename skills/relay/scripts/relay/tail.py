"""Follower (U10): decode a running task's stdout and print one line per event.

A Relay run is invisible while it runs. The runner captures each Task process's stdout, which is
`claude -p --output-format stream-json --verbose`, into `<state>/logs/<task-id>.stdout.log`. That
is one JSON object per line and unreadable at a glance. This module turns it into text.

Three things here are not obvious.

The source is the per Task stdout log, not the session transcript and not `runner.log`. The
transcript under `~/.claude/projects/` is the classifier's input, written by the CLI on its own
schedule, so it is the wrong file to follow live. `runner.log` is closer, since `cmd_run` passes a
`stream` writer into `launch.launch` and a detached run's log therefore carries every Task's
stream json in one ordered file. It loses twice: a foreground `relay run` writes no `runner.log`
at all while `launch.launch` writes the per Task log either way, and a `runner.log` line carries
no Task id, so the phase headers below could not be derived from it.

Reads are byte oriented and buffer the incomplete tail. A follower reads a file another process
is appending to, so a read lands mid line routinely. Decoding that fragment fails and the event
is lost silently, which is the defect in the hand written prototype this replaces. Bytes rather
than text also keep a multi byte character split across a read boundary from being mangled.

The log sequence comes from the Manifest and the cursor advances on what exists on disk. Reading
`state.json` for the current Task would be tighter, but a candidate list plus a frontier handles
the three cases a cursor does not: a Task excluded before launch writes no log at all, a Task
whose Closeout never ran writes no closeout log, and a `tail` started late begins mid list.

A follower launched beside a run starts from a floor rather than from the top of the files. The
state directory is keyed on the Manifest's path, so a second run against the same Manifest finds
the previous run's Task logs still there (`launch.py` appends) and the previous run's terminal
record still in `state.json`. Without a floor, `run --follow` would replay the whole previous run
and then end at once on its record, since a record already present wins. `read_floor` is taken
before the runner is launched, which is what makes "only what this launch produced" exact.

Nothing here takes the Lease. `tail` is a reader, the same rule `status` follows.
"""
import json
import os
import time

from . import contracts

# Bounds on one printed event. A task process writes messages far longer than a terminal line,
# and a follower that reflows them is unreadable next to the tool calls between them.
TEXT_CHARS = 600
ARGUMENT_CHARS = 110

# The argument keys, in the order the first present one wins. Copied from the operator's own
# prototype: these are what a reader wants to see for the tools a task actually calls. A tool
# whose input carries none of them renders as a bare name, which is correct for TaskOutput and
# ToolSearch and is why the list is not extended to cover them.
ARGUMENT_KEYS = ("command", "file_path", "pattern", "skill", "description")

POLL_SECONDS = 1.0

# Phase names for the two logs a Task can produce, in the order the runner writes them.
PHASE_TASK = "task"
PHASE_CLOSEOUT = "closeout"


class _Gone:
    """What `follow` returns when the process it was launched beside exited without writing a
    terminal record. Distinct from None, which is the deadline, and from a run status, because
    the three endings map to three different exit codes."""

    def __repr__(self):
        return "<runner gone>"


GONE = _Gone()


def _argument_of(tool_input):
    if not isinstance(tool_input, dict):
        return ""
    for key in ARGUMENT_KEYS:
        value = tool_input.get(key)
        if value:
            return str(value)
    return ""


def decode(raw):
    """One raw stdout line in, zero or more printable events out.

    Accepts bytes or str, because the follower reads bytes and the tests read either. A line that
    is not JSON, is not an object, or carries no content blocks yields nothing rather than
    raising: a follower that dies on one malformed line is worse than one that skips it, and the
    stream carries several line types (`system`, `tool_progress`, `rate_limit_event`, `result`)
    that hold no message at all.
    """
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    raw = (raw or "").strip()
    if not raw:
        return []
    try:
        payload = json.loads(raw)
    except ValueError:
        return []
    if not isinstance(payload, dict):
        return []
    message = payload.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, list):
        return []

    is_assistant = payload.get("type") == contracts.TRANSCRIPT_TYPE_ASSISTANT
    events = []
    for block in content:
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind == "text" and is_assistant:
            # `thinking` blocks are deliberately not rendered. They outnumbered text blocks two
            # to one in the log this was built against and would bury everything else.
            body = str(block.get("text", "")).strip()
            if body:
                events.append(body[:TEXT_CHARS])
        elif kind == "tool_use":
            argument = _argument_of(block.get("input"))
            name = str(block.get("name") or "tool")
            events.append("  > %-10s %s" % (name, argument[:ARGUMENT_CHARS]))
    return events


def candidates(manifest, store):
    """Every log the run can produce, in the order the runner writes them: one Task log and one
    Closeout log per Task, in Manifest order. Returns (task id, phase, path) triples.

    Derived from the Manifest rather than from `state.json`'s cursor, because the cursor names
    only where the run is now. This list also names where it has been and where it is going, so a
    `tail` started at any point in the run has the same map.
    """
    entries = []
    for task in manifest.tasks:
        entries.append((task.id, PHASE_TASK, store.path("logs", task.id + ".stdout.log")))
        entries.append((task.id, PHASE_CLOSEOUT,
                        store.path("logs", task.id + ".closeout.stdout.log")))
    return entries


def _size(path):
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def read_floor(manifest, store):
    """What is already on disk, read before a run is launched, so a follower given it reports
    only what that launch produces. Carries every candidate log's current size, the terminal
    record present at the time, and every Task's status at the time.

    The statuses are the baseline for phase events. Taking them here rather than on the
    follower's first poll is what stops a runner that reaches its first `store.upsert` before the
    follower's first read from having that first transition swallowed as history.
    """
    state = store.read() or {}
    return {"offsets": {path: _size(path) for _id, _phase, path in candidates(manifest, store)},
            "terminal": state.get("terminal"),
            "statuses": {task_id: record.get("status")
                         for task_id, record in (state.get("tasks") or {}).items()}}


class _Reader:
    """One log file, read forward from where the last read stopped, and the Task and phase it
    belongs to so the follower can name it.

    Holds the bytes after the last newline rather than decoding them, because the writer is still
    appending and a read lands mid line routinely. The fragment is completed by the next read.
    """

    def __init__(self, task_id, phase, path, start_offset=0):
        self.task_id = task_id
        self.phase = phase
        self.path = path
        self.start_offset = start_offset
        self.offset = start_offset
        self.buffer = b""

    def active(self):
        """True once this file carries bytes past where this follower started.

        Not `os.path.exists`. A follower given a floor seeds its offset at the size of a log a
        previous run left behind, and that file exists. Driving the frontier off existence would
        push the cursor past every earlier Task on the first poll, and the output this run is
        about to append to them would never be drained.
        """
        return _size(self.path) > self.start_offset

    def drain(self):
        """The complete lines appended since the last call. A missing file drains to nothing,
        so a candidate whose process never ran costs a stat and no more."""
        try:
            with open(self.path, "rb") as handle:
                handle.seek(self.offset)
                chunk = handle.read()
                self.offset = handle.tell()
        except OSError:
            return []
        if not chunk:
            return []
        self.buffer += chunk
        parts = self.buffer.split(b"\n")
        self.buffer = parts.pop()
        return parts


def follow(manifest, store, stream, sleep=time.sleep, poll_seconds=POLL_SECONDS, floor=None,
           deadline_seconds=None, clock=time.monotonic, phases_only=False, notifier=None,
           runner_alive=None):
    """Follow the run's logs until it reaches a terminal record. Returns that record's
    `run_status`, which the caller maps to an exit code.

    Three other endings, each its own return value because each maps to a different exit code.
    `None` means the `deadline_seconds` bound was reached and the run is still going. `GONE` means
    `runner_alive` reported the launched process had exited and it never wrote a record.

    `floor` is what `read_floor` returned before the run was launched; passing it makes this
    follower report only what that launch produced. `sleep` and `clock` are injected so a test can
    advance a scripted run between polls instead of waiting on a wall clock. Never acquires either
    Lease: this reads `state.json` and the log files and writes nothing, the same rule `status`
    follows.
    """
    offsets = (floor or {}).get("offsets") or {}
    terminal_floor = (floor or {}).get("terminal")
    has_floor = floor is not None

    # One reader per distinct log. `tail` does not validate the Manifest, so it accepts shapes
    # `validate` refuses: a Task listed twice would otherwise get two readers on one file and
    # replay it once each, and an empty Task list leaves nothing to index.
    readers = []
    seen = set()
    for task_id, phase_name, path in candidates(manifest, store):
        if path not in seen:
            seen.add(path)
            readers.append(_Reader(task_id, phase_name, path, start_offset=offsets.get(path, 0)))
    announced = set()
    cursor = 0
    waiting_said = False
    # The baseline for phase events. A follower must not announce history as news, so a follower
    # with no floor takes its first poll as the baseline. A follower with one takes the statuses
    # read before the run was launched, which is tighter: everything the run then does is news,
    # including a first transition the runner wrote before this loop's first read.
    statuses = (floor or {}).get("statuses") if has_floor else None
    deadline = None if deadline_seconds is None else clock() + deadline_seconds

    stream("following: %s" % store.dir)

    def announce(text):
        """One phase event: a line, and a notification when the operator asked for them. Both
        come from one call so the printed line and the notification cannot drift."""
        stream(text)
        if notifier is not None:
            notifier(text)

    def emit(index):
        """Drain one candidate and print what it produced, with its phase header the first time
        that file appears. The header is what tells one Task's output from the next (R9), and it
        is a phase event, so it survives `phases_only` while the decoded lines do not."""
        reader = readers[index]
        if not reader.active():
            return
        if index not in announced:
            announced.add(index)
            if not phases_only:
                stream("")
            announce("== %s %s ==" % (reader.task_id, reader.phase))
        if phases_only:
            return
        for raw in reader.drain():
            for event in decode(raw):
                stream(event)

    def poll_state():
        """One read of `state.json` per poll. `store.records()` and `store.terminal()` would load
        the file once each, and the records and the terminal record have to describe the same
        moment anyway."""
        return store.read() or {}

    def note_statuses(state):
        """Announce every record whose status moved since the last poll."""
        nonlocal statuses
        current = {task_id: record.get("status")
                   for task_id, record in (state.get("tasks") or {}).items()}
        if statuses is not None:
            for task_id in sorted(current):
                if current[task_id] != statuses.get(task_id):
                    announce("%s is now %s" % (task_id, current[task_id]))
        statuses = current

    def frontier():
        """The highest candidate this follower has seen output on, or -1 when there is none."""
        for index in range(len(readers) - 1, -1, -1):
            if readers[index].active():
                return index
        return -1

    def terminal_of(state):
        """The terminal record this run wrote, or None. A record identical to the one the floor
        captured belongs to the previous run against this state directory, so it is not this
        run's ending."""
        record = state.get("terminal")
        if record is None or (has_floor and record == terminal_floor):
            return None
        return record

    def finish(record):
        run_status = record.get("run_status")
        if record.get("halt_task"):
            announce("run %s on %s with class %s"
                     % (run_status, record.get("halt_task"), record.get("halt_class")))
        else:
            announce("run %s" % run_status)
        return run_status

    def drain_the_rest():
        for index in range(cursor, len(readers)):
            emit(index)
        note_statuses(poll_state())

    while True:
        edge = frontier()
        if edge < 0 and not waiting_said:
            waiting_said = True
            stream("waiting for the run to start")
        # Drain forward to the frontier. A candidate below the frontier belongs to a process that
        # has already exited, so one more drain takes the rest of it and the cursor can advance
        # past it. A candidate that never appeared is stepped over rather than waited on.
        while cursor < edge:
            emit(cursor)
            cursor += 1
        if cursor < len(readers):
            emit(cursor)
        state = poll_state()
        note_statuses(state)

        record = terminal_of(state)
        if record is not None:
            # Read after the drain, then drain again: the record and the last lines of the last
            # log are written by different processes, and this is what stops the record winning
            # the race and cutting the ending off.
            drain_the_rest()
            return finish(record)

        if runner_alive is not None and not runner_alive():
            # The process exited. Drain once more and read the record again before calling it a
            # silent death, because the exit and the record are the same race the read above
            # guards, in the other direction.
            drain_the_rest()
            record = terminal_of(poll_state())
            if record is not None:
                return finish(record)
            return GONE

        if deadline is not None and clock() >= deadline:
            return None
        sleep(poll_seconds)
