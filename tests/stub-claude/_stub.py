"""Shared machinery for Relay's stub CLIs (Backends U12). Never calls a model.

`claude`, `codex`, and `grok` are thin binaries: each owns only its own flag grammar, its own
evidence write location, and its own `--version`/`plugin list` output. Everything else, the
queue protocol, the sleep and orphan-child knobs, and the `git.sh` hook, lives here so the three
binaries cannot independently drift on it.

Queue protocol (RELAY_STUB_QUEUE names a directory):
  <queue>/<n>/entry.json   {"fixture": "<path>", "stream": "<path>", "exit": 0, "sleep": 0}
  <queue>/<n>/git.sh       optional; run with bash in the cwd after the fixture is written
  <queue>/counter          advanced under flock, so one runner drives task, closeout, task ...
Entries are consumed in numeric order of <n>, shared across every binary pointed at the same
queue directory. A fixture or stream path is absolute or relative to the queue directory. With
no queue, `main()` calls `write_evidence` with an empty entry and writes nothing further.

Other knobs, read by `main()` itself:
  RELAY_STUB_SLEEP=<seconds>       overrides the entry's sleep (U6 timeout tests)
  RELAY_STUB_CHILD=1               spawns one sleeping child so a group kill test has an orphan
"""
import fcntl
import json
import os
import subprocess
import sys
import time


def ensure_relay_on_path():
    """Puts skills/relay/scripts on sys.path so a stub can `from relay import ...` the same
    contracts and backends modules the runner itself reads, rather than a second hardcoded
    copy. Idempotent; codex and grok both call this, claude does not (its own defaults are a
    deliberately unchanged literal, not a pin lookup)."""
    scripts_dir = os.path.normpath(os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "..", "skills", "relay", "scripts"))
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)


def emit(obj):
    print(json.dumps(obj), flush=True)


def maybe_spawn_child():
    """A sleeping grandchild that outlives this process, so a group-kill test has an orphan to
    reap. Left running on purpose; the caller does not wait for it."""
    if os.environ.get("RELAY_STUB_CHILD") != "1":
        return None
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
    emit({"type": "system", "subtype": "stub_child", "pid": child.pid})
    return child


def next_entry(queue):
    """Take the next queue entry under flock and return its directory, or None when spent."""
    counter_path = os.path.join(queue, "counter")
    with open(counter_path, "a+") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        handle.seek(0)
        text = handle.read().strip()
        taken = int(text) if text else 0
        entries = sorted(
            (int(name) for name in os.listdir(queue) if name.isdigit()),
        )
        if taken >= len(entries):
            fcntl.flock(handle, fcntl.LOCK_UN)
            return None
        handle.seek(0)
        handle.truncate()
        handle.write(str(taken + 1))
        handle.flush()
        fcntl.flock(handle, fcntl.LOCK_UN)
    return os.path.join(queue, str(entries[taken]))


def load_entry(entry_dir):
    with open(os.path.join(entry_dir, "entry.json")) as handle:
        return json.load(handle)


def maybe_sleep(entry):
    sleep_seconds = float(os.environ.get("RELAY_STUB_SLEEP", entry.get("sleep", 0)))
    if sleep_seconds:
        time.sleep(sleep_seconds)


def resolve(queue, path):
    """A fixture or stream path, absolute or relative to the queue directory."""
    return path if os.path.isabs(path) else os.path.join(queue, path)


def echo_stream(entry, queue):
    """Echo a queued `stream` file to stdout unchanged. This is what the runner captures into
    the log the classifier and `relay tail` read; a backend whose evidence lives partly in that
    log (Codex's tool events, Grok's token stream) relies on this, not on `write_evidence`."""
    stream = entry.get("stream")
    if not stream:
        return
    with open(resolve(queue, stream), encoding="utf-8") as handle:
        for line in handle:
            line = line.rstrip("\n")
            if line:
                print(line, flush=True)


def run_git_hook(entry_dir):
    """Run `git.sh` if the entry carries one. Returns an exit code to propagate, or None to
    continue."""
    script = os.path.join(entry_dir, "git.sh")
    if not os.path.exists(script):
        return None
    proc = subprocess.run(["bash", script], capture_output=True, text=True)
    emit({"type": "system", "subtype": "stub_git", "exit": proc.returncode,
          "stderr": proc.stderr[-500:]})
    if proc.returncode != 0:
        return 98
    return None


def main(session_id, write_evidence):
    """The shared body every thin binary's `main()` calls once its own flag parse and
    `--version`/`plugin list` branches have already returned. `write_evidence(entry, queue)` is
    the one backend-specific step: where the queued fixture actually lands. It receives an empty
    dict and `queue=None` when no queue is configured, and must no-op in that case."""
    emit({"type": "system", "subtype": "stub_start", "session_id": session_id})
    maybe_spawn_child()

    queue = os.environ.get("RELAY_STUB_QUEUE")
    entry = {}
    entry_dir = None
    if queue:
        entry_dir = next_entry(queue)
        if entry_dir is None:
            emit({"type": "system", "subtype": "stub_queue_spent"})
            return 97
        entry = load_entry(entry_dir)

    maybe_sleep(entry)
    write_evidence(entry, queue)
    echo_stream(entry, queue)

    if entry_dir is not None:
        code = run_git_hook(entry_dir)
        if code is not None:
            return code

    emit({"type": "result", "subtype": "stub_done", "session_id": session_id})
    return int(entry.get("exit", 0))
