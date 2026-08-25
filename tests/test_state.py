"""U3: the state file, its lease, the repo lease, atomic writes, and the validate rules."""
import json
import os
import stat
import tempfile
import unittest

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

    def store(self, manifest=None, pid=100, hostname="host-a", ttl=600):
        return st.StateStore(manifest or self.manifest, self.repo, home=self.home, now=self.clock,
                             pid=pid, hostname=hostname, ttl_seconds=ttl)


class Acquire(StateCase):
    def test_first_acquire_creates_state_with_schema_and_lease(self):
        store = self.store()
        result = store.acquire()
        self.assertEqual(result.code, st.OK)
        with open(store.state_path) as handle:
            data = json.load(handle)
        self.assertEqual(data["schema_version"], 1)
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

    def test_stale_lease_is_reclaimed_and_old_holder_recorded_as_crashed(self):
        self.store(pid=100).acquire()
        self.clock.advance(601)
        result = self.store(pid=200).acquire()
        self.assertEqual(result.code, st.STALE_RECLAIMED)
        self.assertEqual(result.previous_holder["holder_pid"], 100)
        store = self.store(pid=200)
        terminal = store.terminal()
        self.assertEqual(terminal["run_status"], contracts.RUN_CRASHED)
        self.assertEqual(terminal["previous_holder"]["holder_pid"], 100)
        self.assertEqual(store.lease()["holder_pid"], 200)

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
        self.assertEqual(self.store(pid=200).get("T-1")["status"], contracts.STATUS_LANDED)

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
            self.assertEqual(json.load(handle)["schema_version"], 1)

    def test_cursor_and_git_ops(self):
        store = self.store()
        store.acquire()
        store.set_cursor(2)
        self.assertEqual(store.cursor(), 2)
        store.record_git_op("T-1", "push", "intent", {"remote": "origin"})
        store.record_git_op("T-1", "push", "result", {"exit": 0})
        ops = store.read()["git_ops"]
        self.assertEqual([o["phase"] for o in ops], ["intent", "result"])


class Terminal(StateCase):
    def test_status_word_distinguishes_completed_halted_and_crashed(self):
        store = self.store()
        self.assertEqual(store.status_word(), "no_state")
        store.acquire()
        self.assertEqual(store.status_word(), "running")
        store.write_terminal(contracts.RUN_COMPLETED, cli_version="2.1.245")
        store.release()
        self.assertEqual(store.status_word(), contracts.RUN_COMPLETED)
        store.acquire()
        store.write_terminal(contracts.RUN_HALTED, halt_task="T-2", halt_class=contracts.HALT_TIMEOUT)
        store.release()
        self.assertEqual(store.status_word(), contracts.RUN_HALTED)
        self.assertEqual(store.terminal()["halt_task"], "T-2")
        # A new run acquires, then dies: no terminal after the lease, lease stale.
        self.clock.advance(1)
        store.acquire()
        self.clock.advance(601)
        self.assertEqual(store.status_word(), contracts.RUN_CRASHED)


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
