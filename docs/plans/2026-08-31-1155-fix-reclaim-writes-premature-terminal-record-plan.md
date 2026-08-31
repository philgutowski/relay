---
title: Reclaim No Longer Writes a Premature Run-Level Terminal Record - Plan
type: fix
date: 2026-08-31
artifact_contract: ce-unified-plan/v1
artifact_readiness: implementation-ready
product_contract_source: ce-plan-bootstrap
execution: code
---

# Reclaim No Longer Writes a Premature Run-Level Terminal Record - Plan

## Goal Capsule

**Objective:** an operator watching `relay status` or `relay run --follow`/`relay tail` across a
stale-lease reclaim sees the run's true, current state the whole way through, never a false
"the run already ended" claim manufactured by the reclaim step itself.

**Means:** stop `StateStore._mark_crashed` from writing to `state["terminal"]` (KTD1). It keeps
marking every in-flight record `halted`/`runner_crashed`, exactly as today; it stops writing a
run-level terminal record, because only `run()`'s own true completion, halt, or crash-backstop
path can know whether this run is going to continue.

**Authority hierarchy:** this plan's Key Technical Decisions; then `CLAUDE.md`'s and
`CONCEPTS.md`'s rules for this repo (halt classes are a closed set; the runner never writes to
the tracker; every stop is a named class with evidence); then existing code conventions in
`state.py`, `run.py`, `cli.py`, and `tail.py`.

**Stop conditions:** stop and report a blocker rather than guessing if removing the write causes
`status_word()` to misreport a genuinely dead, never-rerun lease as anything other than
`crashed`, since that path (not `_mark_crashed`'s write) is `status_word()`'s own documented
fallback and must keep working unchanged.

**Execution profile:** single PR, no phased rollout. `ce-work` builds, tests, and lands all units
together.

**Tail ownership:** the calling Relay task process runs the project gate and lands the branch;
this plan does not describe shipping mechanics.

---

## Problem Frame

Round six stage A, 2026-08-30 ~09:55 (source: state dir `926c93f0...`, `runner.log` lines
`reclaimed a stale lease from pid 61799`, `40 halted with class unclean_exit; continuing past
it`). A resume reclaimed the stale lease #47's kill left behind, marked #40 `runner_crashed`,
and then — under `on_halt.continue_past_task_halt` (issue #15) — reclassified #40 as
`unclean_exit` on its own retry and moved on to #37. Two symptoms, one shared cause:

1. `relay status`, run while #37 was genuinely running under a live heartbeat, printed both
   `terminal record: crashed` / `halted on 40 with class runner_crashed` *and* `37 running`
   in the same output. The run had not ended; the printed terminal record described an event
   that happened during startup, not the run's outcome.
2. The `--follow` attached to that same resume treated the crashed record as this launch's own
   terminal, printed the run summary, and exited — telling the operator the run had ended while
   #37 was still in flight. A second `run` was then launched on that false belief and was
   correctly refused by the lease (the one part of this incident that worked as designed).

### Root Cause

`StateStore.acquire()` (`state.py:225`) calls `_mark_crashed(state, previous)` (`state.py:282`)
whenever it reclaims a stale lease, *before* the reclaiming process's own `run()` loop has run a
single task or decided anything about continuation. `_mark_crashed` correctly turns every
in-flight record into `halted`/`runner_crashed` (R55) — that part is not in question. But it also
writes a **run-level** record to `state["terminal"]`:

```python
terminal = state.get("terminal")
lease_started = _epoch(previous.get("acquired_at")) or 0
written = _epoch((terminal or {}).get("written_at")) or -1
if terminal is None or written < lease_started:
    state["terminal"] = {
        "run_status": contracts.RUN_CRASHED,
        "halt_task": ids[0] if ids else None,
        "halt_class": contracts.HALT_RUNNER_CRASHED if ids else None,
        ...
    }
```
(`state.py:315-327`)

`state["terminal"]` has exactly one legitimate writer everywhere else in the codebase: `run()`'s
own `_write_terminal` calls, fired only at a real ending — `RUN_COMPLETED` when every task is
done, `RUN_HALTED` when a halt is not continued past, or `RUN_CRASHED` from the `finally` backstop
when the run itself dies before writing anything (`run.py:206-220`). Every one of those call
sites knows, at the moment it writes, that the run is actually over. `_mark_crashed` does not: it
runs inside `acquire()`, seconds before the *same process*'s `run()` goes on to try #40 again,
route past it, and run #37. Writing `state["terminal"]` here asserts an ending that has not
happened yet — often never happens the way the write claims (`RUN_CRASHED` naming #40, when the
run in fact continues and later writes its own true `RUN_COMPLETED` or a `RUN_HALTED` naming a
different task and class entirely).

Three readers consume `state["terminal"]` directly, and each is fooled the same way for the
window between this premature write and the run's real one:

- `cli.cmd_status` (`cli.py:234-240`) prints `store.terminal()` unconditionally whenever it is
  not `None` — it has no check for whether the terminal record predates the current live lease.
  (`status_word()`, used for the one-line `status: ...` header just above it, already has that
  check at `state.py:458-472` and correctly reports `running`; the raw terminal block below it
  does not share that logic, which is why the two lines contradicted each other in the incident.)
- `tail.follow`'s `terminal_of` (`tail.py:292-299`) treats any `state["terminal"]` that differs
  from the floor captured before launch as *this launch's own ending* and calls `finish()`,
  which prints the run summary and returns. The floor is captured by the parent CLI process
  (`cli.py:194`, `_detach`) before the child process starts; `_mark_crashed`'s write happens
  inside the child, milliseconds after that floor read, so it always looks like fresh news to a
  follower that just started watching.
- `summary.build` (`summary.py:150-166`) reads `halt_task`/`halt_class`/`cli_version*` straight
  from `raw.get("terminal")`, even though it separately computes `run_status` from the
  staleness-aware `store.status_word()`. In today's code this can already print a `run_status` of
  `running` alongside a stale `halt_task`/`halt_class` pair from an unrelated earlier record —
  the same inconsistency `cmd_status` shows, one field at a time.

None of these three readers is wrong to trust `state["terminal"]`; the field's whole purpose is
"the run's own recorded ending." The defect is that `_mark_crashed` writes to it before the run
has one.

## Product Contract

### Requirements

- **R1.** Reclaiming a stale lease (`StateStore.acquire()` returning `STALE_RECLAIMED`) marks
  every in-flight record `halted`/`runner_crashed` with its existing evidence and finding
  behavior (R55, unchanged), and does **not** write to `state["terminal"]`. The only writers of
  `state["terminal"]` after this change are `run()`'s own three call sites: real completion, a
  halt not continued past, and the crash backstop in its `finally` block.
- **R2.** `relay status`, invoked while a reclaiming run is genuinely still in progress (a live
  lease, task records changing), never prints a `terminal record:` line describing an ending that
  has not happened. Once the run does end for real, `relay status` reflects that ending exactly as
  it does today.
- **R3.** `relay run --follow` and `relay tail`, attached across a stale-lease reclaim that the
  run then continues past, keep following through to the run's actual ending — they must not stop
  early on the reclaim's own record-marking activity. Once the run does end for real (completes,
  or halts without continuing), the follower reports that real ending exactly as it does today.
- **R4.** `relay summary` continues to report a `run_status` consistent with `status_word()`
  (unchanged, already correct) and now also reports `halt_task`/`halt_class`/`cli_version*` that
  are consistent with that same `run_status` — both come from the one true terminal record once
  R1 removes the premature write, so the two no longer need separate staleness handling to agree.

### Scope Boundaries

**In scope:** the `_mark_crashed` write removal in `state.py`; the one existing test in
`test_state.py` that encodes the old (buggy) write and must be rewritten to assert the new
contract; new coverage proving R1-R3 at the unit and CLI/integration layers.

**Out of scope (see KTD2 and KTD3 for why):**
- Any change to *which* records `_mark_crashed` marks `halted`/`runner_crashed`, or to the
  self-kill finding scan it already runs (`classify.scan_self_kill`, landed by task #47). R55's
  record-level marking is correct today and untouched by this fix.
- Any change to how `_one_task` re-processes a record left `halted` by a reclaim, or to
  `_continue_past`'s classification logic (issue #15). The incident's #40 -> `unclean_exit` ->
  "continuing past it" sequence is that existing, separately-shipped logic working as designed;
  this plan does not touch it (KTD2).
- Adding staleness-aware guards to `cmd_status`'s terminal block, `tail.terminal_of`, or
  `summary.build`'s direct terminal reads. Once `state["terminal"]` only ever holds the current
  run's own true ending (R1), those three readers need no new staleness logic of their own to
  satisfy R2-R4 (KTD3) — the fix in the write path removes the entire class of "is this the
  current run's record" question their read paths would otherwise each have to answer
  separately.

### Deferred to Follow-Up Work

None. This is a small, single-root-cause fix confined to `state.py`'s write path, with the
consuming call sites unmodified.

## Planning Contract

### Key Technical Decisions

**KTD1. Delete the write, do not gate it.**
Considered gating the write on some predicate ("write it only when the run is not going to
continue"), matching the task text's literal wording. Rejected: at the point `_mark_crashed` runs
(inside `acquire()`), the process has not yet loaded the manifest's `on_halt.continue_past_task_halt`
setting into scope, has not yet re-tried the marked task through `_one_task`, and has not yet
learned whether that retry's own halt (if any) will itself continue past. Every one of those
facts is decided later, in `run()`'s per-task loop. Computing them early enough to gate this write
would mean duplicating `_continue_past`'s decision inside `_mark_crashed`, before there is even a
new halt to evaluate it against — there is nothing to gate on yet. The unconditional backstop in
`run()`'s `finally` (`run.py:214-219`, `if not wrote_terminal: _write_terminal(..., RUN_CRASHED,
...)`) already covers every path where the reclaiming process itself fails to reach a real
terminal write through Python's normal exception and signal handling — a caught exception, or a
caught signal, unwinding through the `try`/`finally`. Deleting the premature write and trusting
that backstop is simpler and, for every one of those paths, exactly as safe.

Accepted residual gap: an uncatchable kill (`SIGKILL`, an OOM-killer kill, host or power loss)
between `acquire()` returning and the reclaiming process's first real terminal write never
reaches the `finally` block at all, catchable or not — Python cannot run any code on `SIGKILL`.
In that narrow compound-failure window (a reclaim immediately followed by a second hard kill of
the *new* holder), `state["terminal"]` is left exactly as it was before this run started, which
`cmd_status`'s raw terminal block (`cli.py:234-238`) prints unconditionally with no
lease-staleness check of its own — unlike `status_word()`, which does compare the terminal
against the lease and would still correctly report `crashed`. This is not a regression this fix
introduces: the same gap already exists today, unrelated to reclaim, whenever a process crashes
this hard immediately after an ordinary (non-stale) `acquire()` on a manifest with an older real
terminal record on file. Closing it fully would mean adding the lease-staleness comparison
`status_word()` already has to `cmd_status`'s raw block directly, which KTD3 deliberately keeps
out of this fix's scope. Accepted here as a pre-existing, narrow, and now-slightly-more-visible
limitation rather than a new one.

**KTD2. Leave `_continue_past` and #40's reclassification path untouched.**
The incident's #40 -> `unclean_exit` -> "continuing past it" sequence is issue #15's own,
separately shipped logic (`docs/solutions/logic-errors/continue-past-halt-checked-general-state-blind-to-the-branch-its-own-skip-left.md`
already documents a related, since-fixed gap in that same function). Nothing in this incident's
two reported symptoms — the false terminal record, and the follower stopping early — depends on
what class #40 was ultimately reclassified to or why. Re-deriving exactly which preflight check
fired for #40 on retry is unnecessary for this fix and out of scope; touching that logic here
would mix two independent concerns in one diff.

**KTD3. Fix the one write site, not the three read sites.**
`cmd_status`, `tail.terminal_of`, and `summary.build` each read `state["terminal"]` with a
different amount of staleness awareness today (`status_word()` has it, the raw `cmd_status` block
does not; `tail`'s floor comparison partially has it, but only against the floor's single
snapshot, not against the current lease; `summary.build` has it for `run_status` but not for the
other three fields it reads from the same record). Patching each of the three independently would
require each to learn "is this terminal record newer than the current lease" — the exact
comparison `status_word()` already encapsulates — duplicating that logic three times, once per
reader, with three chances to get the edge cases wrong. R1 removes the premature write instead:
once `state["terminal"]` can only ever be the current run's own true ending, all three readers'
existing logic is correct by construction for every ending `run()` itself can reach, with no new
comparison needed anywhere for those endings. (KTD1's accepted residual gap — an uncatchable kill
before `run()` writes anything — is the one case this narrowing does not fully close; it is a
pre-existing gap in `cmd_status`'s raw block, not one this fix introduces, and stays out of scope
per the same reasoning.) This is the narrower, single-cause fix; `status_word()`'s existing
crash-detection fallback (`state.py:458-472`,
live lease -> `running`; dead lease with no/stale terminal -> `RUN_CRASHED`) is unaffected and
still the sole source of truth for "was a run ever properly finished," independent of whether
`_mark_crashed` ever wrote anything.

### Assumptions

- No other code path calls `StateStore.acquire()` besides `run()` (`run.py:136`). Confirmed by
  grep across `skills/relay/scripts/relay/*.py`: `acquire()` has exactly one caller. `relay lease`
  (`cli.py:341`) only reads or breaks the lease; it never calls `acquire()`. This means the
  reclaiming process's own `run()` loop is always what follows a `STALE_RECLAIMED` result, so the
  backstop in KTD1 is always reachable.
- `state["terminal"].previous_holder` (the crashed-holder detail `_mark_crashed` was also writing
  into the run-level record) is not read anywhere outside `state.py` itself — confirmed by grep.
  Removing the write loses no information any current reader consumes; the same previous-holder
  detail remains available per-task, unchanged, in `record["halt_evidence"]["previous_holder"]`
  (`state.py:295`), which this fix does not touch.

### Open Questions

None. The fix is narrow and the removed write's only consumers are covered by KTD3's reasoning.

### High-Level Technical Design

```mermaid
sequenceDiagram
    participant Op as Operator (status / --follow)
    participant R2 as Resuming `relay run`
    participant SS as StateStore.acquire()
    participant MC as _mark_crashed
    participant Loop as run()'s task loop

    Note over R2,MC: Reclaim (unchanged record-level marking, R55)
    R2->>SS: acquire()
    SS->>MC: _mark_crashed(state, previous_holder)
    MC->>MC: mark every in-flight record halted/runner_crashed (unchanged)
    MC--xMC: no longer writes state["terminal"] (R1, this fix)
    SS-->>R2: STALE_RECLAIMED

    Note over R2,Loop: The run's own outcome is still undecided here
    R2->>Loop: continue the per-task loop (#40 retried, reclassified, continued past; #37 runs)
    Op->>SS: status / follow polls state.json meanwhile
    SS-->>Op: terminal is still whatever it was before this run (None or an older real ending) -- never this reclaim's phantom crash

    Loop->>SS: _write_terminal(...) exactly once, at the run's real ending
    SS-->>Op: terminal now reflects the true ending; status and follow both report it
```

---

## Implementation Units

### U1. Stop `_mark_crashed` from writing a run-level terminal record

**Goal:** a stale-lease reclaim marks in-flight records `halted`/`runner_crashed` and nothing
else touches `state["terminal"]`, per R1.

**Requirements:** R1. Governs KTD1, KTD3.

**Dependencies:** none.

**Files:**
- `skills/relay/scripts/relay/state.py`
- `tests/test_state.py`

**Approach:**
1. In `_mark_crashed` (`state.py:282-328`), delete the trailing block that computes `terminal`,
   `lease_started`, `written`, and conditionally sets `state["terminal"] = {...}`. Keep the
   `for task_id, record in state.get("tasks", {}).items(): ...` loop and the `ids.append(task_id)`
   / `return tuple(ids)` exactly as they are — the record-level marking, evidence, and
   `classify.scan_self_kill` finding attachment are correct and untouched.
2. Update the function's docstring (`state.py:282-288`). Its first sentence currently reads "a
   reclaimed lease turns every running or merging record into halted with class runner_crashed,
   and records the old holder in the terminal record as crashed" — the trailing clause describes
   exactly the run-level write step 1 deletes and must be rewritten to say the old holder is
   recorded per-record, in `halt_evidence.previous_holder`, not in a run-level terminal record.
   Its second paragraph already only describes the self-kill finding, not the run-level write, and
   needs no change; confirm rather than assume.
3. Rewrite `test_stale_lease_is_reclaimed_and_old_holder_recorded_as_crashed`
   (`tests/test_state.py:84-94`) to assert the new contract: after a reclaim, `store.terminal()`
   is unchanged from whatever it was immediately before `acquire()` was called (`None` in this
   test's case, since no run ever wrote one), while `result.previous_holder["holder_pid"]` and
   `store.lease()["holder_pid"]` still report the reclaim's own facts exactly as before. Rename it
   to reflect what it now proves (e.g.
   `test_stale_lease_is_reclaimed_without_writing_a_run_level_terminal_record`).

**Test scenarios:**
- Happy path (rewritten existing test): a stale lease from pid 100 reclaimed by pid 200 returns
  `STALE_RECLAIMED` with the correct `previous_holder`, and `store.terminal()` stays `None`
  (no run had ever completed for this manifest before the reclaim).
- Edge case (new): seed a real prior terminal record (via `store.write_terminal(RUN_COMPLETED)`
  from an earlier, cleanly-finished run) before the stale lease is taken and reclaimed. Assert
  the reclaim leaves that prior record byte-for-byte unchanged — the reclaim must not overwrite
  an older true ending with a phantom crash either.
- Regression guard (existing, must keep passing unchanged):
  `test_stale_lease_with_merging_record_marks_runner_crashed` — the record-level marking
  (`status`, `halt_class`, `halt_evidence`) is untouched by this unit.
- Regression guard (existing, must keep passing unchanged): the four self-kill-finding tests at
  `tests/test_state.py:112-183` — all record-level behavior, none of it touches
  `state["terminal"]`.

**Verification:** `python3 -m unittest test_state` from `tests/` passes with the rewritten and
new assertions included.

---

### U2. Prove the run's real ending survives a reclaim, end to end

**Goal:** a run that reclaims a stale lease and then continues past the reclaimed halt still
produces exactly one true `state["terminal"]` record, matching the run's actual conclusion, per
R1 and (as the durable, non-flaky form of) R2-R3.

**Requirements:** R1, R2, R3. Governs KTD1.

**Dependencies:** U1.

**Files:**
- `tests/test_run.py`

**Approach:**
1. Add a test to the existing `ContinuePastHalt` class (`tests/test_run.py:844`), reusing its
   `opt_in(gate=...)` helper with `gate=["bash", "-c", GATE_REFUSES_SH % "src/t_1.py"]` (the same
   gate-failure fixture `test_a_refused_gate_pauses_the_task_and_the_later_tasks_land` uses,
   pointed at `T-1` instead of `T-2`), so the reclaimed task's own retry has a concrete, buildable
   reason to halt — a gate refusal, `HALT_GATE_REFUSED`, which is task-scoped and not the
   `no_task_branch` preflight check `_continue_past` unconditionally refuses to step over. Before
   calling `self.go()`, seed a stale, still-live-looking lease from a different pid directly via
   `state.StateStore(self.manifest_path, self.repo, home=self.home, pid=999999,
   ttl_seconds=1).acquire()`, then `upsert` `T-1` to `STATUS_RUNNING` under that store, then
   `time.sleep(1.1)` (real wall-clock past the 1-second TTL this store's lease recorded on itself
   — `state.py:207` reads `ttl_seconds` off the lease record, not off whatever store later
   reclaims it, so a short-TTL seed store does not require the reclaiming `go()` call to share a
   fake clock).
2. Queue the stub fixtures so `T-1`'s retry produces a commit on `relay/T-1` that the gate then
   refuses (mirroring `TASK_BRANCH_SH` plus the gate command, exactly as
   `test_a_refused_gate_pauses_the_task_and_the_later_tasks_land` queues `T-2`), and `T-2`, `T-3`
   both succeed and land, mirroring that same test's shape.
3. Call `self.go()` (which constructs its own `StateStore` at the real, current pid and reclaims
   the seeded stale lease inside `acquire()`, per R1's fixed path) and assert: `outcome.exit_code
   == runner.EXIT_OK`; `self.store().get("T-1")["halt_class"] == contracts.HALT_GATE_REFUSED` and
   `continued_past` is true; `self.store().terminal()["run_status"] == contracts.RUN_COMPLETED`;
   the terminal record's `halt_task` is `None` (proving the reclaim's own phantom
   `runner_crashed` record on `T-1`, and its later real `gate_refused` halt, were never what
   landed in `state["terminal"]`).

**Test scenarios:**
- Happy path: as described above — reclaim happens, `T-1`'s retry halts for real on
  `HALT_GATE_REFUSED`, continue-past steps over it, `T-2`/`T-3` complete normally, and the run's
  one true terminal record is `RUN_COMPLETED` with no `halt_task`.
- Edge case: same seed and failing gate, but the manifest is *not* opted into
  `continue_past_task_halt` — write the manifest directly (replace `command = ["true"]` with the
  same `GATE_REFUSES_SH % "src/t_1.py"` gate in the base `MANIFEST` text and load it, without
  `opt_in()`'s `[on_halt]` append, since `opt_in()` always sets `continue_past_task_halt = true`
  and has no mode that sets only the gate). Assert the run halts for real this time
  (`outcome.exit_code == runner.EXIT_HALTED`), `outcome.halt_task == "T-1"`, and
  `self.store().terminal()["halt_task"] == "T-1"` with `halt_class == contracts.HALT_GATE_REFUSED`
  — proving the fix does not change *whether* the run continues, only that the transient reclaim
  marking never leaks into the run-level record either way.

**Verification:** `python3 -m unittest test_run` from `tests/` passes with both new scenarios
included.

---

### U3. Pin the operator-visible fix: `status` and `--follow` across a reclaim

**Goal:** `relay status` never prints a terminal record for an ending that has not happened,
`relay run --follow` keeps following through a reclaim to the run's real ending, and `relay
summary`'s `halt_task`/`halt_class`/`cli_version*` fields agree with its `run_status` field after
a reclaim — per R2, R3, and R4, observed the way an operator actually sees them (through the CLI,
not only through `state.py` internals).

**Requirements:** R2, R3, R4. Governs KTD3.

**Dependencies:** U1.

**Files:**
- `tests/test_cli.py`

**Approach:**
1. Add a test to `StatusVerb` (`tests/test_cli.py:313`) that seeds a stale lease exactly as U2's
   `state.StateStore(..., pid=999999, ttl_seconds=1).acquire()` does, sleeps past the TTL, then
   calls a second store's `acquire()` directly (no full `run()`) to reclaim it — mirroring
   `test_state.py`'s own `Acquire` tests, but through this file's `CliCase.store()`-adjacent
   pattern. Call `self.call("status", self.manifest_path)` immediately after and assert the
   output contains no `"terminal record:"` line, proving `cmd_status` no longer has a phantom
   ending to print right after a reclaim with no run conclusion yet.
2. Add a test to `FollowedRun` (`tests/test_cli.py:198`), reusing its `queue_complete()` helper
   for the ordinary three-task success/blocked/success sequence. Before calling
   `self.call("run", self.manifest_path, "--follow")`, seed the same stale-lease-plus-running-task
   scenario as U2 (a real subprocess will be the one to reclaim it this time, inside the detached
   child `--follow` launches). Assert the followed run's printed output still reaches `"relay run
   completed"` and `self.store().terminal()["run_status"] == contracts.RUN_COMPLETED` — proving
   the follower did not stop early on the reclaim's record-marking activity. Before this fix, the
   follower would have exited immediately after the reclaim with a truncated summary and never
   printed the completion line; assert that the full three-task output (`T-1`, `T-2`, `T-3`
   phase headers) is present, not only the final line, so a regression that reintroduces an early
   exit cannot pass by coincidence.
3. In the same `FollowedRun` test from step 2, after the followed run completes, call
   `summary.build(self.manifest, self.store())` directly and assert `data["run_status"] ==
   contracts.RUN_COMPLETED` and both `data["halt_task"]` and `data["halt_class"]` are `None` —
   proving R4 directly rather than relying on KTD3's "correct by construction" reasoning alone.
   This reuses the run this unit already produces; it does not require a fourth scenario or a
   dirtied gate.

**Test scenarios:**
- `StatusVerb`: status immediately after a reclaim with no run() involved prints no terminal
  record line (R2, direct).
- `FollowedRun`: a followed run that reclaims a stale lease partway through startup still follows
  all the way to `RUN_COMPLETED`, with every task's phase output present in what was printed (R3,
  direct; also re-confirms R1's write removal end to end through a real subprocess, complementing
  U2's in-process assertion).
- `FollowedRun` (same test, extended): `summary.build`'s `run_status`, `halt_task`, and
  `halt_class` agree with each other after the reclaim-then-complete run (R4, direct).

**Verification:** `python3 -m unittest test_cli` from `tests/` passes with all three scenarios
included.

---

## Verification Contract

- Run the full suite from the repo root: `python3 -m unittest discover -s tests`. It normally
  takes about two and a half minutes.
- Single-module runs during development: `python3 -m unittest test_state`,
  `python3 -m unittest test_run`, `python3 -m unittest test_cli` from `tests/`.
- No live task run is required for this change per
  `docs/solutions/logic-errors/stubbed-seams-agree-by-construction-first-live-run-found-five-contract-defects.md`'s
  warning: it touches no envelope grammar, closeout terminal line, brief template, or halt record
  shape exchanged with a real subprocess. The fix removes an internal write inside `state.py`,
  and U3's follow test already exercises the real subprocess detach/follow path the stub suite
  provides — the fix is fully exercised by the existing hermetic infrastructure.

## Definition of Done

- `StateStore._mark_crashed` marks in-flight records `halted`/`runner_crashed` exactly as before
  and no longer writes to `state["terminal"]`.
- `state["terminal"]` is written only by `run()`'s three existing call sites (real completion, a
  halt not continued past, and the crash backstop), unchanged by this fix.
- A run that reclaims a stale lease and continues past the reclaimed halt writes exactly one true
  terminal record, matching its actual conclusion (U2).
- `relay status`, called right after a reclaim with no run() conclusion yet, prints no terminal
  record line; `relay run --follow`, attached across a reclaim, follows through to the run's real
  ending without stopping early; `relay summary`'s `halt_task`/`halt_class`/`cli_version*` agree
  with its `run_status` after a reclaim-then-complete run (U3).
- `python3 -m unittest discover -s tests` passes.
- No dead-end or experimental code from an approach that did not pan out is left in the diff.
