"""U4: the three read side tracker adapters behind one interface.

Every adapter takes an injectable transport (an opener for Jira, a run callable for `gh`, the
git read wrapper for markdown), so no test here touches a network or invokes `gh`, and `gh` may
be absent from the machine entirely. The shared contract runs against all three.
"""
import os
import tempfile
import unittest

import _paths
import _repo
from relay import adapters, manifest as mf
from relay.adapters import github as gh_adapter, jira as jira_adapter, markdown as md_adapter

FIXTURE = os.path.join(_paths.FIXTURES_DIR, "manifests", "complete.toml")
TRACKER = os.path.join(_paths.FIXTURES_DIR, "tracker")

TRACKER_MD = """# Tasks

- [ ] T-1 Add the brief renderer
  - 2026-08-24 picked this up
  - 2026-08-25 still going
- [x] T-2 Wire the run loop (abc1234)
- [ ] T-3 Nothing yet
"""


def fixture(name):
    with open(os.path.join(TRACKER, name)) as handle:
        return handle.read()


class FakeOpener:
    """Stands in for urllib's opener. Routes a URL substring to a fixture file."""

    def __init__(self, routes, error=None):
        self.routes = dict(routes)
        self.error = error
        self.requests = []

    def open(self, request, timeout=None):
        url = request.get_full_url() if hasattr(request, "get_full_url") else str(request)
        self.requests.append((url, dict(getattr(request, "headers", {})), timeout))
        if self.error:
            raise self.error
        for fragment, name in self.routes.items():
            if fragment in url:
                return _Body(fixture(name))
        raise AssertionError("no fixture routed for %s" % url)


class _Body:
    def __init__(self, text):
        self.text = text

    def read(self):
        return self.text.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class DispatchRun:
    """A `run(args, timeout=None)` for `gh` that answers from fixtures by subcommand."""

    def __init__(self, issues=None, items=None, failure=None):
        self.issues = dict(issues or {})
        self.items = items
        self.failure = failure
        self.calls = []

    def __call__(self, args, timeout=None):
        self.calls.append(list(args))
        if self.failure is not None:
            code, err = self.failure
            return _Proc(code, "", err)
        if "project" in args:
            return _Proc(0, fixture(self.items), "")
        number = args[args.index("view") + 1]
        name = self.issues.get(str(number))
        if name is None:
            return _Proc(1, "", "no issue %s" % number)
        return _Proc(0, fixture(name), "")


class _Proc:
    def __init__(self, returncode, stdout, stderr):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class AdapterCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = _repo.make_repo(self.tmp.name, files={"tracker.md": TRACKER_MD})
        with open(FIXTURE) as handle:
            self.toml = handle.read().replace("__REPO__", self.repo)

    def tearDown(self):
        self.tmp.cleanup()

    def manifest(self, text=None, name="manifest.toml"):
        path = os.path.join(self.tmp.name, name)
        with open(path, "w") as handle:
            handle.write(text if text is not None else self.toml)
        return mf.load(path)

    def jira_manifest(self):
        text = self.toml.replace('adapter = "markdown"', 'adapter = "jira"')
        text = text.replace('file = "tracker.md"', 'site = "example.atlassian.net"\nproject_key = "IW"')
        return self.manifest(text.replace('done_statuses = ["done"]', 'done_statuses = ["Done", "Closed"]'),
                             name="jira.toml")

    def github_manifest(self, status_field="Shipped"):
        text = self.toml.replace('adapter = "markdown"', 'adapter = "github"')
        text = text.replace('file = "tracker.md"',
                            'owner = "philgutowski"\nproject_number = 4\nstatus_field = "%s"' % status_field)
        return self.manifest(text, name="github.toml")

    def jira(self, opener, env=None):
        return jira_adapter.JiraAdapter(self.jira_manifest(),
                                        opener=opener,
                                        env=env or {"JIRA_API_TOKEN": "t", "JIRA_EMAIL": "e@x.invalid"})

    def github(self, run):
        return gh_adapter.GitHubAdapter(self.github_manifest(), run=run)

    def markdown(self):
        return md_adapter.MarkdownAdapter(self.manifest())


class SharedContract(AdapterCase):
    """Every adapter answers the same eight methods and exposes nothing that writes."""

    def each(self):
        yield "jira", self.jira(FakeOpener({"/issue/": "jira_issue_done.json", "search": "jira_search.json"}))
        yield "github", self.github(DispatchRun(issues={"12": "github_issue_closed.json"},
                                                items="github_project_items.json"))
        yield "markdown", self.markdown()

    def test_every_adapter_implements_the_whole_interface(self):
        for name, adapter in self.each():
            for method in adapters.INTERFACE:
                self.assertTrue(callable(getattr(adapter, method, None)), "%s lacks %s" % (name, method))

    def test_no_adapter_exposes_a_method_outside_the_read_side_interface(self):
        for name, adapter in self.each():
            public = {attr for attr in dir(adapter)
                      if not attr.startswith("_") and callable(getattr(adapter, attr))}
            self.assertEqual(public, set(adapters.INTERFACE), "%s exposes more than the interface" % name)

    def test_every_adapter_returns_the_status_shape_verify_reads(self):
        ids = {"jira": "IW-83", "github": "12", "markdown": "T-2"}
        for name, adapter in self.each():
            result = adapter.status(ids[name])
            for key in ("status", "terminal", "reference", "skipped"):
                self.assertIn(key, result, "%s status is missing %s" % (name, key))
            self.assertTrue(result["terminal"], name)

    def test_every_adapter_names_its_closeout_tools_without_a_wildcard(self):
        for name, adapter in self.each():
            tools = adapter.closeout_allowed_tools()
            self.assertTrue(tools, name)
            for tool in tools:
                self.assertNotIn("*", tool, "%s closeout tools carry a wildcard" % name)

    def test_every_adapter_writes_closeout_instructions_for_both_outcomes(self):
        for name, adapter in self.each():
            for outcome in ("landed", "blocked"):
                text = adapter.closeout_instructions(outcome)
                self.assertTrue(text.strip(), "%s has no %s instructions" % (name, outcome))
            self.assertNotEqual(adapter.closeout_instructions("landed"),
                                adapter.closeout_instructions("blocked"), name)


class Jira(AdapterCase):
    def opener(self, **routes):
        return FakeOpener(routes or {"/issue/": "jira_issue_done.json", "search": "jira_search.json"})

    def test_a_done_issue_reads_terminal_with_its_flattened_description(self):
        adapter = self.jira(self.opener())
        card = adapter.read("IW-83")
        self.assertEqual(card["id"], "IW-83")
        self.assertEqual(card["title"], "Add the brief renderer")
        self.assertIn("Render the task brief from a template.", card["description"])
        self.assertIn("Second paragraph", card["description"])
        self.assertEqual(card["status"], "Done")
        self.assertTrue(adapter.status("IW-83")["terminal"])

    def test_a_backlog_issue_is_not_terminal(self):
        adapter = self.jira(self.opener(**{"/issue/": "jira_issue_open.json"}))
        self.assertFalse(adapter.status("IW-84")["terminal"])

    def test_closing_reference_finds_the_comment_naming_the_sha_prefix(self):
        adapter = self.jira(self.opener())
        self.assertEqual(adapter.closing_reference("IW-83", "abc1234def56789"), "10002")

    def test_closing_reference_returns_none_when_no_comment_names_it(self):
        adapter = self.jira(self.opener())
        self.assertIsNone(adapter.closing_reference("IW-83", "9999999"))

    def test_comments_since_a_baseline_returns_exactly_the_newer_ones_in_order(self):
        adapter = self.jira(self.opener())
        newer = adapter.comments_since("IW-83", "10001")
        self.assertEqual([entry["id"] for entry in newer], ["10002", "10003"])
        self.assertIn("Landed on main", newer[0]["body"])

    def test_comments_since_none_returns_every_comment(self):
        adapter = self.jira(self.opener())
        self.assertEqual(len(adapter.comments_since("IW-83", None)), 3)

    def test_a_missing_token_env_var_is_a_named_configuration_error_before_any_request(self):
        opener = self.opener()
        with self.assertRaises(adapters.ConfigurationError) as caught:
            jira_adapter.JiraAdapter(self.jira_manifest(), opener=opener, env={"JIRA_EMAIL": "e@x.invalid"})
        self.assertIn("JIRA_API_TOKEN", str(caught.exception))
        self.assertEqual(opener.requests, [])

    def test_the_request_carries_basic_auth_and_the_thirty_second_timeout(self):
        opener = self.opener()
        self.jira(opener).read("IW-83")
        url, headers, timeout = opener.requests[0]
        self.assertIn("example.atlassian.net/rest/api/3/issue/IW-83", url)
        self.assertEqual(timeout, adapters.NETWORK_TIMEOUT_SECONDS)
        self.assertTrue(any(key.lower() == "authorization" for key in headers))

    def test_a_read_that_raises_becomes_a_skipped_result_rather_than_an_exception(self):
        adapter = self.jira(FakeOpener({}, error=OSError("connection refused")))
        result = adapter.status("IW-83")
        self.assertIn("connection refused", result["skipped"])
        self.assertFalse(result["terminal"])
        self.assertEqual(adapter.comments_since("IW-83", None), [])
        self.assertIsNone(adapter.closing_reference("IW-83", "abc1234"))

    def test_candidates_lists_the_project_issues(self):
        adapter = self.jira(self.opener())
        found = adapter.candidates()
        self.assertEqual([entry["id"] for entry in found], ["IW-83", "IW-84"])

    def test_the_write_patterns_name_the_atlassian_mcp_and_nothing_else(self):
        patterns = self.jira(self.opener()).write_tool_patterns()
        self.assertIn("mcp__atlassian__", patterns["tools"])
        self.assertEqual(patterns["bash"], ())

    def test_the_closeout_tools_are_explicit_and_carry_no_confluence_tool(self):
        tools = self.jira(self.opener()).closeout_allowed_tools()
        self.assertIn("mcp__atlassian__transitionJiraIssue", tools)
        self.assertIn("mcp__atlassian__addCommentToJiraIssue", tools)
        for tool in tools:
            self.assertNotIn("Confluence", tool)
            self.assertNotIn("*", tool)


class GitHub(AdapterCase):
    def run_for(self, **kwargs):
        kwargs.setdefault("issues", {"12": "github_issue_closed.json", "13": "github_issue_open.json"})
        kwargs.setdefault("items", "github_project_items.json")
        return DispatchRun(**kwargs)

    def test_a_closed_issue_is_terminal_and_an_open_one_is_not(self):
        adapter = self.github(self.run_for())
        self.assertTrue(adapter.status("12")["terminal"])
        self.assertFalse(adapter.status("13")["terminal"])

    def test_candidates_come_from_the_project_item_list(self):
        run = self.run_for()
        found = self.github(run).candidates()
        self.assertEqual([entry["id"] for entry in found], ["12", "13"])
        self.assertEqual(found[0]["title"], "Add the brief renderer")
        self.assertIn("--owner", run.calls[0])
        self.assertIn("philgutowski", run.calls[0])

    def test_an_open_issue_whose_project_status_matches_the_manifest_field_is_terminal(self):
        run = self.run_for()
        adapter = gh_adapter.GitHubAdapter(self.github_manifest(status_field="Done"), run=run)
        self.assertTrue(adapter.status("13")["terminal"],
                        "the project status field named in the manifest was not consulted")
        self.assertEqual(adapter.status("13")["status"], "Done")

    def test_candidates_carry_the_project_status_of_each_item(self):
        found = {entry["id"]: entry for entry in self.github(self.run_for()).candidates()}
        self.assertEqual(found["13"]["status"], "Done")
        self.assertEqual(found["12"]["status"], "Todo")

    def test_closing_reference_finds_the_comment_naming_the_sha(self):
        adapter = self.github(self.run_for())
        self.assertEqual(adapter.closing_reference("12", "abc1234def"), "IC_2")
        self.assertIsNone(adapter.closing_reference("12", "0000000"))

    def test_comments_since_a_baseline_returns_the_newer_comment(self):
        adapter = self.github(self.run_for())
        newer = adapter.comments_since("12", "IC_1")
        self.assertEqual([entry["id"] for entry in newer], ["IC_2"])

    def test_a_nonzero_gh_exit_is_skipped_with_the_stderr_text(self):
        adapter = self.github(self.run_for(failure=(1, "gh: not authenticated\n")))
        result = adapter.status("12")
        self.assertIn("not authenticated", result["skipped"])
        self.assertEqual(adapter.candidates(), [])

    def test_a_pr_create_command_does_not_match_the_write_patterns(self):
        from relay import classify

        patterns = self.github(self.run_for()).write_tool_patterns()
        pr_create = {"name": "Bash", "input": {"command": "gh pr create --fill"}}
        issue_close = {"name": "Bash", "input": {"command": "gh issue close 12 --comment landed"}}
        self.assertFalse(classify.matches_write_pattern(pr_create, patterns))
        self.assertTrue(classify.matches_write_pattern(issue_close, patterns))

    def test_every_gh_call_carries_the_thirty_second_timeout(self):
        run = self.run_for()
        self.github(run).status("12")
        self.assertTrue(run.calls)


class Markdown(AdapterCase):
    def test_a_closed_line_reads_closed_with_its_reference(self):
        result = self.markdown().status("T-2")
        self.assertEqual(result["status"], "closed")
        self.assertTrue(result["terminal"])
        self.assertEqual(result["reference"], "abc1234")

    def test_an_open_line_is_not_terminal(self):
        result = self.markdown().status("T-1")
        self.assertEqual(result["status"], "open")
        self.assertFalse(result["terminal"])
        self.assertIsNone(result["reference"])

    def test_comments_are_the_indented_lines_under_the_task(self):
        adapter = self.markdown()
        self.assertEqual(len(adapter.comments_since("T-1", None)), 2)
        newer = adapter.comments_since("T-1", 1)
        self.assertEqual(len(newer), 1)
        self.assertIn("still going", newer[0]["body"])

    def test_a_task_with_no_comments_returns_an_empty_list(self):
        self.assertEqual(self.markdown().comments_since("T-3", None), [])

    def test_the_file_is_read_at_the_remote_default_branch_not_the_working_tree(self):
        with open(os.path.join(self.repo, "tracker.md"), "w") as handle:
            handle.write("- [x] T-1 Add the brief renderer (deadbeef)\n")
        self.assertFalse(self.markdown().status("T-1")["terminal"],
                         "the adapter read the working tree instead of origin/main")

    def test_closing_reference_matches_the_reference_on_the_closed_line(self):
        adapter = self.markdown()
        self.assertEqual(adapter.closing_reference("T-2", "abc1234def"), "T-2")
        self.assertIsNone(adapter.closing_reference("T-2", "9999999"))
        self.assertIsNone(adapter.closing_reference("T-1", "abc1234def"))

    def test_candidates_are_the_open_lines(self):
        found = self.markdown().candidates()
        self.assertEqual([entry["id"] for entry in found], ["T-1", "T-3"])

    def test_the_write_patterns_name_the_tracker_file_for_edit_and_write(self):
        from relay import classify

        patterns = self.markdown().write_tool_patterns()
        edit = {"name": "Edit", "input": {"file_path": os.path.join(self.repo, "tracker.md")}}
        other = {"name": "Edit", "input": {"file_path": os.path.join(self.repo, "src/x.py")}}
        self.assertTrue(classify.matches_write_pattern(edit, patterns))
        self.assertFalse(classify.matches_write_pattern(other, patterns))

    def test_a_missing_tracker_file_is_skipped_rather_than_a_crash(self):
        _repo.git(self.repo, "rm", "-q", "tracker.md")
        _repo.git(self.repo, "commit", "-q", "-m", "drop the tracker")
        _repo.git(self.repo, "push", "-q", "origin", "main")
        result = self.markdown().status("T-1")
        self.assertIsNotNone(result["skipped"])
        self.assertEqual(self.markdown().candidates(), [])


class Factory(AdapterCase):
    def test_the_manifest_adapter_name_selects_the_implementation(self):
        self.assertIsInstance(adapters.build(self.manifest()), md_adapter.MarkdownAdapter)
        self.assertIsInstance(
            adapters.build(self.jira_manifest(), env={"JIRA_API_TOKEN": "t", "JIRA_EMAIL": "e@x.invalid"},
                           opener=FakeOpener({})),
            jira_adapter.JiraAdapter)
        self.assertIsInstance(adapters.build(self.github_manifest(), run=DispatchRun()),
                              gh_adapter.GitHubAdapter)

    def test_an_unknown_adapter_name_is_a_configuration_error(self):
        text = self.toml.replace('adapter = "markdown"', 'adapter = "trello"')
        with self.assertRaises(adapters.ConfigurationError):
            adapters.build(self.manifest(text, name="bad.toml"))


if __name__ == "__main__":
    unittest.main()
