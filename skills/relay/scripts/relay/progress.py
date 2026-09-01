"""The progress view (issue #44): how far along a run is, and roughly how much is left.

`status` answered where a run was, as a cursor of N of M plus one line per task. It never
answered how long that had taken or how much remained, which is what an operator who launched a
run and walked away actually wants to know.

Shaped after `summary.py` deliberately: a `build` that returns data and a `lines` that renders
text from it, never the other way round, so the two read alike and the text can never claim
something the data does not carry. It is a separate module rather than more of `summary.py`
because that module's JSON is a versioned contract other readers parse, and because the
arithmetic here needs an injectable clock to be testable at all.

Three of its rules are judgment rather than arithmetic, and each exists because the honest number
is not the obvious one.

A record that cannot support an elapsed reports none rather than a plausible wrong one. A halted
record left behind by a reclaimed crash has a start and no ending, and counting it to now would
report the age of the crash as work.

The total is a sum of per task elapsed, not a stopwatch, and the line says so. The runner is
serial, so the sum is the run's working time, and it composes across a resumed run where the state
directory holds more than one run's records. It excludes what the runner does between tasks, and
on a resumed run it takes in the earlier run's landed tasks, so a label reading like elapsed since
the run started would be wrong in two directions at once.

The estimate is gated on the size of its sample, not on whether anything reads landed. A task an
operator finished by hand is promoted to landed by `startup_reverify` without ever entering
`running`, and a record written before the stamps existed carries none either. Both read landed
with nothing to average.
"""
import time

from . import contracts, state

# A manifest task with no record, or a record still pending. Not a record status: nothing writes
# `todo` to state, and the point of the bucket is to count what has not started yet.
TODO = "todo"


def _elapsed(record, now):
    """One record's elapsed seconds, or None when its stamps cannot support a number.

    The finished branch is tried first and the live branch only when there is no ending, which is
    what makes a retried task readable: U1 clears `ended_at` on the way back into `running`
    precisely so this falls through to the live branch rather than differencing two attempts.
    """
    started = state._epoch(record.get("started_at"))
    ended = state._epoch(record.get("ended_at"))
    if started is None:
        return None
    if ended is not None:
        return ended - started if ended >= started else None
    if record.get("status") in contracts.IN_FLIGHT_STATUSES:
        return max(0.0, now - started)
    return None


def _entry(task_id, record, now, in_manifest):
    """One line's worth of facts. A record the manifest no longer names carries no elapsed: it
    belongs to a different run's list, so a duration beside it would read as this run's."""
    if record is None:
        return {"id": task_id, "status": TODO, "elapsed_seconds": None, "in_manifest": True}
    status = record.get("status")
    return {
        "id": task_id,
        "status": TODO if status == contracts.STATUS_PENDING else status,
        "elapsed_seconds": _elapsed(record, now) if in_manifest else None,
        "in_manifest": in_manifest,
    }


def build(manifest, store, now=time.time, raw=None):
    """The progress view as data. Reads state only; acquires nothing and changes nothing.

    `raw` lets a caller that has already read `state.json` hand that read in, so `status` does not
    describe two different moments of a live run in one screen of output.
    """
    if raw is None:
        raw = store.read() or {}
    records = dict(raw.get("tasks") or {})
    at = now()

    order = [task.id for task in manifest.tasks]
    named = set(order)
    entries = [_entry(task_id, records.get(task_id), at, True) for task_id in order]
    # Same ordering `summary.build` already uses for the same inputs: the manifest's own list,
    # then whatever the state directory is still holding from a run of some other list.
    entries += [_entry(task_id, records[task_id], at, False)
                for task_id in sorted(records) if task_id not in named]

    mine = [entry for entry in entries if entry["in_manifest"]]
    counts = {}
    for entry in mine:
        counts[entry["status"]] = counts.get(entry["status"], 0) + 1

    measured = [entry["elapsed_seconds"] for entry in mine
                if entry["elapsed_seconds"] is not None]
    landed = [entry["elapsed_seconds"] for entry in mine
              if entry["status"] == contracts.STATUS_LANDED
              and entry["elapsed_seconds"] is not None]

    return {
        "tasks": entries,
        "counts": counts,
        "total_seconds": sum(measured) if measured else None,
        "landed_sample": len(landed),
        "estimate_seconds": _estimate(mine, landed),
    }


def _estimate(entries, landed):
    """Mean landed elapsed times the todo count, plus what the task in flight has left against
    that mean. Rough by construction and labelled as such where it prints.

    The task in flight is counted once, in its own term. A halted or excluded task is not counted
    at all: neither is remaining work this run will do, one needs a hand repair and the other was
    never going to run.
    """
    if not landed:
        return None
    mean = sum(landed) / len(landed)
    todo = sum(1 for entry in entries if entry["status"] == TODO)
    remaining = mean * todo
    for entry in entries:
        if entry["status"] in contracts.IN_FLIGHT_STATUSES:
            remaining += max(0.0, mean - (entry["elapsed_seconds"] or 0))
    return remaining


def duration(seconds):
    """One duration, at the magnitude a reader cares about. Seconds below a minute, minutes and
    seconds below an hour, hours and minutes above, because a five hour run reporting its seconds
    is noise and a forty second task reporting 0h 0m says nothing."""
    if seconds is None:
        return None
    seconds = int(seconds)
    if seconds < 60:
        return "%ds" % seconds
    if seconds < 3600:
        return "%dm %ds" % divmod(seconds, 60)
    hours, rest = divmod(seconds, 3600)
    return "%dh %dm" % (hours, rest // 60)


def lines(data):
    """The text form. Each line is one question an operator asks `status` to answer."""
    counts = data["counts"]
    tally = ", ".join("%d %s" % (counts[status], status) for status in sorted(counts))
    out = ["progress: %s" % (tally or "nothing recorded yet")]
    total = duration(data["total_seconds"])
    if total is not None:
        out.append("elapsed: %s across %d task(s)" % (total, len(
            [e for e in data["tasks"] if e["in_manifest"] and e["elapsed_seconds"] is not None])))
    if data["estimate_seconds"] is None:
        out.append("remaining: no estimate yet, no landed task carries a duration")
    else:
        out.append("remaining: roughly %s, from the mean of %d landed task(s)"
                   % (duration(data["estimate_seconds"]), data["landed_sample"]))
    return out


def task_line(entry):
    """One task's line, for a caller that prints the per task block itself. Returns the status
    and the elapsed only; the caller owns the id and any marker of its own."""
    elapsed = duration(entry["elapsed_seconds"])
    return entry["status"] if elapsed is None else "%s  %s" % (entry["status"], elapsed)
