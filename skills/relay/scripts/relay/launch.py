"""Launcher (U6): run one `claude -p`, bound it, and leave nothing behind.

Four things here are not obvious and each one came from an observed failure.

The session id is chosen by the runner, not discovered afterwards (KTD7). `--session-id <uuid4>`
fixes the transcript path before launch, so classification reads a file the runner named rather
than guessing which of several sessions in a slug directory was its own.

The child gets `stdin=DEVNULL` and its own process group. A detached `claude -p` that inherits an
open pipe reads it until EOF and idles to the timeout; and without a new session, a timeout kill
reaches the process but not the subagents and gates it started, which then outlive the task.

The child's environment is scrubbed (KTD9): the manifest's tracker credential variables and every
`CLAUDECODE*` and `CLAUDE_CODE_*` marker are removed, so no task process, gate, or push ever sees
the operator's token or believes it is nested inside a session. The same environment is what the
gate, the closeout, and every push run under.

Stdout is drained by a reader thread rather than read in the main loop, and the lease heartbeat
runs on its own rescheduling timer (KTD10). A process can be silent for ten minutes inside one
long subagent call; neither the deadline check nor the lease may depend on it saying something.
"""
import glob
import os
import queue as queuemod
import signal
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass

from . import contracts, manifest as manifest_module

SIGKILL_GRACE_SECONDS = 15
TICK_SECONDS = 1.0
SCRUB_PREFIXES = ("CLAUDECODE", "CLAUDE_CODE_")


@dataclass
class LaunchResult:
    session_id: str
    exit_code: int | None = None
    timed_out: bool = False
    lease_lost: bool = False
    killed_group: bool = False
    wall_seconds: float = 0.0
    active_seconds: float = 0.0
    transcript_path: str | None = None
    transcript_present: bool = False
    log_path: str | None = None
    launch_error: str | None = None


def child_env(manifest, base_env=None, home=None):
    """The environment every child of the runner gets: the operator's, minus the tracker
    credentials and minus the markers that tell a CLI it is nested in a session."""
    env = dict(os.environ if base_env is None else base_env)
    for name in (manifest.tracker.token_env, manifest.tracker.email_env):
        env.pop(name, None)
    for name in list(env):
        if name.startswith(SCRUB_PREFIXES):
            env.pop(name, None)
    if home:
        env["HOME"] = home
    return env


def build_args(manifest, task, brief_text, session_id, allowed=None, disallowed=None):
    """The argument list, never a shell string (R9). The allowlist defaults to the manifest's
    and is overridden for the closeout, which gets a narrower one (U9). The disallow list is the
    manifest's plus every R10 variant validate filled in, for both."""
    resolved = manifest_module.resolved_disallowed(manifest)
    for extra in disallowed or ():
        if extra not in resolved:
            resolved.append(extra)
    disallowed = resolved
    allowed = manifest.permissions.allowed if allowed is None else allowed
    return [
        "claude", "-p", brief_text,
        "--session-id", session_id,
        "--model", task.model,
        "--effort", task.effort,
        "--permission-mode", contracts.PERMISSION_MODE,
        "--allowedTools", ",".join(allowed),
        "--disallowedTools", ",".join(disallowed),
        "--output-format", contracts.OUTPUT_FORMAT,
        "--verbose",
    ]


def find_transcript(home, cwd_realpath, session_id):
    """The predicted path, or the one the CLI actually used. The uuid is unique, so a glob over
    every slug directory is unambiguous when the prediction misses (KTD7)."""
    predicted = contracts.transcript_path(home, cwd_realpath, session_id)
    if os.path.exists(predicted):
        return predicted, True
    matches = sorted(glob.glob(os.path.join(home, ".claude", "projects", "*", session_id + ".jsonl")))
    if matches:
        return matches[0], True
    return predicted, False


class _Reader(threading.Thread):
    """Drains the child's stdout so a full pipe never blocks it, and so the main loop can tick
    on the deadline even while the child says nothing."""

    def __init__(self, stream, sink):
        super().__init__(daemon=True)
        self.stream = stream
        self.sink = sink

    def run(self):
        try:
            for line in self.stream:
                self.sink.put(line)
        except (ValueError, OSError):
            pass
        finally:
            self.sink.put(None)


class _Heartbeat:
    """A rescheduling timer, independent of the read loop. `beat()` returning False means the
    lease is no longer ours, which stops the run rather than letting it merge unprotected."""

    def __init__(self, beat, interval):
        self.beat = beat
        self.interval = interval
        self.lost = False
        self._timer = None
        self._stopped = threading.Event()

    def start(self):
        if self.beat is None or not self.interval:
            return
        self._schedule()

    def _schedule(self):
        if self._stopped.is_set():
            return
        self._timer = threading.Timer(self.interval, self._tick)
        self._timer.daemon = True
        self._timer.start()

    def _tick(self):
        if self._stopped.is_set():
            return
        try:
            if self.beat() is False:
                self.lost = True
                return
        except Exception:
            self.lost = True
            return
        self._schedule()

    def stop(self):
        self._stopped.set()
        if self._timer is not None:
            self._timer.cancel()


def _kill_group(proc, grace_seconds, pgid=None):
    """SIGTERM the whole group, then SIGKILL what is left. The pipe usually stays open until the
    SIGKILL because grandchildren inherited it, so nothing here waits on stdout.

    `pgid` must be the value captured at launch. Resolving it here instead would fail once the
    leader has been reaped, which is the common case: the direct process exits, a subagent or a
    gate keeps running, and the group is exactly what still needs killing."""
    if pgid is None:
        try:
            pgid = os.getpgid(proc.pid)
        except (ProcessLookupError, PermissionError, OSError):
            return False
    try:
        os.killpg(pgid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        return False
    try:
        proc.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        pass
    try:
        proc.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        pass
    return True


def launch(manifest, task, brief_text, log_path, timeout_seconds, session_id=None, home=None,
           base_env=None, heartbeat=None, heartbeat_interval=contracts.LEASE_HEARTBEAT_SECONDS,
           stream=print, sigkill_grace_seconds=SIGKILL_GRACE_SECONDS, popen=subprocess.Popen,
           on_release=None, allowed=None, disallowed=None):
    """Run one task or closeout process to completion, a timeout, or a lost lease.

    `active_seconds` is measured on the monotonic clock, which does not advance while the host
    sleeps, and `wall_seconds` on the wall clock. The deadline uses the monotonic one, so a
    laptop that slept for an hour does not consume the task's budget, and the summary can show
    both so a sleep is visible rather than mysterious.
    """
    repo = os.path.realpath(manifest.project.repo)
    session_id = session_id or str(uuid.uuid4())
    env = child_env(manifest, base_env, home)
    home = home or env.get("HOME") or os.path.expanduser("~")
    result = LaunchResult(session_id=session_id, log_path=log_path)

    os.makedirs(os.path.dirname(log_path) or ".", exist_ok=True)
    args = build_args(manifest, task, brief_text, session_id, allowed, disallowed)

    started_wall = time.time()
    started = time.monotonic()
    try:
        proc = popen(args, cwd=repo, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                     stderr=subprocess.STDOUT, text=True, bufsize=1, start_new_session=True)
    except OSError as exc:
        result.launch_error = "could not start %s: %s" % (args[0], exc)
        result.wall_seconds = time.time() - started_wall
        result.active_seconds = time.monotonic() - started
        result.transcript_path, result.transcript_present = find_transcript(home, repo, session_id)
        return result

    try:
        group_id = os.getpgid(proc.pid)
    except OSError:
        group_id = None

    lines = queuemod.Queue()
    reader = _Reader(proc.stdout, lines)
    reader.start()
    beat = _Heartbeat(heartbeat, heartbeat_interval)
    beat.start()

    previous = {}
    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            previous[signum] = signal.getsignal(signum)
        except ValueError:
            previous = {}
            break

    def handle(signum, frame):
        """R49 plus the System-Wide Impact note: the child is in its own session, so the
        operator's Ctrl+C never reaches it. The runner has to pass it on and release the lease."""
        _kill_group(proc, sigkill_grace_seconds, group_id)
        if on_release is not None:
            on_release()
        original = previous.get(signum)
        if callable(original):
            original(signum, frame)
        else:
            raise KeyboardInterrupt()

    installed = False
    try:
        for signum in previous:
            signal.signal(signum, handle)
        installed = True
    except ValueError:
        installed = False

    deadline = started + timeout_seconds
    try:
        with open(log_path, "a", encoding="utf-8") as log:
            done = False
            while True:
                try:
                    line = lines.get(timeout=TICK_SECONDS)
                except queuemod.Empty:
                    line = ""
                else:
                    if line is None:
                        done = True
                    else:
                        log.write(line)
                        log.flush()
                        if stream is not None:
                            stream(line.rstrip("\n"))
                if beat.lost:
                    result.lease_lost = True
                    result.killed_group = _kill_group(proc, sigkill_grace_seconds, group_id)
                    break
                if time.monotonic() >= deadline:
                    # Not conditioned on the child still running. A grandchild that inherited
                    # stdout keeps the pipe open after its parent exits, so the reader never
                    # reaches EOF and this is the only bound left.
                    result.timed_out = True
                    result.killed_group = _kill_group(proc, sigkill_grace_seconds, group_id)
                    break
                if done and proc.poll() is not None:
                    break
                if done and proc.poll() is None:
                    try:
                        proc.wait(timeout=TICK_SECONDS)
                    except subprocess.TimeoutExpired:
                        continue
                    break
    finally:
        beat.stop()
        if installed:
            for signum, original in previous.items():
                try:
                    signal.signal(signum, original)
                except ValueError:
                    pass
        # Order matters. The reader thread may be blocked inside stdout, and closing a
        # buffered reader from this thread while that read holds its lock deadlocks. Wait for
        # the reader to finish first, which it does as soon as the last descendant holding the
        # inherited pipe is gone, and only then close. If a descendant survived the group kill,
        # leave the daemon thread be rather than hanging the runner on it.
        reader.join(timeout=max(1.0, float(sigkill_grace_seconds)))
        if not reader.is_alive():
            try:
                proc.stdout.close()
            except (OSError, ValueError):
                pass

    result.exit_code = proc.poll()
    result.active_seconds = time.monotonic() - started
    result.wall_seconds = time.time() - started_wall
    result.transcript_path, result.transcript_present = find_transcript(home, repo, session_id)
    return result
