"""U4 (issue #44): the progress view's arithmetic.

Every case injects a clock. A test that read the wall clock would be measuring the suite rather
than the module, and the one number here that depends on "now", a running task's elapsed, is
exactly the one worth pinning.
"""
import os
import tempfile
import unittest
from types import SimpleNamespace

import _paths
from relay import contracts, progress, state as st


def iso(epoch):
    return st._iso(epoch)


class FakeStore:
    """Stands in for a StateStore. `progress.build` reads state and nothing else, so a dict is
    the whole surface it needs, and this keeps the arithmetic tests off the filesystem."""

    def __init__(self, tasks, cursor=0):
        self.raw = {"tasks": tasks, "cursor": cursor}

    def read(self):
        return self.raw


def manifest(*task_ids):
    return SimpleNamespace(tasks=[SimpleNamespace(id=task_id) for task_id in task_ids])


def record(status, started=None, ended=None, **extra):
    """A record shaped the way `state.upsert` writes one: the stamps are ISO strings, not
    epochs. Taking epochs here and converting is what keeps each case readable as offsets from
    NOW while still exercising the parse `build` actually performs."""
    out = {"status": status,
           "started_at": None if started is None else iso(started),
           "ended_at": None if ended is None else iso(ended)}
    out.update(extra)
    return out


NOW = 1_000_000.0


class Counts(unittest.TestCase):
    def build(self, tasks, ids=("T-1", "T-2", "T-3", "T-4", "T-5")):
        return progress.build(manifest(*ids), FakeStore(tasks), now=lambda: NOW)

    def test_landed_running_and_todo_are_counted_separately(self):
        data = self.build({
            "T-1": record(contracts.STATUS_LANDED, NOW - 200, NOW - 100),
            "T-2": record(contracts.STATUS_LANDED, NOW - 100, NOW - 50),
            "T-3": record(contracts.STATUS_RUNNING, NOW - 30),
        })
        self.assertEqual(data["counts"][contracts.STATUS_LANDED], 2)
        self.assertEqual(data["counts"][contracts.STATUS_RUNNING], 1)
        self.assertEqual(data["counts"]["todo"], 2)

    def test_a_pending_record_counts_as_todo_not_as_pending(self):
        data = self.build({"T-1": record(contracts.STATUS_PENDING)}, ids=("T-1",))
        self.assertEqual(data["counts"]["todo"], 1)
        self.assertNotIn(contracts.STATUS_PENDING, data["counts"])

    def test_merging_is_counted_rather_than_dropped(self):
        """A task holds `merging` through the gate, the merge, the closeout, the push, and both
        verifies, which is a large share of the window an operator asks `status` about."""
        data = self.build({"T-1": record(contracts.STATUS_MERGING, NOW - 10)}, ids=("T-1",))
        self.assertEqual(data["counts"][contracts.STATUS_MERGING], 1)

    def test_excluded_counts_as_excluded_and_never_as_todo(self):
        data = self.build({"T-1": record(contracts.STATUS_EXCLUDED, None, NOW - 5)},
                          ids=("T-1", "T-2"))
        self.assertEqual(data["counts"][contracts.STATUS_EXCLUDED], 1)
        self.assertEqual(data["counts"]["todo"], 1)

    def test_the_counts_sum_to_the_manifest_length(self):
        data = self.build({
            "T-1": record(contracts.STATUS_LANDED, NOW - 200, NOW - 100),
            "T-2": record(contracts.STATUS_HALTED, NOW - 90, NOW - 80),
            "T-3": record(contracts.STATUS_MERGING, NOW - 10),
        })
        self.assertEqual(sum(data["counts"].values()), 5)


class Elapsed(unittest.TestCase):
    def entry(self, rec, task_id="T-1"):
        data = progress.build(manifest(task_id), FakeStore({task_id: rec}), now=lambda: NOW)
        return data["tasks"][0]

    def test_a_finished_record_reports_the_difference_between_its_stamps(self):
        entry = self.entry(record(contracts.STATUS_LANDED, NOW - 300, NOW - 120))
        self.assertEqual(entry["elapsed_seconds"], 180)

    def test_a_running_record_counts_to_now(self):
        entry = self.entry(record(contracts.STATUS_RUNNING, NOW - 45))
        self.assertEqual(entry["elapsed_seconds"], 45)

    def test_a_merging_record_counts_to_now_too(self):
        entry = self.entry(record(contracts.STATUS_MERGING, NOW - 45))
        self.assertEqual(entry["elapsed_seconds"], 45)

    def test_a_pending_record_reports_nothing(self):
        entry = self.entry(record(contracts.STATUS_PENDING))
        self.assertIsNone(entry["elapsed_seconds"])

    def test_a_halted_record_with_no_ending_reports_nothing_rather_than_counting_to_now(self):
        """The shape a reclaimed crash leaves. Counting to now would report the age of the
        crash as work, and it would feed that number into the mean the estimate divides by."""
        entry = self.entry(record(contracts.STATUS_HALTED, NOW - 86_400))
        self.assertIsNone(entry["elapsed_seconds"])

    def test_an_excluded_record_with_no_start_reports_nothing(self):
        entry = self.entry(record(contracts.STATUS_EXCLUDED, None, NOW - 5))
        self.assertIsNone(entry["elapsed_seconds"])

    def test_an_unparseable_stamp_reports_nothing_and_raises_nothing(self):
        rec = {"status": contracts.STATUS_LANDED, "started_at": "not a date",
               "ended_at": "also not a date"}
        self.assertIsNone(self.entry(rec)["elapsed_seconds"])

    def test_a_task_with_no_record_at_all_reports_nothing(self):
        data = progress.build(manifest("T-1"), FakeStore({}), now=lambda: NOW)
        self.assertIsNone(data["tasks"][0]["elapsed_seconds"])
        self.assertEqual(data["tasks"][0]["status"], "todo")

    def test_an_ending_before_its_start_never_reports_a_negative(self):
        entry = self.entry(record(contracts.STATUS_LANDED, NOW, NOW - 500))
        self.assertIsNone(entry["elapsed_seconds"])


class Total(unittest.TestCase):
    def test_the_total_is_the_sum_of_the_reported_elapsed_values(self):
        data = progress.build(manifest("T-1", "T-2", "T-3"), FakeStore({
            "T-1": record(contracts.STATUS_LANDED, NOW - 300, NOW - 200),
            "T-2": record(contracts.STATUS_LANDED, NOW - 200, NOW - 150),
            "T-3": record(contracts.STATUS_RUNNING, NOW - 20),
        }), now=lambda: NOW)
        reported = [e["elapsed_seconds"] for e in data["tasks"] if e["elapsed_seconds"]]
        self.assertEqual(data["total_seconds"], sum(reported))
        self.assertEqual(data["total_seconds"], 170)

    def test_a_run_with_no_stamps_at_all_totals_nothing(self):
        data = progress.build(manifest("T-1"), FakeStore({}), now=lambda: NOW)
        self.assertIsNone(data["total_seconds"])


class Estimate(unittest.TestCase):
    def build(self, tasks, ids):
        return progress.build(manifest(*ids), FakeStore(tasks), now=lambda: NOW)

    def test_the_mean_of_the_landed_tasks_times_the_todo_count(self):
        data = self.build({
            "T-1": record(contracts.STATUS_LANDED, NOW - 400, NOW - 300),
            "T-2": record(contracts.STATUS_LANDED, NOW - 300, NOW - 100),
        }, ("T-1", "T-2", "T-3", "T-4"))
        self.assertEqual(data["landed_sample"], 2)
        self.assertEqual(data["estimate_seconds"], 300)

    def test_a_running_task_is_counted_once_in_its_own_term_not_in_the_todo_count(self):
        data = self.build({
            "T-1": record(contracts.STATUS_LANDED, NOW - 200, NOW - 100),
            "T-2": record(contracts.STATUS_RUNNING, NOW - 40),
        }, ("T-1", "T-2", "T-3"))
        # One landed at 100s, one todo at 100s, and the running one 60s short of the mean.
        self.assertEqual(data["estimate_seconds"], 160)

    def test_a_running_task_already_past_the_mean_adds_nothing_rather_than_a_negative(self):
        data = self.build({
            "T-1": record(contracts.STATUS_LANDED, NOW - 200, NOW - 100),
            "T-2": record(contracts.STATUS_RUNNING, NOW - 5_000),
        }, ("T-1", "T-2"))
        self.assertEqual(data["estimate_seconds"], 0)

    def test_no_landed_task_means_no_estimate(self):
        data = self.build({"T-1": record(contracts.STATUS_RUNNING, NOW - 10)}, ("T-1", "T-2"))
        self.assertIsNone(data["estimate_seconds"])
        self.assertEqual(data["landed_sample"], 0)

    def test_a_landed_task_with_no_usable_stamps_is_not_a_sample(self):
        """Two supported shapes land here: a task an operator finished by hand, which
        `startup_reverify` promotes without it ever entering `running`, and any record written
        before the stamps existed. Gating on "a task landed" would divide by an empty sample."""
        data = self.build({"T-1": record(contracts.STATUS_LANDED)}, ("T-1", "T-2"))
        self.assertEqual(data["landed_sample"], 0)
        self.assertIsNone(data["estimate_seconds"])

    def test_an_excluded_task_is_not_remaining_work(self):
        data = self.build({
            "T-1": record(contracts.STATUS_LANDED, NOW - 200, NOW - 100),
            "T-2": record(contracts.STATUS_EXCLUDED, None, NOW - 90),
        }, ("T-1", "T-2"))
        self.assertEqual(data["estimate_seconds"], 0)

    def test_a_halted_task_is_remaining_work_because_a_later_run_retries_it(self):
        """`run._one_task` returns early for landed and excluded, never for halted: a resumed
        run re-verifies a halted record and relaunches it. Treating it as work that never
        happens made the estimate report minutes for hours of queued work, on exactly the
        repair and re-run path Relay is built around."""
        data = self.build({
            "T-1": record(contracts.STATUS_LANDED, NOW - 200, NOW - 100),
            "T-2": record(contracts.STATUS_HALTED, NOW - 90, NOW - 80),
        }, ("T-1", "T-2"))
        self.assertEqual(data["estimate_seconds"], 100)

    def test_a_blocked_task_is_not_counted_because_retrying_it_is_a_launch_choice(self):
        """Blocked runs again only under `--retry-blocked`, which the next launch decides and
        this state file does not carry."""
        data = self.build({
            "T-1": record(contracts.STATUS_LANDED, NOW - 200, NOW - 100),
            "T-2": record(contracts.STATUS_BLOCKED, NOW - 90, NOW - 80),
        }, ("T-1", "T-2"))
        self.assertEqual(data["estimate_seconds"], 0)


class Entries(unittest.TestCase):
    def test_entries_follow_manifest_order_not_alphabetical_order(self):
        data = progress.build(manifest("T-9", "T-2", "T-5"), FakeStore({
            "T-2": record(contracts.STATUS_LANDED, NOW - 10, NOW - 5),
            "T-5": record(contracts.STATUS_RUNNING, NOW - 1),
            "T-9": record(contracts.STATUS_LANDED, NOW - 20, NOW - 15),
        }), now=lambda: NOW)
        self.assertEqual([e["id"] for e in data["tasks"]], ["T-9", "T-2", "T-5"])

    def test_a_record_the_manifest_no_longer_names_comes_last_marked_and_without_elapsed(self):
        data = progress.build(manifest("T-1"), FakeStore({
            "T-1": record(contracts.STATUS_LANDED, NOW - 10, NOW - 5),
            "OLD-7": record(contracts.STATUS_LANDED, NOW - 900, NOW - 800),
            "OLD-2": record(contracts.STATUS_LANDED, NOW - 900, NOW - 800),
        }), now=lambda: NOW)
        self.assertEqual([e["id"] for e in data["tasks"]], ["T-1", "OLD-2", "OLD-7"])
        strays = [e for e in data["tasks"] if not e["in_manifest"]]
        self.assertEqual(len(strays), 2)
        for entry in strays:
            self.assertIsNone(entry["elapsed_seconds"])

    def test_a_stray_record_is_not_counted_and_not_estimated(self):
        data = progress.build(manifest("T-1"), FakeStore({
            "T-1": record(contracts.STATUS_RUNNING, NOW - 10),
            "OLD-1": record(contracts.STATUS_LANDED, NOW - 900, NOW - 800),
        }), now=lambda: NOW)
        self.assertEqual(sum(data["counts"].values()), 1)
        self.assertEqual(data["landed_sample"], 0)


class Duration(unittest.TestCase):
    def test_three_magnitudes_render_three_ways(self):
        self.assertEqual(progress.duration(45), "45s")
        self.assertEqual(progress.duration(605), "10m 5s")
        self.assertEqual(progress.duration(7325), "2h 2m")

    def test_zero_renders_rather_than_reading_as_absent(self):
        self.assertEqual(progress.duration(0), "0s")

    def test_none_has_no_duration(self):
        self.assertIsNone(progress.duration(None))


class Lines(unittest.TestCase):
    def render(self, tasks, ids):
        return progress.lines(progress.build(manifest(*ids), FakeStore(tasks), now=lambda: NOW))

    def test_the_counts_total_and_estimate_each_get_a_line(self):
        text = "\n".join(self.render({
            "T-1": record(contracts.STATUS_LANDED, NOW - 200, NOW - 100),
            "T-2": record(contracts.STATUS_RUNNING, NOW - 40),
        }, ("T-1", "T-2", "T-3")))
        self.assertIn("1 landed", text)
        self.assertIn("1 running", text)
        self.assertIn("1 todo", text)
        self.assertIn("elapsed", text)
        self.assertIn("remaining", text)

    def test_the_estimate_line_names_the_sample_it_came_from(self):
        """A one sample extrapolation sitting beside measured counts should be legible as one."""
        text = "\n".join(self.render({
            "T-1": record(contracts.STATUS_LANDED, NOW - 200, NOW - 100),
        }, ("T-1", "T-2")))
        self.assertIn("1 landed task", text)

    def test_with_nothing_landed_the_estimate_line_says_so_in_words(self):
        text = "\n".join(self.render({
            "T-1": record(contracts.STATUS_RUNNING, NOW - 10),
        }, ("T-1", "T-2")))
        self.assertIn("no estimate", text)
        self.assertNotIn("roughly", text)

    def test_the_total_line_names_what_it_sums_rather_than_reading_as_a_stopwatch(self):
        """It excludes lease acquisition, startup re-verify, pre-flight, and the tracker reads
        between tasks, and on a resumed run it takes in a previous run's landed tasks."""
        text = "\n".join(self.render({
            "T-1": record(contracts.STATUS_LANDED, NOW - 200, NOW - 100),
        }, ("T-1",)))
        self.assertIn("across", text)


class Liveness(unittest.TestCase):
    """The state directory outlives any one run, so a record reading `running` is only a task in
    progress while a runner is actually driving. Without the gate the same screen printed
    `status: crashed` beside a task that had been running for eight hours."""

    def build(self, tasks, ids, live):
        return progress.build(manifest(*ids), FakeStore(tasks), now=lambda: NOW, live=live)

    def test_a_dead_runs_in_flight_record_reports_no_duration(self):
        data = self.build({"T-1": record(contracts.STATUS_RUNNING, NOW - 28_800)},
                          ("T-1",), live=False)
        self.assertIsNone(data["tasks"][0]["elapsed_seconds"])
        self.assertIsNone(data["total_seconds"])

    def test_the_same_record_counts_while_the_run_is_live(self):
        data = self.build({"T-1": record(contracts.STATUS_RUNNING, NOW - 45)},
                          ("T-1",), live=True)
        self.assertEqual(data["tasks"][0]["elapsed_seconds"], 45)

    def test_a_finished_record_is_unaffected_by_liveness(self):
        for live in (True, False):
            data = self.build({"T-1": record(contracts.STATUS_LANDED, NOW - 300, NOW - 120)},
                              ("T-1",), live=live)
            self.assertEqual(data["tasks"][0]["elapsed_seconds"], 180, live)


class DowngradedRecord(unittest.TestCase):
    """`state.validate`'s R33 downgrade returns a landed record to pending without touching its
    stamps, so a task waiting to be re-run still carries the previous run's pair."""

    def test_a_downgraded_record_reads_todo_and_reports_no_duration(self):
        data = progress.build(manifest("T-1"), FakeStore({
            "T-1": record(contracts.STATUS_PENDING, NOW - 500, NOW - 100),
        }), now=lambda: NOW)
        entry = data["tasks"][0]
        self.assertEqual(entry["status"], "todo")
        self.assertIsNone(entry["elapsed_seconds"])
        self.assertIsNone(data["total_seconds"])

    def test_a_downgraded_record_is_remaining_work(self):
        data = progress.build(manifest("T-1", "T-2"), FakeStore({
            "T-1": record(contracts.STATUS_LANDED, NOW - 200, NOW - 100),
            "T-2": record(contracts.STATUS_PENDING, NOW - 500, NOW - 400),
        }), now=lambda: NOW)
        self.assertEqual(data["estimate_seconds"], 100)


class AgainstARealStore(unittest.TestCase):
    """Cases through the real StateStore, so the record shapes `build` reads are the ones the
    store actually writes rather than the ones this module's fake happens to use.

    This class exists because of a defect the fake could not have caught. `test_progress` and
    `test_state` each held a case about what a reclaimed crash leaves behind, they asserted
    opposite things, and both passed: one against a hand written dict, the other against the
    store. The store was right and the progress fixture was fiction.
    """

    def store(self, tmp, clock):
        home = os.path.join(tmp, "home")
        repo = os.path.join(tmp, "repo")
        os.makedirs(home, exist_ok=True)
        os.makedirs(repo, exist_ok=True)
        path = os.path.join(tmp, "m.toml")
        open(path, "w").close()
        return st.StateStore(path, repo, home=home, now=lambda: clock[0])

    def test_a_landed_task_written_by_the_store_reports_its_elapsed(self):
        with tempfile.TemporaryDirectory() as tmp:
            clock = [NOW]
            store = self.store(tmp, clock)
            store.upsert("T-1", status=contracts.STATUS_RUNNING)
            clock[0] += 120
            store.upsert("T-1", status=contracts.STATUS_LANDED)
            clock[0] += 5
            data = progress.build(manifest("T-1"), store, now=lambda: clock[0])
            self.assertEqual(data["tasks"][0]["elapsed_seconds"], 120)
            self.assertEqual(data["landed_sample"], 1)

    def test_a_task_the_store_is_still_running_reports_a_live_duration(self):
        with tempfile.TemporaryDirectory() as tmp:
            clock = [NOW]
            store = self.store(tmp, clock)
            store.upsert("T-1", status=contracts.STATUS_RUNNING)
            clock[0] += 90
            data = progress.build(manifest("T-1"), store, now=lambda: clock[0])
            self.assertEqual(data["tasks"][0]["elapsed_seconds"], 90)

    def test_a_real_reclaimed_crash_reports_no_duration_and_no_estimate(self):
        """The defect three reviewers found. A reclaim marks the record halted, and the ending
        it would have stamped is the moment somebody noticed the crash. `startup_reverify` then
        promotes that record to landed, and the estimate divides by it."""
        with tempfile.TemporaryDirectory() as tmp:
            clock = [NOW]
            first = self.store(tmp, clock)
            first.acquire()
            first.upsert("T-1", status=contracts.STATUS_RUNNING)
            clock[0] += 172_800
            second = st.StateStore(first.manifest_path, first.repo_path,
                                   home=os.path.join(tmp, "home"), now=lambda: clock[0],
                                   pid=99_999, hostname="other")
            second.acquire()
            record_after = second.get("T-1")
            self.assertEqual(record_after["status"], contracts.STATUS_HALTED)
            self.assertIsNone(record_after["ended_at"])
            data = progress.build(manifest("T-1", "T-2"), second, now=lambda: clock[0])
            self.assertIsNone(data["tasks"][0]["elapsed_seconds"])
            # Promoted to landed the way a hand repair plus startup_reverify would.
            second.upsert("T-1", status=contracts.STATUS_LANDED)
            data = progress.build(manifest("T-1", "T-2"), second, now=lambda: clock[0])
            self.assertEqual(data["landed_sample"], 0)
            self.assertIsNone(data["estimate_seconds"])


if __name__ == "__main__":
    unittest.main()
