---
title: A foreign untracked file from another session fails the tree clean preflight, and unclean_exit blames the task for it
date: 2026-09-01
category: workflow-issues
module: runner
problem_type: workflow_issue
component: runner
severity: medium
root_cause: missing_workflow_step
resolution_type: workflow_improvement
related_components: [gitwrite, gitread, run-loop, contracts, verify, summary]
applies_when:
  - "Relay is running an unattended manifest against a repository other sessions also work in, including its own"
  - "a separate session writes an artifact, such as a plan file, into the repo while a manifest targeting that repo may still be running"
  - "a merge or the next task's launch runs the tree clean check, which fails on any untracked file regardless of who wrote it"
  - "a halt is being diagnosed as unclean_exit and the dirty path named as evidence was not written by the halted task"
  - "a task branch is complete and only the landing failed, so the hand landing repair applies"
symptoms:
  - "preflight refuses before launching the next task, failed check tree_clean"
  - "the merge tail's clean check fails right after the same task's own gate passed"
  - "the record halts with class unclean_exit and a Cause line reading left the tree dirty on {branch}"
  - "the halted task's own diff, commits, and gate are clean; the dirty evidence names a file the task's plan never mentioned"
  - "the file reappears after being moved out of the tree, because a separate session restored it without knowing why it vanished"
tags: [foreign-untracked-file, tree-clean-preflight, unclean-exit, cross-session-write, hand-repair, verify, live-run, cause-line]
---

# A foreign untracked file from another session fails the tree clean preflight, and unclean_exit blames the task for it

## Context

Relay round eight ran live against Relay's own repository, `~/.relay/manifests/relay-round8.toml`. While it was running, a separate brainstorm session, working in the same working tree, wrote an untracked file, `docs/plans/2026-09-01-1255-feat-backend-routing-plan.md`, at 12:55 on 2026-09-01. Nothing about that write touched a task's own branch or commits; it landed straight into the working directory the runner also reads.

Twice, once around the runner's handling of task 51 and again around task 44, the tree the runner stood on carried that file, and its own clean checks refused to proceed. Both halts were recorded as class `unclean_exit`. A babysitter session watching the round moved the file out of the tree each time to unblock the run. The writing session, seeing only that its own file had disappeared and not knowing why, put it back, until the babysitter reached it directly and the two agreed on a handoff: the file stays outside the tree while runs are live, and the babysitter restores the newest copy once they finish.

The hazard had been licensed explicitly. The brainstorm session's own brief said reading files was always safe and only git operations needed the runner's status checked first, and the babysitter session later named that as its own handoff's gap: an untracked file is enough to fail the merge preflight with zero commits and zero git operations (session history). The same signature had also fired earlier in a self inflicted form, when a killed codex task left its own untracked plan file behind and preflight refused before the next test could run (session history).

No task misbehaved. The gates that failed were the runner's own, and the record is honest about what it found: a dirty tree. The halts were cheap, a few minutes each to repair by hand, but they were avoidable, and the mechanism worth writing down is why the runner cannot tell the difference between its own mess and someone else's.

**The runner's definition of clean does not distinguish provenance.** Preflight runs before every task launch, and the first of `PREFLIGHT_CHECKS` (`skills/relay/scripts/relay/gitwrite.py:241`, the tuple `("tree_clean", "on_default", "head_equals_remote", "no_task_branch")`) is `_tree_is_clean` (`gitwrite.py:244`). That function reads `gitread.status_porcelain(repo)` (`skills/relay/scripts/relay/gitread.py:35`), which runs plain `git status --porcelain` with no `-u` override, so git's default untracked mode reports every untracked, non ignored file as its own `??` line. `_tree_is_clean` then returns `not porcelain.strip()`. A foreign untracked file makes that string non empty exactly the way a leftover edit would. The check has no way to ask whether the dirt is its own.

When preflight fails on any check, the run loop raises a halt of class `HALT_UNCLEAN_EXIT`, naming the failed check and its evidence, regardless of which of the four checks tripped. That is one of three places this can fire on the same foreign file. The second is later in the same task's own merge: `gitwrite.local_merge_tail` runs the manifest's gate on the task branch and, right after the gate passes, checks `gitread.is_clean(repo)` again (`gitwrite.py:366`) before it fetches and merges; a failure there raises the same `HALT_UNCLEAN_EXIT`. Untracked files are not branch scoped, checkout only moves tracked paths, so a file sitting in the working directory shows up in this check on whichever branch happens to be checked out when it runs. The third is `resume_disposition` (`gitwrite.py:429`), the check a run makes before letting the next task start past a halt, which opens with the identical `_tree_is_clean` call for the same reason: a dirty tree there might be evidence worth preserving, so the runner never guesses at what to do with it.

The Cause line both halts printed comes from one template. `contracts.HALT_LINES[HALT_UNCLEAN_EXIT]` (`skills/relay/scripts/relay/contracts.py:560`) reads `left the tree dirty on {branch}`, rendered by `summary.cause_line` from whatever evidence the raising site attached. Read on its own, that sentence names the branch the runner was on, not the file that caused the refusal or who wrote it, which is why the session watching the halt has to read the evidence, the `tree` list of status lines the check recorded, rather than the Cause line alone.

The runner never attempts to fix this itself. A repository wide search of `skills/relay/scripts/relay/` turns up two occurrences of the string `git clean`: the `DISALLOWED_TOOLS` entry at `contracts.py:356`, a permission denylist entry that keeps the task process from running it, and a comment referencing that same entry. Neither is a call the runner makes. There is no call to `git clean`, `git stash`, or any other command that would remove an untracked file anywhere in the runner's own git wrappers. When the tree is dirty for any reason, the runner's only move is to stop and say so; recovering the tree is the operator's job.

## Guidance

**Rule one: while a Relay run is live against a repository, every other session writes its artifacts outside that working tree.** Live is answered by the runner's own state, not by guessing whether it looks busy: `python3 skills/relay/scripts/relay_cli.py status <manifest>` reads state only, never takes the lease, and prints `status: running` while a lease is held. Check every manifest that could be targeting the repository in question; a run is live until its status word says otherwise. Draft the file in a scratch location, a temp directory, another worktree, anywhere the runner's working tree cannot see it, and only move or write it into the tree once status confirms nothing is running there. When a babysitter session is already watching the run, its coordination beats guessing: it can tell a writing session exactly when the tree is safe to touch, and it is the right session to restore an artifact that had to wait, since it knows why the file was parked and knows to bring back the newest copy.

**Rule two: when an `unclean_exit` halt shows a dirty tree, check whether the dirt is foreign before blaming the task.** The evidence the halt recorded is the first lines of `git status --porcelain` at the moment of refusal. Read those lines before reading the Cause line as an accusation against the task. A path the task's own plan, diff, and commits never mention, especially one whose name or timestamp lines up with another session's known activity, points at another writer, not a defect in the merge. Confirm against `git log` on the task branch and the halt's own branch evidence; if the task's commits are clean and the offending path is not among them, the fix is to relocate the foreign file and land the branch, not to debug the task.

**Rule three: when the task branch is complete, repair by parking the file and hand landing the branch, never by re running the task.** The sequence, proven twice in this incident:

1. Move the foreign file to a location outside the repo. Park it, do not delete it, it belongs to another session.
2. `git checkout main`.
3. Merge the task branch: `git merge --no-ff relay/<id>`.
4. Add an empty marker commit whose message contains `Closes #<id>`: `git commit --allow-empty`. This is what lets verify recognize the hand landing. `_task_pattern` (`skills/relay/scripts/relay/verify.py:265`) requires the id as `#<id>` bounded as a word, and `hand_landing` (`verify.py:273`) scans every commit message between the baseline and head for it. The merge commit's own subject, `Merge relay task <id> from relay/<id>`, does not contain `#<id>`, so without the marker the closing reference check stays a blocking skip and the record never promotes. See `hand-landing-repair-lands-only-when-a-commit-names-the-issue-number-as-a-word.md`, whose repair this reuses.
5. Push. The pre push hook runs the suite, so this is also the gate.
6. Delete the task branch.
7. Set the tracker card to its terminal status.
8. `python3 skills/relay/scripts/relay_cli.py verify <manifest> <id>` promotes the record to landed.

Return the parked file to its owning session, or restore it once every run against the tree has ended.

## Why This Matters

The runner's clean checks exist to protect the merge, not to police who else uses the directory. They were built, correctly, on the assumption that the only source of tree state between tasks is the runner's own sequence: a task's commits, its gate, a closeout's commits. A repository is not actually exclusive to the runner just because the runner assumes it. Relay running against its own repository means every session working in that repository, human or agent, planning or building, shares one working tree with a process that halts the instant it finds anything unexpected there. That is a real constraint on how the repository gets used while a run is live, not a flaw in the check; a tree clean preflight that ignored untracked files would let a task launch on top of someone else's half written draft and could commit or clobber it.

The task processes themselves were never confused. Task 44's own process encountered the foreign file mid run and left it exactly where it was, judging it outside its scope (session history). The defect surface is purely the orchestration layer, where the merge time checks meet a shared filesystem, so the fix taken was operational rather than a code change: nothing weakened or special cased the preflight (session history).

The cost of getting this wrong compounds in the specific way this incident showed. The first halt was cheap and legible: the babysitter read the evidence, recognized a foreign file, moved it, and the run continued. The second cost more, because the writing session, with no visibility into the runner or the babysitter's reasoning, saw its own file vanish and did the only sensible thing from its side: put it back. Two well behaved sessions, coordinating through the filesystem instead of with each other, recreated the same hazard. The fix was not a smarter check; it was the two sessions talking, which is why this doc states the rule as a coordination protocol rather than a workaround. A repository being run unattended by Relay is a shared resource during that window, and the tree clean checks are the mechanism that makes the sharing visible rather than silent.

## When to Apply

- Before writing any file into a repository's working tree, when a Relay manifest might be targeting that repository. Check `relay_cli.py status <manifest>` for every manifest that could apply; if any reports `running`, write elsewhere until it does not.
- When a babysitter session exists for a live run, coordinate with it directly rather than inferring the safe window from the run's visible pace. It holds the context a writing session does not: which tasks are in flight, when the tree will next be quiescent, and what already got parked and why.
- When diagnosing any halt of class `unclean_exit`. Read the evidence's `tree` lines before the Cause line; a path outside the halted task's own diff and commits points at another writer, not the task. The backgrounded command case and the usage window case each own the same signature from other causes; rule all three out from the evidence rather than assuming any one.
- When a file that was moved out of the way during a halt reappears, or a session reports its own file disappearing near a Relay run's timestamps. That is the signature of this miscoordination, not file corruption or a runner defect.

## Examples

### The failure trace

1. 12:55, 2026-09-01: a brainstorm session, told that only git operations were dangerous, writes `docs/plans/2026-09-01-1255-feat-backend-routing-plan.md` into the working tree. The file is untracked and not gitignored.
2. Round eight's runner reaches its next clean check, the merge tail's `gitread.is_clean` or the next task's preflight, and finds the plan file in `git status --porcelain`.
3. The record halts, class `unclean_exit`. The Cause line reads `left the tree dirty on relay/51`, or whichever branch was checked out. The evidence's `tree` field names the plan file, a path task 51's own commits never touch.
4. The babysitter session reads the evidence, recognizes the file as foreign, parks it outside the tree, and hand lands the completed branch: merge, marker commit `c66701f` whose body carries `Closes #51`, push, verify promotes the record to landed.
5. The writing session, seeing its file gone and not knowing why, restores it at 14:06. The identical halt recurs around task 44 on the same file, repaired the same way with marker commit `67a2a79` carrying `Closes #44`. Commit SHAs are local history; issues 51 and 44 on `philgutowski/relay` are the durable references.
6. The babysitter contacts the writing session directly. The two agree: the file stays outside the tree until `relay_cli.py status` no longer reports `running` for every manifest targeting the repo, and the babysitter restores the newest copy afterward.

### The correct pattern

1. Before drafting, the writing session checks `python3 skills/relay/scripts/relay_cli.py status <manifest>` for every manifest that could target the repo, or asks the babysitter session already watching them.
2. It drafts the artifact in a scratch location outside the repository's working tree.
3. It waits for status to report something other than `running`, or for the babysitter's all clear.
4. Only then does it write or move the file into the tree, where the next preflight and merge tail see a tree with nothing in it the runner did not put there itself.

No halt fires under this pattern, because the runner's tree clean checks never observe a file they cannot account for.

## Related

- `docs/solutions/workflow-issues/hand-landing-repair-lands-only-when-a-commit-names-the-issue-number-as-a-word.md` is the canonical hand landing repair this doc's rule three reuses, and the reason the marker commit must name `#<id>` as a word. This incident adds a fourth producer to its list: a foreign untracked file from a concurrent session, which needs the opposite of its step for a task's own stray file, park it, never commit it to the task branch.
- `docs/solutions/workflow-issues/headless-turn-end-is-exit-backgrounded-command-is-killed.md` owns the other, task internal cause of the same signature: a backgrounded command killed at turn end leaving the task's own dirt behind. The two docs are siblings under one halt shape.
- `docs/solutions/workflow-issues/quota-exhaustion-reads-as-no-envelope-and-the-rate-limit-telemetry-is-already-discarded.md` is the family precedent for a record that is accurate about the evidence and wrong about the world, and its diagnostic chain for a dirty tree gains a third check from this doc: backgrounded command, usage window, foreign untracked file.
- `docs/solutions/workflow-issues/change-spanning-a-live-template-and-a-frozen-module-breaks-the-landing-run.md` is a contrast, not a duplicate: the same surface shape, a landing time halt blaming the wrong thing during a live run, but the mechanism is the runner's own frozen imports going out of phase with a live template, not a second actor writing into the tree.
- `docs/solutions/logic-errors/continue-past-halt-checked-general-state-blind-to-the-branch-its-own-skip-left.md` supplies the design rationale for why the halt happens at all: the runner never resets, stashes, or deletes anything to make a disposition pass, so a foreign file halts the run rather than being parked automatically.
- `docs/solutions/workflow-issues/self-hosted-run-cannot-observe-the-code-its-own-tasks-land.md` is the parent hazard family: Relay running against its own repository creates failure modes invisible from inside the process.
