"""U3: the state file, its lease, the repo lease, atomic writes, and the validate rules."""
import fcntl
import json
import os
import stat
import tempfile
import unittest
import unittest.mock

import _paths
from relay import contracts, state as st


class FakeClock:
    def __init__(self, start=1_000_000.0):
        self.t = start

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


class StateCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = os.path.join(self.tmp.name, "home")
        self.repo = os.path.join(self.tmp.name, "repo")
        os.makedirs(self.home)
        os.makedirs(self.repo)
        self.manifest = os.path.join(self.tmp.name, "a.toml")
        open(self.manifest, "w").close()
        self.clock = FakeClock()
        os.environ.pop("XDG_STATE_HOME", None)

    def tearDown(self):
        self.tmp.cleanup()

    def store(self, manifest=None, pid=100, hostname="host-a", ttl=600, observer=None):
        return st.StateStore(manifest or self.manifest, self.repo, home=self.home, now=self.clock,
                             pid=pid, hostname=hostname, ttl_seconds=ttl, observer=observer)


class Acquire(StateCase):
    def test_first_acquire_creates_state_with_schema_and_lease(self):
        store = self.store()
        result = store.acquire()
        self.assertEqual(result.code, st.OK)
        with open(store.state_path) as handle:
            data = json.load(handle)
        self.assertEqual(data["schema_version"], contracts.STATE_SCHEMA_VERSION)
        self.assertEqual(data["lease"]["holder_pid"], 100)
        self.assertEqual(data["tasks"], {})
        self.assertTrue(os.path.exists(store.repo_lock_path))

    def test_live_lease_from_another_pid_is_locked_with_holder_and_age(self):
        self.store(pid=100).acquire()
        self.clock.advance(42)
        result = self.store(pid=200).acquire()
        self.assertEqual(result.code, st.LOCKED)
        self.assertEqual(result.holder["holder_pid"], 100)
        self.assertEqual(result.holder["hostname"], "host-a")
        self.assertAlmostEqual(result.age_seconds, 42)
        self.assertFalse(result.ok)

    def test_reacquire_by_the_same_holder_is_ok(self):
        store = self.store()
        store.acquire()
        self.assertEqual(store.acquire().code, st.OK)

    def test_two_manifests_naming_one_repo_lock_each_other(self):
        other_manifest = os.path.join(self.tmp.name, "b.toml")
        open(other_manifest, "w").close()
        first = self.store(pid=100)
        self.assertEqual(first.acquire().code, st.OK)
        second = self.store(manifest=other_manifest, pid=200)
        result = second.acquire()
        self.assertEqual(result.code, st.LOCKED)
        self.assertEqual(result.other_manifest, os.path.realpath(self.manifest))
        self.assertIsNone(second.lease(), "the manifest lease must be rolled back when the repo lease is held")
        first.release()
        self.assertEqual(second.acquire().code, st.OK)

    def test_stale_lease_is_reclaimed_without_writing_a_run_level_terminal_record(self):
        self.store(pid=100).acquire()
        self.clock.advance(601)
        result = self.store(pid=200).acquire()
        self.assertEqual(result.code, st.STALE_RECLAIMED)
        self.assertEqual(result.previous_holder["holder_pid"], 100)
        store = self.store(pid=200)
        self.assertIsNone(store.terminal(), "only run()'s own three call sites may write terminal")
        self.assertEqual(store.lease()["holder_pid"], 200)

    def test_stale_lease_reclaim_leaves_a_prior_terminal_record_untouched(self):
        store = self.store(pid=100)
        store.acquire()
        before = store.write_terminal(contracts.RUN_COMPLETED, halt_task=None, halt_class=None)
        self.clock.advance(601)
        result = self.store(pid=200).acquire()
        self.assertEqual(result.code, st.STALE_RECLAIMED)
        after = self.store(pid=200).terminal()
        self.assertEqual(after, before)

    def test_stale_lease_with_merging_record_marks_runner_crashed(self):
        store = self.store(pid=100)
        store.acquire()
        store.upsert("T-1", status=contracts.STATUS_LANDED, landing_ref="abc", verify={"at": "x"})
        store.upsert("T-2", status=contracts.STATUS_MERGING)
        store.record_git_op("T-2", "merge", "intent")
        self.clock.advance(601)
        result = self.store(pid=200).acquire()
        self.assertEqual(result.reclaimed_ids, ("T-2",))
        record = self.store(pid=200).get("T-2")
        self.assertEqual(record["status"], contracts.STATUS_HALTED)
        self.assertEqual(record["halt_class"], contracts.HALT_RUNNER_CRASHED)
        self.assertEqual(record["halt_evidence"]["status_before"], contracts.STATUS_MERGING)
        self.assertEqual(record["halt_evidence"]["last_git_op"]["op"], "merge")
        self.assertEqual(record["halt_evidence"]["previous_holder"]["holder_pid"], 100)
        self.assertEqual(self.store(pid=200).get("T-1")["status"], contracts.STATUS_LANDED)

    def test_stale_lease_reclaim_finds_the_task_s_own_kill_of_the_previous_holder(self):
        """Round six #40: task #40 killed the Runner (pid 100 here) with a Bash `kill -9`
        naming its own pid. The next runner's reclaim should surface that as a finding, not a
        bare crash."""
        store = self.store(pid=100)
        store.acquire()
        store.upsert("T-2", status=contracts.STATUS_RUNNING)
        log_path = store.path("logs", "T-2.stdout.log")
        with open(log_path, "w", encoding="utf-8") as handle:
            handle.write(json.dumps({"type": "assistant", "message": {"content": [
                {"type": "tool_use", "name": "Bash",
                 "input": {"command": "kill -9 57246 100 61800"}}]}}) + "\n")
        self.clock.advance(601)
        result = self.store(pid=200).acquire()
        self.assertEqual(result.reclaimed_ids, ("T-2",))
        record = self.store(pid=200).get("T-2")
        self.assertEqual(record["halt_class"], contracts.HALT_RUNNER_CRASHED)
        self.assertEqual(len(record["findings"]), 1)
        finding = record["findings"][0]
        self.assertEqual(finding["class"], contracts.RUNNER_SELF_KILL)
        self.assertEqual(finding["victim_pid"], "100")

    def test_stale_lease_reclaim_stays_a_bare_crash_when_the_log_does_not_name_the_holder(self):
        store = self.store(pid=100)
        store.acquire()
        store.upsert("T-2", status=contracts.STATUS_RUNNING)
        log_path = store.path("logs", "T-2.stdout.log")
        with open(log_path, "w", encoding="utf-8") as handle:
            handle.write(json.dumps({"type": "assistant", "message": {"content": [
                {"type": "tool_use", "name": "Bash", "input": {"command": "git status"}}]}}) + "\n")
        self.clock.advance(601)
        self.store(pid=200).acquire()
        record = self.store(pid=200).get("T-2")
        self.assertEqual(record["halt_class"], contracts.HALT_RUNNER_CRASHED)
        self.assertEqual(record["findings"], [])

    def test_stale_lease_reclaim_with_no_log_file_at_all_still_succeeds(self):
        """The crash may happen before the task's subprocess ever wrote a line."""
        store = self.store(pid=100)
        store.acquire()
        store.upsert("T-2", status=contracts.STATUS_RUNNING)
        self.clock.advance(601)
        result = self.store(pid=200).acquire()
        self.assertEqual(result.reclaimed_ids, ("T-2",))
        record = self.store(pid=200).get("T-2")
        self.assertEqual(record["halt_class"], contracts.HALT_RUNNER_CRASHED)
        self.assertEqual(record["findings"], [])

    def test_stale_lease_reclaim_survives_a_scan_self_kill_that_raises(self):
        """Code review: `_mark_crashed` runs inside `_mutate`'s flock. A crashed task's log is
        untrusted input the runner never validated (e.g. pathologically deep JSON nesting raises
        `RecursionError`, which is neither `OSError` nor `ValueError` and was not caught anywhere
        on the read path). An uncaught exception here would skip `_write_locked` and leave the
        same stale lease behind for every future `acquire()` to hit again. Force the exception
        directly rather than trusting the guard by inspection -- a guard whose precondition is
        never exercised proves nothing."""
        store = self.store(pid=100)
        store.acquire()
        store.upsert("T-2", status=contracts.STATUS_RUNNING)
        log_path = store.path("logs", "T-2.stdout.log")
        with open(log_path, "w", encoding="utf-8") as handle:
            handle.write("{}\n")
        self.clock.advance(601)
        reclaiming = self.store(pid=200)
        with unittest.mock.patch(
                "relay.classify.scan_self_kill", side_effect=RecursionError("too deep")):
            result = reclaiming.acquire()
        self.assertEqual(result.code, st.STALE_RECLAIMED)
        self.assertEqual(result.reclaimed_ids, ("T-2",))
        record = self.store(pid=200).get("T-2")
        self.assertEqual(record["halt_class"], contracts.HALT_RUNNER_CRASHED)
        self.assertEqual(record["findings"], [])

    def test_unparseable_heartbeat_counts_as_live(self):
        store = self.store(pid=100)
        store.acquire()
        data = store.read()
        data["lease"]["heartbeat_at"] = "not a time"
        with open(store.state_path, "w") as handle:
            json.dump(data, handle)
        self.clock.advance(10_000)
        self.assertEqual(self.store(pid=200).acquire().code, st.LOCKED)


class Heartbeat(StateCase):
    def test_heartbeat_restamps_only_for_the_holder(self):
        store = self.store(pid=100)
        store.acquire()
        self.clock.advance(300)
        self.assertTrue(store.heartbeat())
        self.assertFalse(self.store(pid=200).heartbeat())
        self.clock.advance(400)
        # 700 seconds since acquire but 400 since heartbeat: still live.
        self.assertEqual(self.store(pid=200).acquire().code, st.LOCKED)
        self.assertEqual(store.status_word(), "running")

    def test_heartbeat_fails_after_another_manifest_reclaims_the_repo_lease(self):
        other_manifest = os.path.join(self.tmp.name, "b.toml")
        open(other_manifest, "w").close()
        first = self.store(pid=100)
        first.acquire()
        self.clock.advance(601)
        second = self.store(manifest=other_manifest, pid=200)
        result = second.acquire()
        self.assertEqual(result.code, st.OK, "manifest b has no lease of its own")
        self.assertEqual(result.repo_lease_reclaimed_from["holder_pid"], 100)
        self.assertFalse(first.heartbeat(), "the old holder must learn it lost the repo lease")
        self.assertEqual(second._read_repo_lock()["holder_pid"], 200)
        self.assertTrue(second.heartbeat())

    def test_release_clears_both_leases_for_the_holder_only(self):
        store = self.store(pid=100)
        store.acquire()
        self.assertFalse(self.store(pid=200).release())
        self.assertIsNotNone(store.lease())
        self.assertTrue(store.release())
        self.assertIsNone(store.lease())
        self.assertFalse(os.path.exists(store.repo_lock_path))

    def test_break_lease_clears_regardless_of_holder(self):
        self.store(pid=100).acquire()
        self.store(pid=200).break_lease()
        self.assertEqual(self.store(pid=200).acquire().code, st.OK)


class Records(StateCase):
    def test_validate_downgrades_landed_without_verify_at(self):
        store = self.store()
        store.acquire()
        store.upsert("T-1", status=contracts.STATUS_LANDED, landing_ref="abc", verify={"at": "2026-08-25T00:00:00+00:00"})
        store.upsert("T-2", status=contracts.STATUS_LANDED, landing_ref="def", verify={"checks": {}})
        store.upsert("T-3", status=contracts.STATUS_LANDED, landing_ref=None, verify={"at": "x"})
        self.assertEqual(store.validate(), ["T-2", "T-3"])
        self.assertEqual(store.get("T-1")["status"], contracts.STATUS_LANDED)
        self.assertEqual(store.get("T-2")["status"], contracts.STATUS_PENDING)
        self.assertEqual(store.get("T-3")["status"], contracts.STATUS_PENDING)

    def test_two_writers_upserting_different_keys_both_survive(self):
        store = self.store()
        store.acquire()
        store.upsert("T-1", baseline_sha="aaa")
        self.store(pid=100).upsert("T-1", session_id="sid", custom_key="kept")
        record = store.get("T-1")
        self.assertEqual(record["baseline_sha"], "aaa")
        self.assertEqual(record["session_id"], "sid")
        self.assertEqual(record["custom_key"], "kept")
        self.assertEqual(record["status"], contracts.STATUS_PENDING)

    def test_abort_between_write_and_rename_leaves_previous_state(self):
        store = self.store()
        store.acquire()
        store.upsert("T-1", baseline_sha="aaa")

        def boom():
            raise RuntimeError("simulated crash")

        store._abort_after_write = boom
        with self.assertRaises(RuntimeError):
            store.upsert("T-1", baseline_sha="bbb")
        store._abort_after_write = None
        self.assertEqual(store.get("T-1")["baseline_sha"], "aaa")
        with open(store.state_path) as handle:
            self.assertEqual(json.load(handle)["schema_version"], contracts.STATE_SCHEMA_VERSION)

    def test_cursor_and_git_ops(self):
        store = self.store()
        store.acquire()
        store.set_cursor(2)
        self.assertEqual(store.cursor(), 2)
        store.record_git_op("T-1", "push", "intent", {"remote": "origin"})
        store.record_git_op("T-1", "push", "result", {"exit": 0})
        ops = store.read()["git_ops"]
        self.assertEqual([o["phase"] for o in ops], ["intent", "result"])


class Stamps(StateCase):
    """R12: `started_at` and `ended_at`, written by the transition rule in `_mutate`."""

    def test_entering_running_stamps_started_at_and_nothing_else(self):
        store = self.store()
        store.upsert("T-1", status=contracts.STATUS_RUNNING)
        record = store.get("T-1")
        self.assertEqual(st._epoch(record["started_at"]), self.clock.t)
        self.assertIsNone(record["ended_at"])

    def test_reaching_a_terminal_status_stamps_ended_at_and_keeps_started_at(self):
        store = self.store()
        store.upsert("T-1", status=contracts.STATUS_RUNNING)
        started = store.get("T-1")["started_at"]
        self.clock.advance(90)
        store.upsert("T-1", status=contracts.STATUS_LANDED)
        record = store.get("T-1")
        self.assertEqual(record["started_at"], started)
        self.assertEqual(st._epoch(record["ended_at"]) - st._epoch(started), 90)

    def test_a_second_upsert_at_the_same_status_does_not_restamp(self):
        store = self.store()
        store.upsert("T-1", status=contracts.STATUS_RUNNING)
        started = store.get("T-1")["started_at"]
        self.clock.advance(60)
        store.upsert("T-1", status=contracts.STATUS_RUNNING, session_id="sid")
        self.assertEqual(store.get("T-1")["started_at"], started)

    def test_a_retried_record_gets_a_fresh_start_and_drops_the_old_ending(self):
        """The blocked and halted retry path. A record re-entering `running` still carries the
        previous attempt's `ended_at`, and an ending older than its start reads as no elapsed
        at all for the whole window the progress view exists to show."""
        store = self.store()
        store.upsert("T-1", status=contracts.STATUS_RUNNING)
        self.clock.advance(30)
        store.upsert("T-1", status=contracts.STATUS_BLOCKED)
        first_end = store.get("T-1")["ended_at"]
        self.assertIsNotNone(first_end)
        self.clock.advance(300)
        store.upsert("T-1", status=contracts.STATUS_RUNNING)
        record = store.get("T-1")
        self.assertIsNone(record["ended_at"])
        self.assertEqual(st._epoch(record["started_at"]), self.clock.t)

    def test_a_terminal_to_terminal_move_keeps_the_original_ending(self):
        """`verify.startup_reverify` promotes a halted record to landed at the next run's
        startup. Restamping there would report hours of elapsed for work that already ended,
        and that number feeds the mean the remaining estimate divides by."""
        store = self.store()
        store.upsert("T-1", status=contracts.STATUS_RUNNING)
        self.clock.advance(30)
        store.upsert("T-1", status=contracts.STATUS_HALTED)
        ended = store.get("T-1")["ended_at"]
        self.clock.advance(86_400)
        store.upsert("T-1", status=contracts.STATUS_LANDED)
        self.assertEqual(store.get("T-1")["ended_at"], ended)

    def test_pending_straight_to_excluded_ends_without_ever_starting(self):
        store = self.store()
        store.upsert("T-1", status=contracts.STATUS_EXCLUDED, excluded_reason="asked")
        record = store.get("T-1")
        self.assertIsNone(record["started_at"])
        self.assertIsNotNone(record["ended_at"])

    def test_an_explicit_stamp_from_the_caller_wins(self):
        store = self.store()
        store.upsert("T-1", status=contracts.STATUS_RUNNING, started_at="2020-01-01T00:00:00+00:00")
        self.assertEqual(store.get("T-1")["started_at"], "2020-01-01T00:00:00+00:00")

    def test_a_write_that_moves_no_status_touches_neither_stamp(self):
        store = self.store()
        store.upsert("T-1", status=contracts.STATUS_RUNNING)
        started = store.get("T-1")["started_at"]
        self.clock.advance(60)
        store.upsert("T-1", baseline_sha="aaa")
        record = store.get("T-1")
        self.assertEqual(record["started_at"], started)
        self.assertIsNone(record["ended_at"])

    def test_terminal_statuses_names_the_four_a_task_does_not_leave(self):
        self.assertEqual(set(contracts.TERMINAL_STATUSES),
                         {contracts.STATUS_EXCLUDED, contracts.STATUS_BLOCKED,
                          contracts.STATUS_HALTED, contracts.STATUS_LANDED})
        self.assertFalse(set(contracts.TERMINAL_STATUSES) & set(contracts.IN_FLIGHT_STATUSES))
        self.assertNotIn(contracts.STATUS_PENDING, contracts.TERMINAL_STATUSES)


class Observer(StateCase):
    """R2's seam. Every status move, whichever writer made it, reported once after the lock."""

    def setUp(self):
        super().setUp()
        self.seen = []

    def watching(self, **kwargs):
        return self.store(observer=lambda task_id, before, after:
                          self.seen.append((task_id, before, after)), **kwargs)

    def test_every_upsert_transition_is_reported_in_order(self):
        store = self.watching()
        store.upsert("T-1", status=contracts.STATUS_RUNNING)
        store.upsert("T-1", status=contracts.STATUS_MERGING)
        store.upsert("T-1", status=contracts.STATUS_LANDED)
        store.upsert("T-2", status=contracts.STATUS_HALTED)
        self.assertEqual(self.seen, [
            ("T-1", None, contracts.STATUS_RUNNING),
            ("T-1", contracts.STATUS_RUNNING, contracts.STATUS_MERGING),
            ("T-1", contracts.STATUS_MERGING, contracts.STATUS_LANDED),
            ("T-2", None, contracts.STATUS_HALTED),
        ])

    def test_a_write_that_moves_no_status_reports_nothing(self):
        store = self.watching()
        store.upsert("T-1", status=contracts.STATUS_RUNNING)
        self.seen.clear()
        store.upsert("T-1", session_id="sid", baseline_sha="aaa")
        store.set_cursor(1)
        store.record_git_op("T-1", "push", "intent")
        self.assertEqual(self.seen, [])

    def test_a_stale_lease_reclaim_reports_the_records_it_marks_crashed(self):
        """`_mark_crashed` writes `record["status"]` directly inside `_mutate`, so a seam on
        `upsert` alone would stay silent on the one event an unattended operator most needs."""
        first = self.store(pid=100)
        first.acquire()
        first.upsert("T-1", status=contracts.STATUS_RUNNING)
        self.clock.advance(1_200)
        second = self.watching(pid=200, hostname="host-b")
        second.acquire()
        self.assertEqual(self.seen,
                         [("T-1", contracts.STATUS_RUNNING, contracts.STATUS_HALTED)])

    def test_a_reclaim_leaves_no_ending_because_it_does_not_know_when_work_stopped(self):
        """The reclaim's own clock is the moment somebody noticed the crash, which can be days
        after the task stopped. Stamping it would make the crash's age look like work, and
        `startup_reverify` promoting the record to landed would then feed that number to the
        remaining estimate as its whole sample."""
        first = self.store(pid=100)
        first.acquire()
        first.upsert("T-1", status=contracts.STATUS_RUNNING)
        self.clock.advance(172_800)
        second = self.store(pid=200, hostname="host-b")
        second.acquire()
        record = second.get("T-1")
        self.assertEqual(record["status"], contracts.STATUS_HALTED)
        self.assertIsNone(record["ended_at"])
        self.assertIsNotNone(record["started_at"])

    def test_the_r33_downgrade_in_validate_is_reported(self):
        store = self.watching()
        store.upsert("T-1", status=contracts.STATUS_LANDED, landing_ref=None, verify={"at": "x"})
        self.seen.clear()
        store.validate()
        self.assertEqual(self.seen,
                         [("T-1", contracts.STATUS_LANDED, contracts.STATUS_PENDING)])

    def test_the_observer_runs_after_the_lock_is_released(self):
        """It may fire a subprocess. Holding the flock across that would block every reader,
        including `status`, for as long as the notifier takes."""
        store = self.store()
        locked = []
        store.observer = lambda task_id, before, after: locked.append(self.lock_is_free(store))
        store.upsert("T-1", status=contracts.STATUS_RUNNING)
        self.assertEqual(locked, [True])

    def lock_is_free(self, store):
        fd = os.open(store.lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False
        finally:
            os.close(fd)

    def test_an_fn_that_raises_announces_nothing_and_still_propagates(self):
        """The observer fires only on the path that reached `_write_locked`. A mutation that
        never persisted must not announce a status it did not write, and the exception must
        still reach the caller with the lock released."""
        store = self.watching()
        store.upsert("T-1", status=contracts.STATUS_RUNNING)
        self.seen.clear()

        def boom():
            raise RuntimeError("simulated crash")

        store._abort_after_write = boom
        with self.assertRaises(RuntimeError):
            store.upsert("T-1", status=contracts.STATUS_LANDED)
        store._abort_after_write = None
        self.assertEqual(self.seen, [])
        self.assertEqual(store.get("T-1")["status"], contracts.STATUS_RUNNING)
        self.assertTrue(self.lock_is_free(store), "the lock was not released")

    def test_a_store_with_no_observer_is_unchanged(self):
        store = self.store()
        store.upsert("T-1", status=contracts.STATUS_RUNNING)
        store.upsert("T-1", status=contracts.STATUS_LANDED)
        self.assertEqual(store.get("T-1")["status"], contracts.STATUS_LANDED)


class Terminal(StateCase):
    def test_status_word_distinguishes_completed_halted_and_crashed(self):
        store = self.store()
        self.assertEqual(store.status_word(), "no_state")
        store.acquire()
        self.assertEqual(store.status_word(), "running")
        store.write_terminal(contracts.RUN_COMPLETED, cli_version="2.1.245",
                             cli_version_observed="2.1.247")
        store.release()
        self.assertEqual(store.status_word(), contracts.RUN_COMPLETED)
        self.assertEqual(store.terminal()["cli_version"], {"claude": "2.1.245"})
        self.assertEqual(store.terminal()["cli_version_observed"], {"claude": "2.1.247"})
        store.acquire()
        store.write_terminal(contracts.RUN_HALTED, halt_task="T-2", halt_class=contracts.HALT_TIMEOUT)
        store.release()
        self.assertEqual(store.status_word(), contracts.RUN_HALTED)
        self.assertEqual(store.terminal()["halt_task"], "T-2")
        # A failed version probe is an empty per-backend map, not an omitted field.
        self.assertEqual(store.terminal()["cli_version_observed"], {})
        # A new run acquires, then dies: no terminal after the lease, lease stale.
        self.clock.advance(1)
        store.acquire()
        self.clock.advance(601)
        self.assertEqual(store.status_word(), contracts.RUN_CRASHED)

    def test_legacy_scalar_terminal_and_missing_record_backend_open_as_claude(self):
        store = self.store()
        legacy = {
            "schema_version": 1,
            "manifest": store.manifest_path,
            "repo": store.repo_path,
            "lease": None,
            "cursor": 0,
            "tasks": {"T-1": {"id": "T-1", "status": contracts.STATUS_BLOCKED}},
            "terminal": {"run_status": contracts.RUN_HALTED, "halt_task": "T-1",
                         "halt_class": contracts.HALT_TIMEOUT, "cli_version": "2.1.245",
                         "cli_version_observed": "2.1.247", "written_at": "1970-01-01T00:00:00+00:00"},
            "git_ops": [],
        }
        with open(store.state_path, "w", encoding="utf-8") as handle:
            json.dump(legacy, handle)
        self.assertEqual(store.get("T-1")["backend"], "claude")
        self.assertEqual(store.terminal()["cli_version"], {"claude": "2.1.245"})
        self.assertEqual(store.terminal()["cli_version_observed"], {"claude": "2.1.247"})
        store.set_cursor(1)
        with open(store.state_path, encoding="utf-8") as handle:
            data = json.load(handle)
            self.assertEqual(data["schema_version"], contracts.STATE_SCHEMA_VERSION)
            # The upgrade is the one mutation that makes the backfill durable, because the
            # read side backfill below stops applying once the version is current.
            self.assertEqual(data["tasks"]["T-1"]["backend"], "claude")
        self.assertEqual(store.get("T-1")["backend"], "claude")

    def test_a_current_schema_record_with_no_backend_is_not_backfilled(self):
        """U14, T-35: a record halted before launch has no backend yet. Backfilling claude onto
        it made run.py's record wins rule swap a resumed grok task onto claude, which launched
        claude with grok's model. Only a genuinely legacy file earns the backfill."""
        store = self.store()
        store.acquire()
        store.upsert("T-1", status=contracts.STATUS_HALTED,
                     halt_class=contracts.HALT_UNCLEAN_EXIT)
        store.release()
        self.assertIsNone(store.get("T-1")["backend"])


class Layout(StateCase):
    def test_directory_and_file_modes(self):
        store = self.store()
        store.acquire()
        self.assertEqual(stat.S_IMODE(os.stat(store.dir).st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(os.stat(store.state_path).st_mode), 0o600)
        for sub in ("logs", "briefs", "digests", "gate"):
            self.assertTrue(os.path.isdir(store.path(sub)))

    def test_symlinked_manifest_shares_state_with_its_target(self):
        link = os.path.join(self.tmp.name, "link.toml")
        os.symlink(self.manifest, link)
        self.assertEqual(self.store().dir, self.store(manifest=link).dir)

    def test_xdg_state_home_is_honored(self):
        os.environ["XDG_STATE_HOME"] = os.path.join(self.tmp.name, "xdg")
        try:
            store = self.store()
            self.assertTrue(store.dir.startswith(os.path.join(self.tmp.name, "xdg", "relay")))
        finally:
            del os.environ["XDG_STATE_HOME"]


if __name__ == "__main__":
    unittest.main()
