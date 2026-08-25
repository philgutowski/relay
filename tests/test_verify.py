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

    def land_a_commit(self, message="task work"):
        """Put one new commit on main and push it, the state a passing tail leaves behind."""
        sha = commit_on_branch(self.repo, "main", {"src/feature.py": "value = 1\n"}, message)
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
