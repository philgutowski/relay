"""State store (U3): one JSON file per manifest, a lease, and atomic writes under a lock.

Where it lives (KTD3): `~/.relay/<sha256(realpath(manifest))>/` or `$XDG_STATE_HOME/relay/...`
when that variable is set. The hash of the real path means a symlinked or relocated manifest
keeps its state, and two manifests never share a directory. A second, repo level lease at
`~/.relay/repos/<sha256(realpath(repo))>.lock` stops two manifests naming one repo from
interleaving merges (R31).

Two different locks do two different jobs, the same split ce-sweep uses. The **lease** is a
record inside the state file saying which runner owns this manifest's run; it is renewed by a
heartbeat and reclaimed when stale. The **file lock** (`flock` on `state.lock`, an advisory lock
the OS hands to one process at a time) serializes the physical read-modify-write, so a `status`
verb reading while the runner writes never sees a torn file. Every write goes to a temp file in
the same directory and is renamed over `state.json`, because rename is atomic on one filesystem:
a crash between write and rename leaves the previous valid state.
"""
import fcntl
import hashlib
import json
import os
import socket
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from . import classify, contracts

OK = "OK"
LOCKED = "LOCKED"
STALE_RECLAIMED = "STALE_RECLAIMED"

RECORD_FIELDS = (
    "status", "baseline_sha", "baseline_tracker_status", "baseline_comment_id", "session_id",
    "branch", "landing_ref", "verify", "halt_class", "halt_evidence", "findings", "closeout",
    "started_at", "ended_at", "wall_seconds", "active_seconds", "transcript_path", "brief_sha256",
    "excluded_reason", "continued_past", "backend", "binary_path", "args",
)


@dataclass
class AcquireResult:
    code: str
    holder: dict | None = None
    age_seconds: float | None = None
    other_manifest: str | None = None
    reclaimed_ids: tuple = ()
    previous_holder: dict | None = None
    repo_lease_reclaimed_from: dict | None = None

    @property
    def ok(self):
        return self.code in (OK, STALE_RECLAIMED)


def _iso(epoch):
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


def _epoch(iso):
    try:
        return datetime.fromisoformat(iso).timestamp()
    except (TypeError, ValueError):
        return None


def sha256_of(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def base_dir(home=None):
    xdg = os.environ.get("XDG_STATE_HOME")
    if xdg:
        return os.path.join(xdg, "relay")
    return os.path.join(home or os.path.expanduser("~"), ".relay")


def new_record(task_id):
    """A pending record with every field from RECORD_FIELDS present, so readers never key
    error on a record that has not reached a later stage."""
    record = {field: None for field in RECORD_FIELDS}
    record.update(id=task_id, status=contracts.STATUS_PENDING, findings=[])
    return record


class StateStore:
    def __init__(self, manifest_path, repo_path, home=None, now=time.time, pid=None, hostname=None,
                 ttl_seconds=contracts.LEASE_TTL_SECONDS):
        self.manifest_path = os.path.realpath(manifest_path)
        self.repo_path = os.path.realpath(repo_path)
        self.now = now
        self.pid = pid if pid is not None else os.getpid()
        self.hostname = hostname or socket.gethostname()
        self.ttl_seconds = ttl_seconds
        base = base_dir(home)
        self.dir = os.path.join(base, sha256_of(self.manifest_path))
        self.repo_lock_path = os.path.join(base, "repos", sha256_of(self.repo_path) + ".lock")
        self.state_path = os.path.join(self.dir, "state.json")
        self.lock_path = os.path.join(self.dir, "state.lock")
        self._abort_after_write = None
        self._ensure_dirs()

    # Directory layout and permissions. Logs carry tracker text, so 0700 and 0600.
    def _ensure_dirs(self):
        for sub in ("", "logs", "briefs", "digests", "gate"):
            path = os.path.join(self.dir, sub)
            os.makedirs(path, mode=0o700, exist_ok=True)
            os.chmod(path, 0o700)
        os.makedirs(os.path.dirname(self.repo_lock_path), mode=0o700, exist_ok=True)

    def path(self, *parts):
        return os.path.join(self.dir, *parts)

    # Raw file access.
    def _empty(self):
        return {
            "schema_version": contracts.STATE_SCHEMA_VERSION,
            "manifest": self.manifest_path,
            "repo": self.repo_path,
            "lease": None,
            "cursor": 0,
            "tasks": {},
            "terminal": None,
            "git_ops": [],
        }

    def read(self):
        """The current state, or None when no state file exists yet."""
        if not os.path.exists(self.state_path):
            return None
        with open(self.state_path, encoding="utf-8") as handle:
            state = json.load(handle)
        return self._normalize_legacy_state(state)

    @staticmethod
    def _version_map(value):
        """Accept U10's scalar terminal evidence while every new record is backend-keyed."""
        if isinstance(value, dict):
            return dict(value)
        return {"claude": value} if value is not None else {}

    def _normalize_legacy_state(self, state):
        """Present pre-U11 state in the current reader shape without rewriting it on read."""
        if not isinstance(state, dict):
            return state
        terminal = state.get("terminal")
        if isinstance(terminal, dict):
            terminal["cli_version"] = self._version_map(terminal.get("cli_version"))
            terminal["cli_version_observed"] = self._version_map(
                terminal.get("cli_version_observed"))
        for record in (state.get("tasks") or {}).values():
            if isinstance(record, dict) and not record.get("backend"):
                # Before pluggable backends every task was Claude, so this is evidence, not a
                # guess. It also keeps a resumed task on the CLI that produced its first run.
                record["backend"] = "claude"
        return state

    def _write_locked(self, state):
        tmp = self.state_path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(state, handle, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        if self._abort_after_write is not None:
            self._abort_after_write()
        os.replace(tmp, self.state_path)

    def _mutate(self, fn):
        """Load, apply fn(state), write atomically, all under one flock. Opens the lock file
        fresh each call and releases by closing it, so one process never holds two descriptors
        on the lock (a second descriptor on the same file would not block the first)."""
        fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            state = self.read() or self._empty()
            # A legacy file is upgraded only when a normal mutation already makes it durable;
            # opening a state directory remains strictly read-only.
            if state.get("schema_version", 0) < contracts.STATE_SCHEMA_VERSION:
                state["schema_version"] = contracts.STATE_SCHEMA_VERSION
            result = fn(state)
            self._write_locked(state)
            return result
        finally:
            os.close(fd)

    # Lease.
    def _holder(self):
        return {"holder_pid": self.pid, "hostname": self.hostname, "manifest": self.manifest_path}

    def _is_mine(self, lease):
        return bool(lease) and lease.get("holder_pid") == self.pid and lease.get("hostname") == self.hostname

    def _age(self, lease):
        stamped = _epoch(lease.get("heartbeat_at")) if lease else None
        if stamped is None:
            return None
        return self.now() - stamped

    def _is_live(self, lease):
        """A lease is live while its heartbeat is younger than its TTL. An unparseable stamp
        counts as live, so a hand edited file is never stomped (the ce-sweep rule)."""
        if not lease:
            return False
        age = self._age(lease)
        if age is None:
            return True
        return age < float(lease.get("ttl_seconds", self.ttl_seconds))

    def _read_repo_lock(self):
        if not os.path.exists(self.repo_lock_path):
            return None
        try:
            with open(self.repo_lock_path, encoding="utf-8") as handle:
                return json.load(handle)
        except (OSError, ValueError):
            return None

    def _write_repo_lock(self, payload):
        tmp = self.repo_lock_path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
        os.replace(tmp, self.repo_lock_path)

    def acquire(self):
        """Take the manifest lease and the repo lease. Returns an AcquireResult whose code is
        OK, LOCKED (a live holder, named), or STALE_RECLAIMED (an expired lease taken over,
        in flight records marked runner_crashed per R55, their ids returned)."""
        outcome = {}

        def fn(state):
            lease = state.get("lease")
            now = self.now()
            if lease and not self._is_mine(lease) and self._is_live(lease):
                outcome["result"] = AcquireResult(LOCKED, holder=dict(lease), age_seconds=self._age(lease))
                return
            reclaimed = ()
            previous = None
            if lease and not self._is_mine(lease):
                previous = dict(lease)
                reclaimed = self._mark_crashed(state, previous)
            state["lease"] = dict(
                self._holder(), acquired_at=_iso(now), heartbeat_at=_iso(now), ttl_seconds=self.ttl_seconds
            )
            outcome["result"] = AcquireResult(
                STALE_RECLAIMED if previous else OK, holder=dict(state["lease"]),
                reclaimed_ids=reclaimed, previous_holder=previous,
            )

        self._mutate(fn)
        result = outcome["result"]
        if result.code == LOCKED:
            return result
        repo_result = self._acquire_repo_lock()
        if isinstance(repo_result, AcquireResult):
            self._mutate(lambda state: state.update(lease=None))
            return repo_result
        result.repo_lease_reclaimed_from = repo_result
        return result

    def _acquire_repo_lock(self):
        """Returns an AcquireResult(LOCKED) when another manifest holds a live repo lease;
        otherwise takes it and returns the stale lease it displaced (None when it was free)."""
        fd = os.open(self.repo_lock_path + ".flock", os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            current = self._read_repo_lock()
            if current and current.get("manifest") != self.manifest_path and self._is_live(current):
                return AcquireResult(
                    LOCKED, holder=dict(current), age_seconds=self._age(current),
                    other_manifest=current.get("manifest"),
                )
            displaced = dict(current) if current and current.get("manifest") != self.manifest_path else None
            now = self.now()
            self._write_repo_lock(dict(
                self._holder(), acquired_at=_iso(now), heartbeat_at=_iso(now), ttl_seconds=self.ttl_seconds
            ))
            return displaced
        finally:
            os.close(fd)

    def _mark_crashed(self, state, previous):
        """R55: a reclaimed lease turns every running or merging record into halted with class
        runner_crashed, and records the old holder in the terminal record as crashed.

        Round six #40: when the crashed task's own stdout log shows it killed the previous
        holder's pid, that self-kill is attached to the record as a runner_self_kill finding
        instead of leaving the halt bare (classify.scan_self_kill)."""
        ids = []
        victim_pid = previous.get("holder_pid")
        for task_id, record in state.get("tasks", {}).items():
            if record.get("status") in contracts.IN_FLIGHT_STATUSES:
                record["halt_evidence"] = {
                    "status_before": record.get("status"),
                    "previous_holder": previous,
                    "last_git_op": (state.get("git_ops") or [None])[-1],
                }
                record["status"] = contracts.STATUS_HALTED
                record["halt_class"] = contracts.HALT_RUNNER_CRASHED
                if victim_pid is not None:
                    finding = classify.scan_self_kill(
                        self.path("logs", task_id + ".stdout.log"), victim_pid)
                    if finding is not None:
                        record.setdefault("findings", []).append(finding)
                ids.append(task_id)
        terminal = state.get("terminal")
        lease_started = _epoch(previous.get("acquired_at")) or 0
        written = _epoch((terminal or {}).get("written_at")) or -1
        if terminal is None or written < lease_started:
            state["terminal"] = {
                "run_status": contracts.RUN_CRASHED,
                "halt_task": ids[0] if ids else None,
                "halt_class": contracts.HALT_RUNNER_CRASHED if ids else None,
                "cli_version": {},
                "cli_version_observed": {},
                "previous_holder": previous,
                "written_at": _iso(self.now()),
            }
        return tuple(ids)

    def heartbeat(self):
        """Re-stamp both leases; only when this process holds both. Returns False when either
        lease is no longer ours, including a repo lease another manifest reclaimed while this
        runner was busy, so the run loop halts instead of merging without the repo lease."""
        stamped = {"ok": False}

        def fn(state):
            lease = state.get("lease")
            if self._is_mine(lease):
                lease["heartbeat_at"] = _iso(self.now())
                stamped["ok"] = True

        self._mutate(fn)
        if not stamped["ok"]:
            return False
        fd = os.open(self.repo_lock_path + ".flock", os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            current = self._read_repo_lock()
            if not current or not self._is_mine(current) or current.get("manifest") != self.manifest_path:
                return False
            current["heartbeat_at"] = _iso(self.now())
            self._write_repo_lock(current)
        finally:
            os.close(fd)
        return True

    def release(self):
        """Clear both leases when held by this process. Never touches another holder's lease."""
        released = {"ok": False}

        def fn(state):
            if self._is_mine(state.get("lease")):
                state["lease"] = None
                released["ok"] = True

        self._mutate(fn)
        current = self._read_repo_lock()
        if current and current.get("manifest") == self.manifest_path and current.get("holder_pid") == self.pid:
            try:
                os.remove(self.repo_lock_path)
            except FileNotFoundError:
                pass
        return released["ok"]

    def break_lease(self):
        """Operator only (`relay lease --break`): clear both leases regardless of holder."""
        self._mutate(lambda state: state.update(lease=None))
        try:
            os.remove(self.repo_lock_path)
        except FileNotFoundError:
            pass

    def lease(self):
        state = self.read()
        return (state or {}).get("lease")

    # Records.
    def validate(self):
        """The ce-sweep rule (R33): a landed record missing its landing_ref or verify.at goes
        back to pending. Returns the downgraded ids."""
        downgraded = []

        def fn(state):
            for task_id, record in state.get("tasks", {}).items():
                if record.get("status") != contracts.STATUS_LANDED:
                    continue
                verify = record.get("verify") or {}
                if not record.get("landing_ref") or not verify.get("at"):
                    record["status"] = contracts.STATUS_PENDING
                    record["halt_evidence"] = {"downgraded": "landed record missing landing_ref or verify.at"}
                    downgraded.append(task_id)

        self._mutate(fn)
        return downgraded

    def upsert(self, task_id, **fields):
        """Id-keyed merge: replace only the keys given, keep every other field. Unknown keys
        are kept too, so a newer writer and an older reader can share a file."""
        out = {}

        def fn(state):
            record = state["tasks"].get(task_id) or new_record(task_id)
            record.update(fields)
            state["tasks"][task_id] = record
            out["record"] = dict(record)

        self._mutate(fn)
        return out["record"]

    def get(self, task_id):
        state = self.read() or {}
        return (state.get("tasks") or {}).get(task_id)

    def records(self):
        state = self.read() or {}
        return dict(state.get("tasks") or {})

    def set_cursor(self, index):
        self._mutate(lambda state: state.update(cursor=int(index)))

    def cursor(self):
        return (self.read() or {}).get("cursor", 0)

    def record_git_op(self, task_id, op, phase, detail=None):
        """One entry before (`intent`) and one after (`result`) each mutating git call, so a
        crash between them is a named state (System-Wide Impact)."""
        entry = {"task": task_id, "op": op, "phase": phase, "detail": detail, "at": _iso(self.now())}
        self._mutate(lambda state: state.setdefault("git_ops", []).append(entry))
        return entry

    # Terminal record.
    def write_terminal(self, run_status, halt_task=None, halt_class=None, cli_version=None,
                       cli_version_observed=None):
        record = {
            "run_status": run_status,
            "halt_task": halt_task,
            "halt_class": halt_class,
            "cli_version": self._version_map(cli_version),
            "cli_version_observed": self._version_map(cli_version_observed),
            "written_at": _iso(self.now()),
        }
        self._mutate(lambda state: state.update(terminal=record))
        return record

    def terminal(self):
        return (self.read() or {}).get("terminal")

    def status_word(self):
        """What `relay status` reports: `running` under a live lease, the terminal record's
        status when one was written after the last lease, else `crashed`."""
        state = self.read()
        if state is None:
            return "no_state"
        lease = state.get("lease")
        if self._is_live(lease):
            return "running"
        terminal = state.get("terminal")
        if terminal is None:
            return contracts.RUN_CRASHED if lease else "never_run"
        if lease and (_epoch(terminal.get("written_at")) or 0) < (_epoch(lease.get("acquired_at")) or 0):
            return contracts.RUN_CRASHED
        return terminal["run_status"]
