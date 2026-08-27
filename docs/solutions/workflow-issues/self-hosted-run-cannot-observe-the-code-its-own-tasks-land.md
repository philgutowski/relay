---
title: A run cannot observe the runner code its own tasks land, so its terminal record is not evidence about that change
date: 2026-08-27
category: workflow-issues
module: runner
problem_type: workflow_issue
component: runner
severity: medium
root_cause: missing_workflow_step
resolution_type: workflow_improvement
related_components: [state-store, run-loop, launch, contracts, summary]
applies_when:
  - "Relay is running against its own repository, so a Task lands a change to a module the live Runner process already imported"
  - "the landed change alters what the Runner writes to state.json, such as a new key in the terminal record"
  - "the Runner was launched with relay run <manifest> --detach and stays resident for the whole Manifest"
  - "an operator or a later session verifies the change by reading the artifact the landing run itself produced"
  - "any long lived agent driver edits its own source mid run"
symptoms:
  - "task T-2 landed merge c5b20a0 at 15:26:36 adding cli_version_observed to StateStore.write_terminal, and the same run's terminal record written at 15:55:32 carries no such key"
  - "the terminal record reads cli_version 2.1.245 with no cli_version_observed, which is the pre merge write_terminal output"
  - "the record reads as the change having failed to land, while git shows the merge on main and all three run.py call sites passing cli_version_observed"
  - "relay summary --json reports cli_version_observed None, which is also exactly what a genuinely failed version probe reports"
  - "the full suite is green at 376 tests, so the code and the artifact disagree with no defect present"
tags:
  - self-hosting
  - long-lived-process
  - stale-import
  - terminal-record
  - cli-version-drift
  - verify-landed
  - unattended-run
  - state-json
---

# A run cannot observe the runner code its own tasks land, so its terminal record is not evidence about that change

## Context

Relay is an unattended outer loop. The Runner is a single long lived Python process that reads
one Manifest, launches one fresh headless `claude -p` Task process per Task, serially, and
decides each outcome by Verify-landed. `relay run <manifest> --detach` starts it in its own
session (`skills/relay/scripts/relay/cli.py:145` to `:163`, a `subprocess.Popen` with
`start_new_session=True`, prefixed with `caffeinate -i` where that binary exists). Python
imports `run.py`, `state.py`, `launch.py`, `summary.py` and the rest of the package once, when
that process starts. Those module objects are what the process holds for the whole run,
however long the run lasts and whatever the repository under it does in the meantime.

When Relay runs against its own repository, that ordinary fact becomes a verification trap.

Round two of Relay on Relay used `~/.relay/manifests/relay-self-round2.toml`, two Tasks, T-2
and T-3, the markdown Tracker adapter, `local_merge` Shipping mode. The state directory was
created at 14:58:42 EDT on 2026-08-27, with `runner.log` first written at 14:58:46. T-2 landed
at merge `c5b20a0`, committed 15:26:36 EDT. T-3 landed at merge `f774683`, committed 15:52:07
EDT. The head of `main` is `bd0fe00` and matches `origin/main`. The run's terminal record was
written at 15:55:32 EDT (`"written_at": "2026-08-27T19:55:32.479047+00:00"` in that state
directory's `state.json`).

T-2's whole purpose was to make one thing visible in `state.json`: drift between the pinned
`contracts.CLI_VERSION_TESTED` (`skills/relay/scripts/relay/contracts.py:14`, `"2.1.245"`) and
the `claude` binary actually installed. The task itself came from a `learnings` reviewer in the
eight reviewer `ce-code-review` of the U4 to U11 milestone on 2026-08-25, which wrote it down at
`docs/ideation/2026-08-25-relay-review-residuals.md:87` as the terminal record's `cli_version`
always being the pinned `CLI_VERSION_TESTED` and never the CLI that actually ran, filed there so
nothing from the review would be dropped (session history). It landed as a coherent change across
the Runner package.

- `launch.cli_version(env)` at `skills/relay/scripts/relay/launch.py:77` to `:96`, a
  `claude --version` probe that fails closed to `None` rather than raising.
- `observed_cli_version = launch.cli_version(env)` at `skills/relay/scripts/relay/run.py:145`,
  deferred past the `EXIT_CONFIG` and `EXIT_LEASE` early returns so a run that never gets
  going does not pay for a blocking subprocess call.
- A new `cli_version_observed` parameter on `StateStore.write_terminal`
  (`skills/relay/scripts/relay/state.py:395` to `:406`), written into every terminal record.
- The same key in the Lease reclaim path's placeholder record (`state.py:276` to `:277`).
- All three `write_terminal` call sites passing it (`run.py:190` to `:192` for the halt path,
  `:196` to `:197` for completion, `:207` to `:209` for the crash path inside the `finally`).
- The key in the summary payload (`skills/relay/scripts/relay/summary.py:151` to `:152`).
- `tests/stub-claude/claude:82` to `:84`, teaching the stub to answer `--version`.
- Tests at `tests/test_launch.py:302` to `:341`, `tests/test_run.py:247` to `:263`, and
  `tests/test_state.py:219` to `:231`.

Then the terminal record that same run wrote, twenty nine minutes after its own Task merged the
replacement, came out like this:

```json
{"cli_version": "2.1.245", "halt_class": null, "halt_task": null,
 "run_status": "completed", "written_at": "2026-08-27T19:55:32.479047+00:00"}
```

No `cli_version_observed` key at all. That is the pre merge `write_terminal` output, written by
the `state.py` module object the process imported at 14:58.

The change is correct. The suite is green at 376 tests, twelve more test methods than at the
run's baseline commit `2a1721a` (357 methods there, 369 at `bd0fe00`; the collected count runs
ahead of the method count because several case classes subclass another and re run its inherited
methods). The External gate agreed
during the run itself: the pre push hook recorded in the T-2 push's `git_ops` entry ran 373
tests and passed, before T-3 added the rest.

## Guidance

**The run that lands a change to the Runner package is the one instrument that cannot verify
it.** Read that run's `state.json`, or its `relay summary`, as a record written by the code as
it stood when the Runner started, not by the code now on `main`.

Two things are frozen at Runner launch and two are not. The line is whether the thing is a
Python module imported into the Runner process, or a file opened at the moment of use.

Frozen for the life of the Runner process:

- Every module in `skills/relay/scripts/relay/`. `run.py`, `state.py`, `launch.py`,
  `classify.py`, `closeout.py`, `verify.py`, `summary.py`, `contracts.py`, the adapters.
- The Manifest, loaded once by `cmd_run` at `cli.py:121` before it hands the object to
  `run_module.run`, and never re read by `run.py`.

Live, re read after a merge lands mid run:

- The brief templates in `skills/relay/templates/`. `brief.render` calls `_template_text`, which
  opens the file every time (`skills/relay/scripts/relay/brief.py:89` to `:91`, called per render
  at `:137`), so a Task that lands a template edit changes the brief the next Task in the same
  run receives.
- The Tracker, read through the adapter at `origin/<default_branch>` per Task.

The clean way to hold this is that a process started after the merge sees the merge and a
process started before it does not, and Relay runs both kinds at once. The same round two run
showed the split directly. T-2's Closeout process, a fresh `claude` invocation launched at
19:28 UTC, read `run.py` and `launch.py` from disk after the merge and reasoned correctly about
the post merge line numbers, confirming the lease seam at `store.acquire()` on line 129,
`launch.cli_version()` on line 145, and the `try/finally` on line 156. The Runner that spawned
that Closeout was still executing the pre merge modules while it did so (session history). One
process in the run observed the landed code and the other could not, minutes apart, and nothing
in the run marked the asymmetry.

So the correct instruments for a landed Runner module change are the ones that load the new
code:

1. **The suite.** `python3 -m unittest discover -s tests` from the repo root imports the
   post merge modules. If T-2's tests pass, `write_terminal` takes the parameter.
2. **The next run.** A fresh `relay run` imports the merged package at its own launch and
   writes the new record shape.
3. **Reading the source at head.** `git show HEAD:skills/relay/scripts/relay/state.py` says
   what the code is. The old process says only what the code was.

**Do not treat the absent key's stand in as a probe result.** This is the sharp edge. Run
`relay summary <manifest> --json` today, with the post merge `summary.py`, against that same
run's state file and you get:

```
{'run_status': 'completed', 'cli_version': '2.1.245', 'cli_version_observed': None}
```

`summary.py:152` reads `terminal.get("cli_version_observed")`, the key is not in the record, and
`.get` returns `None`. But `None` is also exactly what `launch.cli_version` returns when the
probe genuinely fails: a missing binary, a nonzero exit, a timeout, a decode error, or output
whose leading token is not a version (`launch.py:77` to `:96`, and the fail closed contract that
`docs/solutions/logic-errors/version-probe-between-lease-acquire-and-try-finally-must-never-raise.md`
explains is load bearing for the Lease). A never written key and a failed probe are the same
value at the reader. Nothing in the summary distinguishes them. The only thing that does is the
raw record, where one shows the key absent and the other shows it present and null, which is the
distinction `tests/test_state.py:230` pins in a comment: the field defaults to `None`, not to
omitted.

## Why This Matters

The failure mode is a false negative on a change that works, and it lands on the reader with
maximum confidence. `state.json` is a durable record of a completed unattended run, produced by
the machinery whose entire job is to decide outcomes from evidence rather than from a process's
claims about itself. It is the last artifact anyone would think to distrust. A session that
reads the terminal record, sees no `cli_version_observed`, and concludes the change did not work
will go looking for a defect that is not there, in code that is already correct and already
tested, and may revert or rewrite it.

The cost compounds because the wrong instrument agrees with itself on re inspection. Re read the
record and the key is still missing. Run `summary --json` and it is still `None`. Every read of
that run says the same wrong thing, and each repetition raises confidence.

Review does not catch it either. T-2's own Task process ran `ce-plan`, then `ce-doc-review` with
three reviewers, then `ce-work`, then `ce-simplify-code` with three reviewers, then
`ce-code-review` with five reviewers, against a diff spanning `launch.py`, `run.py`, `state.py`,
`summary.py`, the stub `claude`, and three test modules. Eleven distinct reviewer personas read
that change. None of them raised that a Relay run was at that moment executing the pre change
`run.py` and `state.py` (session history). This is not a gap a reviewer is positioned to see: it
is not visible in the diff, only in the relationship between a running process and the tree.

This also draws a boundary on the standing rule from
`docs/solutions/logic-errors/stubbed-seams-agree-by-construction-first-live-run-found-five-contract-defects.md`,
which is that a live run is the only instrument for seams the stub cannot produce, and that
`CLAUDE.md` restates as running one live task against a throwaway target after any change to a
cross process contract. That rule is right, and this is a limit on it: the live run that
*lands* the change is not the live run that verifies it. Its Runner predates the code. A change
to a cross process contract still needs a live run, and it needs the next one.

The symmetry with
`docs/solutions/workflow-issues/headless-turn-end-is-exit-backgrounded-command-is-killed.md` is
worth one sentence. That doc is about the Task process's lifetime, where ending the turn is
exiting and anything backgrounded dies with it. This one is about the Runner's lifetime, where
the process outlives the repository state it started from. Both are cases of an agent reasoning
about a process boundary it cannot see from the inside.

Beyond Relay, this holds for any self hosting agent loop whose long lived driver process edits
its own source: a scheduler that reschedules itself, a build runner that rebuilds its own
runner, a CI orchestrator whose pipeline definition it loaded at start. The landing run always
reports on the code it booted with.

## When to Apply

Apply this whenever all three hold:

- A run landed a change to code the Runner itself imports, meaning anything in
  `skills/relay/scripts/relay/`, as opposed to a change to templates, docs, tests, or the
  Tracker file.
- You are about to judge whether that change worked from an artifact the same run produced:
  `state.json`, `relay summary`, `relay status`, the run's terminal record, its `git_ops` log.
- The observation is "the new behavior is not there."

It applies with equal force to a partial landing. T-3 landed after T-2 in the same run, and
`state.py` was still the pre merge module when T-3's outcome was recorded, because the import
happened once at 14:58 and nothing since re imported it. Later Tasks in a run do not get the
newer Runner.

It does not apply to the reverse direction. If the record *does* show the new shape, that is
real evidence, since the old code could not have produced a key it does not write.

It also does not apply to a Task that changes a brief template, since those are read from disk
per Task and a later Task in the same run does see the edit.

## Examples

**The wrong read.** A future session opens the round two state file and reasons:

> T-2's stated purpose was to record the observed CLI version in the terminal record. The
> terminal record for the run that landed T-2 has `cli_version` and no `cli_version_observed`.
> `relay summary --json` reports `cli_version_observed: None`, which per `launch.py` means the
> probe failed. So the probe is broken.

Both premises are true and the conclusion is wrong twice over. The record predates the merge,
and the `None` is `.get` on an absent key rather than a probe result.

**The right read, in three checks.**

```
$ git log --oneline -1 --format='%h %ci %s' c5b20a0
c5b20a0 2026-08-27 15:26:36 -0400 Merge relay task T-2 from relay/T-2

$ python3 -c "import json; print('written_at:', json.load(open(
    '/Users/pgutowski/.relay/85f17e.../state.json'))['terminal']['written_at'])"
written_at: 2026-08-27T19:55:32.479047+00:00
```

The record is later in wall clock time than the merge, which is exactly what makes the trap
convincing. Wall clock ordering is the wrong comparison. The comparison that decides it is the
merge time against the Runner's *launch* time, 14:58:42 to 14:58:46, which is when the modules
were imported. The merge is after the import, so the running code cannot contain it.

```
$ python3 -m unittest discover -s tests
Ran 376 tests in 142.853s
OK
```

`tests/test_state.py:219` to `:224` passes `cli_version_observed="2.1.247"` into
`write_terminal` and reads it back, and `tests/test_run.py:256` to `:263` drives a full run
under the stub with `RELAY_STUB_CLI_VERSION` set and asserts the observed value diverges from
the pinned one. Green means the parameter exists and the record carries it.

**The next run is where the payoff shows.** The installed binary reports `2.1.247`:

```
$ claude --version
2.1.247 (Claude Code)
```

and `contracts.py:14` still pins `CLI_VERSION_TESTED = "2.1.245"`. Real drift exists. The next
Relay run against this repository will import the merged `state.py`, run the probe at
`run.py:145`, and write a terminal record carrying both, `"cli_version": "2.1.245"` beside
`"cli_version_observed": "2.1.247"`. That record, not this one, is the evidence that T-2
worked. Confirming this is also the cheapest way for a reader to check the whole mechanism for
themselves: launch any run, then compare its terminal record against the round two one.

**The general form.** Before concluding a landed change to the Runner package did not work,
answer one question. Was the process that produced this artifact started before or after the
merge? If before, the artifact is a photograph of the old code, and the answer is in the tests
or in the next run.

## Related

- `docs/solutions/logic-errors/stubbed-seams-agree-by-construction-first-live-run-found-five-contract-defects.md`:
  the parent rule this qualifies. It establishes the live run as the instrument of last resort
  for seams the stub cannot produce. This doc carves out the one live run that cannot serve as
  evidence, the run that landed the change, and its Prevention section is incomplete rather than
  wrong until it names that exception.
- `docs/solutions/workflow-issues/headless-turn-end-is-exit-backgrounded-command-is-killed.md`:
  the other half of the pair. Relay now has one documented process lifetime trap per process,
  the Task process there and the Runner process here.
- `docs/solutions/logic-errors/version-probe-between-lease-acquire-and-try-finally-must-never-raise.md`:
  the sibling from the same merge, written autonomously by T-2's own Closeout process about the
  same `cli_version` work. A reader who acts on its statement that the probe makes drift visible
  in `state.json`, by opening `state.json` from the run that landed it, walks straight into the
  misread this doc prevents.
- `docs/solutions/logic-errors/verify-checked-only-one-direction-of-the-landing-tracker-link.md`:
  nearest neighbour on the same seam, the Runner's write side of landing state against its read
  side. Here both sides are correct and the record still misleads, because of when the process
  was launched.
- `docs/solutions/logic-errors/process-group-kill-resolves-target-lazily.md`: the mirror image.
  There a process group id was resolved too late, at kill time rather than at launch; here a
  module is bound too early, at import rather than at use. Both are defects in when a value is
  fixed relative to a process lifetime, and both were invisible in the only case the tests
  exercised.
