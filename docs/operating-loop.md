# Operating Loop (interim)

How the day runs while Relay is being built, and how real work in other repos feeds its plan.

**This document expires in pieces.** Every manual step below names the implementation unit from
`docs/plans/2026-08-25-1346-feat-relay-outer-loop-plan.md` that retires it. When that unit ships,
delete the step. When every step carries a shipped unit number, delete the file.

## The invariant

Relay is the residue of other projects. It is never built from imagination and never gets its own
slot in the day. You run real tasks in a real repo through a real gate, and the run tells you what
to build. Right now the runner is `prototype/run-sweep.sh` and you are standing in for the parts
that do not exist yet.

## Before the run

**Check the egress policy.** Confirm the target repo has an active `cross_model_review_mode: off`
in `.compound-engineering/config.yaml` or `config.local.yaml` at its root. Without it the checkout
resolves to `auto`, and any run reaching `ce-plan`, `ce-doc-review`, or `ce-code-review` sends the
full document or diff to a third party provider with no disclosure in a non-interactive run. See
`ce-doc-review-cross-model-pass-sends-docs-to-third-party-models.md` in the workspace corpus. As of
2026-08-25 this is set in relay, support-workbench, the Integrel workspace, and Cratekit on main.
A new repo has it unset. *No unit yet. This is a proposed plan edit, see Feedback below.*

**Pick a queue, not a task.** Three questions per candidate card:

1. Is it independent of everything else in this run? A dependent task belongs in a later run.
2. Does its plan or card text touch any path under `.claude/`? A hit means attended only, because
   `dontAsk` denies edits there regardless of the tool allowlist. *Retired by U5, requirement R41.*
3. Can a fresh context act on it with no questions? A one line card is an idea, not a task. A
   headless process cannot ask you anything, and a task whose brief says stop and ask should be
   marked excluded rather than queued.

## During the run

**Launch and leave.** Start the run, then go do other work. This is the whole economic case. An
hour long unattended run you sit and watch is strictly worse than doing the task by hand.

The attended lane is whatever the day actually requires, plus the cards that failed the pre-flight.

## After the run

**Verify from git and the tracker, never from the run's report.** A headless run has every
incentive to report success. Each task is landed, blocked, or timed out. Landed means the work is
where the shipping mode says it belongs and the tracker record names the landing; one half without
the other is a partial landing and halts the run. *Retired by U8.*

**Diagnose a block from the transcript.** Find the denial or the deliberate stop. This is currently
the highest value twenty minutes of the day, because you are hand running the feature that classifies
halts. *Retired by U7.*

**Harvest the learning.** Every run yields a confirmed requirement, a requirement nobody wrote down,
or a defect. File it before closing the session. Routing: a learning about a repo's own code goes to
that repo's `docs/solutions/`, a learning about the runner, the harness, or the loop goes here.
*Retired by U9, which gives the closeout process the compound duty.*

## Rotating the projects

Two target repos, structurally different in the way that matters:

| | support-workbench | Cratekit |
|---|---|---|
| Tracker | Jira IW | GitHub Projects, `scripts/board.py` and `board.yml` |
| Shipping mode | local merge to main, plus a mirror push | PR terminal |
| External gate | `hooks/pre-push` running `scripts/test.sh` | GitHub Actions `ci.yml` |
| Gate timing | before the push, locally | after the push, remotely |

Anything built while running only one of them hardcodes to that one and works every time, which is
how you would fail to notice. Give each project fixed days, or alternate. Neither should go two
weeks without a run.

Cratekit matters more than its card count suggests. The plan commits to a GitHub Projects adapter
in U4, designed on paper with no run behind it, while `scripts/board.py` already exists in that repo
doing some of the same reading. The first Cratekit run is that adapter's only evidence.

## The rule about what gets built

The plan is the build queue. Evidence from a run edits the plan rather than bypassing it. A run that
contradicts a requirement, or demands one nobody wrote, is a plan edit with the run named as its
source. A feature that no run has demanded and no requirement covers is not work yet.

## One time per project

Before a repo joins the rotation, confirm the three qualifying properties hold and record how, since
the manifest will carry that as data:

- **Independence.** At least two queued tasks that do not touch each other.
- **Durable state.** Between tasks, everything that matters is in git or on the tracker.
- **External gate.** Something refuses a broken change without any agent's agreement. Confirm it
  actually fails on a broken change and runs on the branch a task would push.

Then set the egress policy, and read whatever board tooling already exists so the adapter does not
reinvent it.

## Feedback into the plan

A running list of edits this loop has generated. Fold them in at the next plan revision.

- **2026-08-25.** The pre-flight needs an egress check beside R41's `.claude/` scan: refuse to launch
  against a repo whose `cross_model_review_mode` resolves to `auto`, since a task process reaching
  `ce-plan` egresses the plan document silently. Source, the ce-plan run that produced this repo's
  own plan. Belongs with the manifest's qualifying properties.
