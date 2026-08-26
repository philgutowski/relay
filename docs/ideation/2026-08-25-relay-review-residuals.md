# Relay outer loop: review findings not yet applied

Date: 2026-08-25
Source: an eight reviewer code review of units U4 to U11, base 072d068, run id
20260825-174244-6ec89071. Fourteen P0 and P1 findings were applied in eae48d5. These are the
rest, kept here so they are recorded rather than remembered.

## The one decision

**pr_terminal mode is shipped as working and cannot run.** `run.py` halts every task under it
with `ci_undecided` and the message "not wired into the run loop yet", while `manifest.validate`
accepts the mode, `docs/examples/manifest-github-pr.toml` configures it, `brief-pr-terminal.md`
renders for it, and both `SKILL.md` and `README.md` present it as one of two shipping modes with
no caveat. The halt class an operator sees is actively misleading: the documented remedy is
"wait for CI", and there is no PR being checked at all.

Three reviewers found this independently. The parts already exist and are unit tested:
`gitwrite.find_pr`, `gitwrite.poll_ci`, and the `pr_probe` seam in `verify.verify`. Nothing wires
them together and no end to end test covers the mode.

Two honest options. Implement the R50 PR sequence in the run loop, or refuse the mode in
`validate` and mark it unimplemented in the example, the skill, and the README until it is real.
Shipping it as documented and non functional is the only option that is wrong.

## Findings left unapplied

Each was validated against the code. Numbers are the review's stable ids.

| # | Severity | Where | What | State |
|---|---|---|---|---|
| 19 | P2 | `adapters/github.py:116` | A board read that failed is reported as "not terminal" rather than unknown, so an unreadable tracker looks like a card that did not move. | Applied 2026-08-26 |
| 20 | P2 | `adapters/markdown.py:110` | The markdown adapter can only ever report open or closed, so KTD6's route for a missing envelope, which needs the card in `in_review_status`, can never fire for it. The obvious workaround, setting `in_review_status = "open"`, makes the route fire for every unclosed card. | Open |
| 21 | P2 | `gitwrite.py:351` | The closeout scope check diffs commits only, so a closeout that leaves the working tree dirty or drops an untracked file outside the allowed paths passes it. | Applied 2026-08-26 |
| 22 | P2 | `run.py:295` | The timeout cause line prints `?` minutes because the evidence carries seconds under different key names. | Applied 2026-08-26 |
| 23 | P2 | `run.py:354` | The `partial_landing` cause line renders with no evidence at all: the halt passes a `checks` dict and the template names `sha` and `card_status`. | Applied 2026-08-26 |
| 25 | P2 | `summary.py:67` | The `runner_crashed` line always reads "during halted", because the record is passed after the evidence and its post crash status wins over `status_before`. | Applied 2026-08-26 |
| 27 | P2 | `verify.py:177` | Partly addressed. A mirror rule the runner cannot parse now blocks; a mirror that is configured and pushed but whose ref cannot be read back is still worth a second look. | Open |
| 18 | P2 | `adapters/markdown.py` | Kept here only because it was introduced by this milestone's own simplification pass and is a useful reminder that a mechanical edit can leave both forms behind. | Applied in eae48d5 |
| 28 | P3 | `closeout.py:69` | `depth_for`'s `gate_refused` parameter is unreachable from any production caller. | Applied 2026-08-26 |
| 29 | P3 | `verify.py:50` | `LOCAL_MERGE` is defined and never used. | Applied 2026-08-26 |
| 30 | P3 | `relay_cli.py:11` | A comment still says the CLI "lands in U10", which it has. | Applied 2026-08-26 |

Findings 22, 23 and 25 shared one fix path and one missing test: there was no
`tests/test_summary.py`, and the only assertion on a cause line was that it was non empty. That
file now exists as a table over every entry in `HALT_LINES`, one row per class holding what its
production raiser records and citing it by function, plus two runs of the real loop over the
stub. The table found three more placeholder lines the review had not:

- `blocked: ?` on every blocked task, which is the most common non landing outcome there is. The
  blocked route recorded only the stranded head, so the blocker text the class names was dropped.
- `closeout changed ? outside ?`, the same shape as 23: the evidence carried `offending` and
  `reset_to` while the template named `path` and `allowed`.
- `the runner hit an unexpected {error_type} on {task}: {error}`, braces and all, because
  `LINE_FIELD_DEFAULTS` was a hand written list that had gone stale. It is now derived from the
  templates, so a template that gains a field cannot outrun its defaults.

Finding 21's fix also separated two failures the old check conflated. A path outside the allowed
set is `closeout_out_of_scope`; a tree the closeout left dirty entirely inside the allowed set is
`unclean_exit`. Both reset to the pre closeout head and neither pushes.

## Gaps against the existing solutions doc

`docs/solutions/workflow-issues/headless-dontask-blocks-claude-dir-edits.md` is implemented
faithfully, and the branch diff backstop is stronger than the doc's own script. Three gaps:

- The doc resolved the R19 tension by asking the runner to write the blocked comment itself, as a
  narrow carved exception. The code implemented detection only (`blocked_unrecorded`), so a
  blocked and uncommented task still depends on a human reading the summary. The code took the
  opposite position from the doc and nothing records why.
- `cli_version` in the terminal record is always the pinned `CLI_VERSION_TESTED`, never the CLI
  that actually ran, so the doc's ask to notice the gate boundary moving cannot be satisfied.
- `CLAUDE_DIR_SCAN_REGEX` misses markdown link and bold forms of a `.claude/` path, which tracker
  cards commonly contain. Worth amending that doc rather than opening a new one.

## Other residual risks worth knowing

- `verify` runs with `do_fetch=False` everywhere in the run loop, so `head_equals_remote` and
  `mirror_equals_head` read remote tracking refs this runner's own pushes updated. The check
  confirms that the push succeeded, not that the remote agrees, and cannot see a third party
  force push between the merge and the verify.
- `comments_since` returns every comment when the baseline id is not found in the fetched list,
  in both the Jira and GitHub adapters. A deleted baseline comment makes `confirm_blocked_comment`
  report success when the closeout wrote nothing.
- The classify digest is a string keyed dict with no shared contract test, and `run.py` and
  `closeout.py` are now its first production consumers. A renamed key returns `None` silently.

Full artifacts, including every reviewer's evidence: `review.json` and the per reviewer files
under the run id above.
