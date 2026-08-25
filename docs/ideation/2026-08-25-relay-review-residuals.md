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

| # | Severity | Where | What |
|---|---|---|---|
| 19 | P2 | `adapters/github.py:116` | A board read that failed is reported as "not terminal" rather than unknown, so an unreadable tracker looks like a card that did not move. |
| 20 | P2 | `adapters/markdown.py:110` | The markdown adapter can only ever report open or closed, so KTD6's route for a missing envelope, which needs the card in `in_review_status`, can never fire for it. The obvious workaround, setting `in_review_status = "open"`, makes the route fire for every unclosed card. |
| 21 | P2 | `gitwrite.py:351` | The closeout scope check diffs commits only, so a closeout that leaves the working tree dirty or drops an untracked file outside the allowed paths passes it. |
| 22 | P2 | `run.py:295` | The timeout cause line prints `?` minutes because the evidence carries seconds under different key names. |
| 23 | P2 | `run.py:354` | The `partial_landing` cause line renders with no evidence at all: the halt passes a `checks` dict and the template names `sha` and `card_status`. |
| 25 | P2 | `summary.py:67` | The `runner_crashed` line always reads "during halted", because the record is passed after the evidence and its post crash status wins over `status_before`. |
| 27 | P2 | `verify.py:177` | Partly addressed. A mirror rule the runner cannot parse now blocks; a mirror that is configured and pushed but whose ref cannot be read back is still worth a second look. |
| 18 | P2 | `adapters/markdown.py` | Applied in eae48d5. Kept here only because it was introduced by this milestone's own simplification pass and is a useful reminder that a mechanical edit can leave both forms behind. |
| 28 | P3 | `closeout.py:69` | `depth_for`'s `gate_refused` parameter is unreachable from any production caller. |
| 29 | P3 | `verify.py:50` | `LOCAL_MERGE` is defined and never used. |
| 30 | P3 | `relay_cli.py:11` | A comment still says the CLI "lands in U10", which it has. |

Findings 22, 23 and 25 share one fix path and one missing test: there is no `tests/test_summary.py`,
and the only assertion on a cause line is that it is non empty. A table test rendering every entry
in `HALT_LINES` from a realistic record and asserting no `?` survives would have caught all three.

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
