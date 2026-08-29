"""Launcher (U6): run one Task process, bound it, and leave nothing behind.

Four things here are not obvious and each one came from an observed failure.

The session id is chosen by the runner, not discovered afterwards (KTD7). `--session-id <uuid4>`
fixes the transcript path before launch, so classification reads a file the runner named rather
than guessing which of several sessions in a slug directory was its own.

The child gets `stdin=DEVNULL` and its own process group. A detached `claude -p` that inherits an
open pipe reads it until EOF and idles to the timeout; and without a new session, a timeout kill
reaches the process but not the subagents and gates it started, which then outlive the task.

The child's environment is scrubbed two different ways (KTD16). The run level copy, used by
git, the tracker adapters, and the version probes, keeps every backend's credentials and
removes the union of every backend's nesting markers. launch() then narrows the Task process's
copy to that backend's own credentials. Tracker tokens are removed in both copies.

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

from . import backends, contracts, manifest as manifest_module

SIGKILL_GRACE_SECONDS = 15
TICK_SECONDS = 1.0
CLI_VERSION_TIMEOUT_SECONDS = 10


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


ALWAYS_SCRUBBED = ("JIRA_API_TOKEN", "JIRA_EMAIL")


def child_env(manifest, base_env=None, home=None, backend=None):
    """The environment every child of the runner gets.

    Called two ways (KTD16). run() passes no backend, so the result keeps every backend's
    credentials and scrubs the union of every backend's nesting markers. launch() passes the
    Task's backend, and that copy then drops every other backend's credential prefixes.
    Markers are removed first because CLAUDE_ is a prefix of CLAUDE_CODE_, and the same
    overlap holds for CODEX_ and GROK_.
    """
    env = dict(os.environ if base_env is None else base_env)
    # The manifest's own credential names, and the Jira defaults regardless of adapter: a
    # GitHub or markdown manifest names no token, and the operator's shell may still carry one.
    for name in (manifest.tracker.token_env, manifest.tracker.email_env) + ALWAYS_SCRUBBED:
        if name:
            env.pop(name, None)
    markers = []
    for name in contracts.BACKEND_PINS:
        markers.extend(backends.build(name).CAPABILITY.nesting_markers)
    for name in list(env):
        if markers and name.startswith(tuple(markers)):
            env.pop(name, None)
    if backend:
        keep = backends.build(backend).CAPABILITY.credential_prefixes
        drop = []
        for name in contracts.BACKEND_PINS:
            if name == backend:
                continue
            drop.extend(backends.build(name).CAPABILITY.credential_prefixes)
        for name in list(env):
            if drop and name.startswith(tuple(drop)):
                if keep and name.startswith(keep):
                    continue
                env.pop(name, None)
    if home:
        env["HOME"] = home
    return env


def cli_version(env, run=subprocess.run, timeout=CLI_VERSION_TIMEOUT_SECONDS, backend="claude"):
    """The installed binary's own version for one backend. Returns None on any failure,
    a missing binary, a nonzero exit, a timeout, or output the backend cannot parse,
    rather than raising: this call runs after run() has already acquired the lease and
    before its try/finally, so any exception here would skip store.release() and strand
    the lease. cli_version()'s contract is to fail closed to None, never raise."""
    try:
        module = backends.build(backend)
        proc = run([module.CAPABILITY.binary, "--version"], capture_output=True, text=True,
                   env=env, timeout=timeout, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        # ValueError covers UnicodeDecodeError from text=True decoding a non-UTF-8 byte in the
        # binary's output, and backends.ConfigurationError, which subclasses ValueError.
        return None
    if proc.returncode != 0:
        return None
    try:
        return module.parse_version(proc.stdout)
    except Exception:
        return None


def _reject_forbidden(backend_name, capability, pieces, brief_text):
    """Refuse every forbidden spelling in the backend's tuple (KTD6). The brief is excluded
    because a Task's instructions may mention a mode the argv itself must never carry."""
    scanned = [item for item in pieces if item != brief_text]
    joined = " ".join(str(item) for item in scanned)
    for spelling in capability.forbidden_permission_modes:
        if spelling in joined:
            raise ValueError("backend %s forbids %s" % (backend_name, spelling))


def build_args(manifest, task, brief_text, session_id, allowed=None, disallowed=None,
               log_path=None, repo=None):
    """The argument list, never a shell string (R9). Delegates the flag grammar to the
    Task's backend. The allowlist defaults to the manifest's and is overridden for the
    closeout, which gets a narrower one (U9). The disallow list is the manifest's plus
    every R10 variant validate filled in, for both. A backend with no deny flag still
    resolves that list; it simply does not put it on the argv."""
    resolved = manifest_module.resolved_disallowed(manifest)
    for extra in disallowed or ():
        if extra not in resolved:
            resolved.append(extra)
    disallowed = resolved
    allowed = manifest.permissions.allowed if allowed is None else allowed
    module = backends.build(task.backend)
    repo = repo or os.path.realpath(manifest.project.repo)
    args = module.build_args(
        manifest, task, brief_text, session_id,
        allowed=allowed, disallowed=disallowed,
        log_path=log_path, repo=repo,
    )
    _reject_forbidden(task.backend, module.CAPABILITY, args, brief_text)
    _reject_forbidden(task.backend, module.CAPABILITY, list(allowed) + list(disallowed), None)
    return args


def find_transcript(home, cwd_realpath, session_id, backend=None, log_path=None):
    """The backend's predicted evidence path, or the one the CLI actually used.
    Claude's uuid is unique, so a glob over every slug directory is unambiguous when
    the prediction misses (outer loop KTD7). Codex and Grok name their files up front."""
    name = backend or "claude"
    module = backends.build(name)
    sources = module.evidence_sources(
        home=home, cwd=cwd_realpath, session_id=session_id, log_path=log_path,
    )
    predicted = sources[0] if sources else contracts.transcript_path(home, cwd_realpath, session_id)
    if os.path.exists(predicted):
        return predicted, True
    if name == "claude":
        matches = sorted(glob.glob(
            os.path.join(home, ".claude", "projects", "*", session_id + ".jsonl")))
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
    env = child_env(manifest, base_env, home, backend=task.backend)
    home = home or env.get("HOME") or os.path.expanduser("~")
    result = LaunchResult(session_id=session_id, log_path=log_path)

    os.makedirs(os.path.dirname(log_path) or ".", exist_ok=True)
    args = build_args(manifest, task, brief_text, session_id, allowed, disallowed,
                      log_path=log_path, repo=repo)

    started_wall = time.time()
    started = time.monotonic()
    try:
        proc = popen(args, cwd=repo, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                     stderr=subprocess.STDOUT, text=True, bufsize=1, start_new_session=True)
    except OSError as exc:
        result.launch_error = "could not start %s: %s" % (args[0], exc)
        result.wall_seconds = time.time() - started_wall
        result.active_seconds = time.monotonic() - started
        result.transcript_path, result.transcript_present = find_transcript(
            home, repo, session_id, backend=task.backend, log_path=log_path)
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
    result.transcript_path, result.transcript_present = find_transcript(
        home, repo, session_id, backend=task.backend, log_path=log_path)
    return result
