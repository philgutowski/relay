"""U8: verify-landed, the pure verdict over git and the tracker.

The tracker side runs against tests/_fakes.FakeAdapter, which satisfies the interface KTD16 and
plan U4 define. When the real Jira, GitHub, and markdown adapters land in U4, these same
scenarios should also run against them; the fake proves the contract, not any adapter.
"""
import os
import tempfile
import unittest

import _paths
import _repo
from _fakes import FakeAdapter
from relay import contracts, gitread, manifest as mf, state, verify
from test_gitwrite import commit_on_branch

FIXTURE = os.path.join(_paths.FIXTURES_DIR, "manifests", "complete.toml")


class VerifyCase(unittest.TestCase):
    task_id = "T-1"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = _repo.make_repo(self.tmp.name)
        with open(FIXTURE) as handle:
            self.toml = handle.read().replace("__REPO__", self.repo)
        self.baseline = gitread.rev_parse(self.repo, "origin/main")

    def tearDown(self):
        self.tmp.cleanup()

    def manifest(self, text=None, name="manifest.toml"):
        path = os.path.join(self.tmp.name, name)
        with open(path, "w") as handle:
            handle.write(text if text is not None else self.toml)
        return mf.load(path)

    def land_a_commit(self, message="task work", content="value = 1\n"):
        """Put one new commit on main and push it, the state a passing tail leaves behind."""
        sha = commit_on_branch(self.repo, "main", {"src/feature.py": content}, message)
        _repo.git(self.repo, "push", "-q", "origin", "main")
        return sha

    def record(self, **fields):
        base = state.new_record(self.task_id)
        base.update(baseline_sha=self.baseline)
        base.update(fields)
        return base

    def landed_adapter(self, sha, status="done"):
        return FakeAdapter(
            statuses={self.task_id: {"status": status, "terminal": True, "reference": sha}},
            references={(self.task_id, sha[:7]): "comment-9"},
        )


class CodeScope(VerifyCase):
    def test_a_pushed_merge_passes_every_applicable_code_check(self):
        sha = self.land_a_commit()
        verdict = verify.verify(self.manifest(), self.record(landing_ref=sha),
                                FakeAdapter(), scope=verify.SCOPE_CODE)
        for name in ("tree_clean", "on_default", "head_equals_remote", "new_commit_since_baseline"):
            self.assertEqual(verdict.checks[name]["result"], verify.PASS, name)
        for name in ("pr_open", "ci_green"):
            self.assertEqual(verdict.checks[name]["result"], verify.SKIPPED, name)
        self.assertNotIn("card_terminal", verdict.checks)
        self.assertFalse(verdict.landed, "the code scope never decides landing")

    def test_an_unpushed_merge_fails_head_equals_remote_with_both_shas(self):
        sha = commit_on_branch(self.repo, "main", {"src/feature.py": "value = 1\n"}, "unpushed")
        verdict = verify.verify(self.manifest(), self.record(landing_ref=sha),
                                FakeAdapter(), scope=verify.SCOPE_CODE)
        check = verdict.checks["head_equals_remote"]
        self.assertEqual(check["result"], verify.FAIL)
        self.assertEqual(check["evidence"]["local_sha"], sha)
        self.assertEqual(check["evidence"]["remote_sha"], self.baseline)

    def test_a_dirty_tree_fails_tree_clean(self):
        self.land_a_commit()
        with open(os.path.join(self.repo, "scratch.txt"), "w") as handle:
            handle.write("left behind\n")
        verdict = verify.verify(self.manifest(), self.record(), FakeAdapter(), scope=verify.SCOPE_CODE)
        self.assertEqual(verdict.checks["tree_clean"]["result"], verify.FAIL)

    def test_no_commit_past_the_baseline_fails_new_commit_since_baseline(self):
        verdict = verify.verify(self.manifest(), self.record(), FakeAdapter(), scope=verify.SCOPE_CODE)
        self.assertEqual(verdict.checks["new_commit_since_baseline"]["result"], verify.FAIL)


class DoFetch(VerifyCase):
    def test_do_fetch_true_reads_the_remote_fresh_while_the_cached_ref_stays_stale(self):
        """A stand-in for run.py:449's final verify: without a fetch, `head_equals_remote`
        reads whatever `origin/main` this repo last knew about, not what origin actually holds
        now. `do_fetch=False` runs first here so its evidence is the control -- a `do_fetch=True`
        call earlier would resolve the tracking ref and erase the difference this test proves."""
        self.land_a_commit()
        clone = os.path.join(self.tmp.name, "clone")
        _repo.git(self.tmp.name, "clone", "-q", self.repo + ".git", clone)
        _repo.git(clone, "config", "user.name", "Relay Test")
        _repo.git(clone, "config", "user.email", "relay@example.invalid")
        with open(os.path.join(clone, "src", "feature.py"), "w") as handle:
            handle.write("value = 2\n")
        _repo.git(clone, "add", "-A")
        _repo.git(clone, "commit", "-q", "-m", "a third party's commit")
        _repo.git(clone, "push", "-q", "origin", "main")
        new_remote_sha = gitread.rev_parse(clone, "HEAD")

        manifest, record, adapter = self.manifest(), self.record(), FakeAdapter()
        stale = verify.verify(manifest, record, adapter, scope=verify.SCOPE_FULL, do_fetch=False)
        self.assertNotEqual(stale.checks["head_equals_remote"]["evidence"]["remote_sha"],
                            new_remote_sha)

        fresh = verify.verify(manifest, record, adapter, scope=verify.SCOPE_FULL, do_fetch=True)
        self.assertEqual(fresh.checks["head_equals_remote"]["evidence"]["remote_sha"],
                         new_remote_sha)


class FullScope(VerifyCase):
    def test_code_on_the_remote_and_a_terminal_card_is_landed(self):
        sha = self.land_a_commit()
        verdict = verify.verify(self.manifest(), self.record(landing_ref=sha), self.landed_adapter(sha))
        self.assertTrue(verdict.landed, verdict.checks)
        self.assertEqual(verdict.halt_class, contracts.HALT_LANDED)
        self.assertEqual(verdict.checks["card_terminal"]["result"], verify.PASS)
        self.assertEqual(verdict.checks["closing_reference"]["result"], verify.PASS)
        self.assertEqual(verdict.checks["mirror_equals_head"]["result"], verify.SKIPPED)
        self.assertIsNotNone(verdict.at)

    def test_code_on_the_remote_with_the_card_not_terminal_is_a_partial_landing(self):
        sha = self.land_a_commit()
        adapter = FakeAdapter(statuses={self.task_id: {"status": "Backlog", "terminal": False}})
        verdict = verify.verify(self.manifest(), self.record(landing_ref=sha), adapter)
        self.assertFalse(verdict.landed)
        self.assertEqual(verdict.halt_class, contracts.HALT_PARTIAL_LANDING)
        self.assertEqual(verdict.checks["card_terminal"]["result"], verify.FAIL)
        self.assertEqual(verdict.checks["card_terminal"]["evidence"]["status"], "Backlog")

    def test_a_terminal_card_with_no_closing_reference_is_a_partial_landing(self):
        sha = self.land_a_commit()
        adapter = FakeAdapter(statuses={self.task_id: {"status": "done", "terminal": True}})
        verdict = verify.verify(self.manifest(), self.record(landing_ref=sha), adapter)
        self.assertEqual(verdict.halt_class, contracts.HALT_PARTIAL_LANDING)
        self.assertEqual(verdict.checks["closing_reference"]["result"], verify.FAIL)

    def test_a_denied_tracker_write_becomes_the_evidence_for_the_partial_landing(self):
        sha = self.land_a_commit()
        finding = {"class": contracts.HALT_TRACKER_WRITE_DENIED, "tool": "fake_tracker__close",
                   "target": self.task_id}
        adapter = FakeAdapter(statuses={self.task_id: {"status": "Backlog", "terminal": False}})
        verdict = verify.verify(self.manifest(),
                                self.record(landing_ref=sha, findings=[finding]), adapter)
        self.assertEqual(verdict.halt_class, contracts.HALT_PARTIAL_LANDING)
        self.assertIn(finding, verdict.evidence["findings"])

    def test_a_tracker_read_that_failed_blocks_landing_without_claiming_a_partial(self):
        sha = self.land_a_commit()
        adapter = FakeAdapter(statuses={self.task_id: {"skipped": "gh exited 1: not authenticated"}})
        verdict = verify.verify(self.manifest(), self.record(landing_ref=sha), adapter)
        self.assertFalse(verdict.landed)
        self.assertIsNone(verdict.halt_class)
        check = verdict.checks["card_terminal"]
        self.assertEqual(check["result"], verify.SKIPPED)
        self.assertTrue(check["blocking"])
        self.assertIn("not authenticated", check["evidence"]["reason"])


class LandedByHand(VerifyCase):
    """The first Cratekit run: a task halted, the operator finished it by hand and merged it
    with `Closes #62`, and the record had no landing_ref, so closing_reference was a blocking
    skip forever and the runner relaunched a closed issue."""

    def test_a_commit_on_main_naming_the_task_is_the_closing_reference(self):
        sha = self.land_a_commit("merge: the shell, %s done" % self.task_id)
        adapter = FakeAdapter(statuses={self.task_id: {"status": "done", "terminal": True}})
        verdict = verify.verify(self.manifest(), self.record(), adapter)
        self.assertTrue(verdict.landed, verdict.checks)
        check = verdict.checks["closing_reference"]
        self.assertEqual(check["result"], verify.PASS)
        self.assertEqual(check["evidence"]["ref"], sha)
        self.assertIn("names the task", check["evidence"]["derived"])
        self.assertEqual(verdict.evidence["landing_ref"], sha)

    def test_a_numeric_id_is_matched_as_a_hash_reference_only(self):
        self.task_id = "62"
        self.land_a_commit("feat: 62 tests, no reference")
        adapter = FakeAdapter(statuses={"62": {"status": "CLOSED", "terminal": True}})
        verdict = verify.verify(self.manifest(), self.record(), adapter)
        self.assertEqual(verdict.checks["closing_reference"]["result"], verify.SKIPPED)
        sha = self.land_a_commit("merge: the master shell\n\nCloses #62", content="value = 2\n")
        verdict = verify.verify(self.manifest(), self.record(), adapter)
        self.assertTrue(verdict.landed, verdict.checks)
        self.assertEqual(verdict.checks["closing_reference"]["evidence"]["ref"], sha)

    def test_no_commit_naming_the_task_keeps_the_blocking_skip(self):
        self.land_a_commit("unrelated work")
        adapter = FakeAdapter(statuses={self.task_id: {"status": "done", "terminal": True}})
        verdict = verify.verify(self.manifest(), self.record(), adapter)
        self.assertFalse(verdict.landed)
        self.assertIsNone(verdict.halt_class)
        self.assertIn("closing_reference", verdict.blocking_skips())

    def test_a_record_with_its_own_landing_ref_is_not_second_guessed(self):
        sha = self.land_a_commit("merge: %s" % self.task_id)
        adapter = FakeAdapter(statuses={self.task_id: {"status": "done", "terminal": True}})
        verdict = verify.verify(self.manifest(), self.record(landing_ref=sha), adapter)
        self.assertEqual(verdict.checks["closing_reference"]["result"], verify.FAIL)
        self.assertEqual(verdict.halt_class, contracts.HALT_PARTIAL_LANDING)


class Mirror(VerifyCase):
    def toml_with_mirror(self):
        return self.toml.replace("mirror = []", 'mirror = ["origin", "main:release"]')

    def test_a_mirror_behind_head_fails_with_both_shas(self):
        _repo.git(self.repo, "push", "-q", "origin", "main:release")
        behind = gitread.rev_parse(self.repo, "origin/release")
        sha = self.land_a_commit()
        verdict = verify.verify(self.manifest(self.toml_with_mirror()),
                                self.record(landing_ref=sha), self.landed_adapter(sha))
        check = verdict.checks["mirror_equals_head"]
        self.assertEqual(check["result"], verify.FAIL)
        self.assertEqual(check["evidence"]["mirror_sha"], behind)
        self.assertEqual(check["evidence"]["head_sha"], sha)
        self.assertFalse(verdict.landed)

    def test_a_mirror_at_head_passes_and_the_task_is_landed(self):
        sha = self.land_a_commit()
        _repo.git(self.repo, "push", "-q", "origin", "main:release")
        verdict = verify.verify(self.manifest(self.toml_with_mirror()),
                                self.record(landing_ref=sha), self.landed_adapter(sha))
        self.assertEqual(verdict.checks["mirror_equals_head"]["result"], verify.PASS)
        self.assertTrue(verdict.landed, verdict.checks)

    def test_a_mirror_ref_the_runner_cannot_read_back_blocks_rather_than_fails(self):
        """Finding 27. `origin/release` was never pushed, so the ref does not resolve. That is
        not a mirror behind head, it is a mirror the runner cannot see, and the remedies differ."""
        sha = self.land_a_commit()
        verdict = verify.verify(self.manifest(self.toml_with_mirror()),
                                self.record(landing_ref=sha), self.landed_adapter(sha))
        check = verdict.checks["mirror_equals_head"]
        self.assertEqual(check["result"], verify.SKIPPED)
        self.assertTrue(check["blocking"])
        self.assertIn("origin/release", check["evidence"]["reason"])
        self.assertFalse(verdict.landed)


class PrTerminalMode(VerifyCase):
    def toml_pr_mode(self):
        return self.toml.replace('mode = "local_merge"', 'mode = "pr_terminal"')

    def test_the_pr_probe_decides_the_pr_checks_and_the_merge_checks_are_skipped(self):
        url = "https://example.invalid/pr/7"
        probe = lambda branch: {"url": url, "number": 7, "state": "OPEN", "ci": "pass"}
        adapter = FakeAdapter(
            statuses={self.task_id: {"status": "done", "terminal": True, "reference": url}},
            references={(self.task_id, url): "comment-3"},
        )
        verdict = verify.verify(self.manifest(self.toml_pr_mode()),
                                self.record(landing_ref=url), adapter, pr_probe=probe)
        self.assertEqual(verdict.checks["pr_open"]["result"], verify.PASS)
        self.assertEqual(verdict.checks["ci_green"]["result"], verify.PASS)
        self.assertEqual(verdict.checks["new_commit_since_baseline"]["result"], verify.SKIPPED)
        self.assertTrue(verdict.landed, verdict.checks)

    def test_ci_that_never_decided_fails_ci_green(self):
        url = "https://example.invalid/pr/7"
        probe = lambda branch: {"url": url, "number": 7, "state": "OPEN", "ci": "undecided"}
        verdict = verify.verify(self.manifest(self.toml_pr_mode()),
                                self.record(landing_ref=url), FakeAdapter(), pr_probe=probe)
        self.assertEqual(verdict.checks["ci_green"]["result"], verify.FAIL)
        self.assertFalse(verdict.landed)

    def test_no_probe_leaves_the_pr_checks_blocking_rather_than_silently_landed(self):
        verdict = verify.verify(self.manifest(self.toml_pr_mode()),
                                self.record(landing_ref="https://example.invalid/pr/7"),
                                FakeAdapter())
        self.assertTrue(verdict.checks["pr_open"]["blocking"])
        self.assertFalse(verdict.landed)


class AgainstTheRealMarkdownAdapter(VerifyCase):
    """The U8 verdict against a real U4 adapter rather than the fake.

    The fake proves the interface; this proves the verdict holds against an adapter that really
    parses a file at the remote default branch. The Jira and GitHub adapters have the same
    contract test in tests/test_adapters.py, and their transports are fixture driven there.
    """

    def setUp(self):
        super().setUp()
        self.write_tracker("- [ ] T-1 Add the brief renderer\n", "seed the tracker")
        # The baseline is the remote head before the task ran; the closeout's own tracker commit
        # lands after it, which is exactly what new_commit_since_baseline is counting.
        self.baseline = gitread.rev_parse(self.repo, "origin/main")

    def write_tracker(self, text, message="tracker"):
        path = os.path.join(self.repo, "tracker.md")
        with open(path, "w") as handle:
            handle.write(text)
        _repo.git(self.repo, "add", "tracker.md")
        _repo.git(self.repo, "commit", "-q", "-m", message)
        _repo.git(self.repo, "push", "-q", "origin", "main")

    def adapter(self):
        from relay import adapters

        return adapters.build(self.manifest())

    def test_a_closed_line_naming_the_merge_sha_lands_the_task(self):
        sha = self.land_a_commit()
        self.write_tracker("- [x] T-1 Add the brief renderer (%s)\n" % sha[:7], "close the line")
        verdict = verify.verify(self.manifest(), self.record(landing_ref=sha), self.adapter())
        self.assertTrue(verdict.landed, verdict.checks)
        self.assertEqual(verdict.checks["closing_reference"]["evidence"]["comment_id"], "T-1")

    def test_an_open_line_after_the_code_landed_is_a_partial_landing(self):
        sha = self.land_a_commit()
        verdict = verify.verify(self.manifest(), self.record(landing_ref=sha), self.adapter())
        self.assertEqual(verdict.halt_class, contracts.HALT_PARTIAL_LANDING)
        self.assertEqual(verdict.checks["card_terminal"]["evidence"]["status"], "open")

    def test_a_tracker_file_missing_from_the_remote_is_a_blocking_skip(self):
        sha = self.land_a_commit()
        _repo.git(self.repo, "rm", "-q", "tracker.md")
        _repo.git(self.repo, "commit", "-q", "-m", "drop the tracker")
        _repo.git(self.repo, "push", "-q", "origin", "main")
        verdict = verify.verify(self.manifest(), self.record(landing_ref=sha), self.adapter())
        self.assertFalse(verdict.landed)
        self.assertIsNone(verdict.halt_class)
        self.assertTrue(verdict.checks["card_terminal"]["blocking"])


class StartupReverify(VerifyCase):
    def store_for(self, manifest):
        return state.StateStore(manifest.path, manifest.project.repo,
                                home=os.path.join(self.tmp.name, "home"))

    def test_a_halted_record_whose_repo_and_tracker_now_pass_is_promoted_to_landed(self):
        manifest = self.manifest()
        store = self.store_for(manifest)
        sha = self.land_a_commit()
        store.upsert(self.task_id, status=contracts.STATUS_HALTED,
                     halt_class=contracts.HALT_PARTIAL_LANDING, baseline_sha=self.baseline,
                     landing_ref=sha)
        promoted = verify.startup_reverify(manifest, store, self.landed_adapter(sha))
        self.assertEqual(promoted, [self.task_id])
        record = store.get(self.task_id)
        self.assertEqual(record["status"], contracts.STATUS_LANDED)
        self.assertEqual(record["verify"]["scope"], verify.SCOPE_FULL)
        self.assertIsNotNone(record["verify"]["at"])

    def test_a_task_landed_by_hand_is_promoted_and_its_landing_ref_recorded(self):
        manifest = self.manifest()
        store = self.store_for(manifest)
        sha = self.land_a_commit("merge: %s by hand" % self.task_id)
        store.upsert(self.task_id, status=contracts.STATUS_HALTED,
                     halt_class=contracts.HALT_UNCLEAN_EXIT, baseline_sha=self.baseline)
        adapter = FakeAdapter(statuses={self.task_id: {"status": "done", "terminal": True}})
        self.assertEqual(verify.startup_reverify(manifest, store, adapter), [self.task_id])
        record = store.get(self.task_id)
        self.assertEqual(record["status"], contracts.STATUS_LANDED)
        self.assertEqual(record["landing_ref"], sha)

    def test_a_halted_record_that_still_fails_stays_halted(self):
        manifest = self.manifest()
        store = self.store_for(manifest)
        store.upsert(self.task_id, status=contracts.STATUS_HALTED,
                     halt_class=contracts.HALT_GATE_REFUSED, baseline_sha=self.baseline)
        promoted = verify.startup_reverify(manifest, store, FakeAdapter())
        self.assertEqual(promoted, [])
        self.assertEqual(store.get(self.task_id)["status"], contracts.STATUS_HALTED)

    def test_blocked_and_landed_records_are_left_alone(self):
        manifest = self.manifest()
        store = self.store_for(manifest)
        sha = self.land_a_commit()
        store.upsert("T-2", status=contracts.STATUS_BLOCKED, baseline_sha=self.baseline)
        store.upsert(self.task_id, status=contracts.STATUS_LANDED, landing_ref=sha,
                     verify={"at": "2026-08-25T00:00:00+00:00"})
        promoted = verify.startup_reverify(manifest, store, self.landed_adapter(sha))
        self.assertEqual(promoted, [])
        self.assertEqual(store.get("T-2")["status"], contracts.STATUS_BLOCKED)

    def test_a_dangling_merge_is_aborted_before_any_record_is_re_verified(self):
        manifest = self.manifest()
        store = self.store_for(manifest)
        commit_on_branch(self.repo, "relay/T-1", {"shared.txt": "task\n"}, "task work", base="main")
        _repo.git(self.repo, "checkout", "-q", "main")
        commit_on_branch(self.repo, "main", {"shared.txt": "operator\n"}, "local work")
        _repo.git(self.repo, "merge", "--no-ff", "relay/T-1", check=False)
        self.assertTrue(gitread.merge_head_exists(self.repo))
        verify.startup_reverify(manifest, store, FakeAdapter())
        self.assertFalse(gitread.merge_head_exists(self.repo))


if __name__ == "__main__":
    unittest.main()


class GitReadFailures(VerifyCase):
    """A verdict is re-run on repaired repos days later, so a ref that no longer resolves has
    to become a blocking skip rather than a traceback (review finding #11)."""

    def test_a_baseline_that_no_longer_resolves_is_a_blocking_skip_not_a_raise(self):
        sha = self.land_a_commit()
        record = self.record(landing_ref=sha)
        record["baseline_sha"] = "0" * 40
        verdict = verify.verify(self.manifest(), record, FakeAdapter(), scope=verify.SCOPE_CODE)
        check = verdict.checks["new_commit_since_baseline"]
        self.assertEqual(check["result"], verify.SKIPPED)
        self.assertTrue(check["blocking"])
        self.assertFalse(verdict.landed)

    def test_startup_reverify_survives_a_record_whose_baseline_is_gone(self):
        manifest = self.manifest()
        store = state.StateStore(manifest.path, manifest.project.repo,
                                 home=os.path.join(self.tmp.name, "home"))
        store.upsert(self.task_id, status=contracts.STATUS_HALTED, baseline_sha="0" * 40,
                     landing_ref="deadbeef")
        self.assertEqual(verify.startup_reverify(manifest, store, FakeAdapter()), [])
        self.assertEqual(store.get(self.task_id)["status"], contracts.STATUS_HALTED)

    def test_a_mirror_rule_the_runner_cannot_read_back_blocks_rather_than_skips(self):
        sha = self.land_a_commit()
        toml = self.toml.replace("mirror = []", 'mirror = ["release"]')
        verdict = verify.verify(self.manifest(toml, name="bad-mirror.toml"),
                                self.record(landing_ref=sha), self.landed_adapter(sha))
        check = verdict.checks["mirror_equals_head"]
        self.assertEqual(check["result"], verify.SKIPPED)
        self.assertTrue(check["blocking"], "an unreadable mirror rule let the task read landed")
        self.assertFalse(verdict.landed)
