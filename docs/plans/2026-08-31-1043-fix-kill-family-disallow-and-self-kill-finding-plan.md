---
title: Kill-Family Disallow and Self-Kill Finding - Plan
type: fix
date: 2026-08-31
artifact_contract: ce-unified-plan/v1
artifact_readiness: implementation-ready
product_contract_source: ce-plan-bootstrap
execution: code
---

# Kill-Family Disallow and Self-Kill Finding - Plan

## Goal Capsule

**Objective:** a task process can no longer end a Relay run by killing the Runner's own
process, and when a crash happens anyway, the operator sees why instead of a bare
`runner_crashed` record.

**Means:** add `Bash(kill*)`, `Bash(pkill*)`, `Bash(killall*)` to the default disallow list
(KTD1), and scan a crashed task's own stdout log for a kill-family command naming the previous
Runner's PID, attaching the match as a finding on the reclaimed record (KTD2).

**Authority hierarchy:** this plan's Key Technical Decisions; then `CLAUDE.md`'s rule that
halt classes are closed and a new outcome is a finding, not a class; then existing code
conventions in `contracts.py`, `classify.py`, and `state.py`.

**Stop conditions:** stop and report a blocker rather than guessing if `_mark_crashed` cannot
reach a task's stdout log path, or if a test shows the new disallow entries block a Bash call
that legitimate task work depends on beyond the kill family itself.

**Execution profile:** single PR, no phased rollout. `ce-work` builds, tests, and lands all
units together.

**Tail ownership:** the calling Relay task process runs the project gate and lands the branch;
this plan does not describe shipping mechanics.

---

## Problem Frame

Round six stage A (2026-08-30 09:00), Task #40 was chasing a hung unittest child process. It
listed python PIDs with `ps aux | grep` and ran `kill -9 57246 61799 61800`. PID 61799 was the
Runner itself and 61800 was its `caffeinate` wrapper. Both died. The run's state file was left
holding a stale lease with task #40 recorded as `running`; task #40's own branch, `relay/40`,
held one clean, unmerged commit; nothing recorded why the run stopped.

Two gaps let this happen silently:

1. `contracts.DISALLOWED_TOOLS` denies force pushes, hard resets, `git clean`, and `rm -rf`, but
   has no entry for `kill`, `pkill`, or `killall`. On an enforcing backend (Claude, Grok) this
   list becomes a `--deny`/`--disallowedTools` flag the CLI's own permission engine checks
   before a Bash call runs, so the missing entries meant nothing stopped the call at the
   permission layer.
2. When a later `relay run` reclaims a stale lease, `state.StateStore._mark_crashed` halts every
   in-flight record with class `runner_crashed` and a small evidence block (status before, the
   previous holder, the last git op). It never looks at what the crashed task actually did, so a
   task that took the Runner down with it and a task that was merely mid-work when the host lost
   power produce an identical, uninformative record.

## Product Contract

### Requirements

- **R1.** `contracts.DISALLOWED_TOOLS` includes glob entries that deny `kill`, `pkill`, and
  `killall` Bash invocations, in the same style as the existing force-push, reset, and
  delete entries.
- **R2.** When a stale lease is reclaimed and an in-flight record is halted with class
  `runner_crashed`, the record's own stdout log (`logs/<task_id>.stdout.log`) is scanned for a
  kill-family command whose argument list names the previous lease holder's PID. When one is
  found, the halted record carries a finding describing the command and the full PID list from
  that command, instead of leaving the record's `findings` empty.
- **R3.** The scan in R2 does not require the crashed record's `transcript_path` or `session_id`
  fields, because a task that killed the Runner mid-run crashes before those fields are ever
  written back to state (see KTD2's Approach for why).
- **R4.** The new finding class participates in the existing summary/closeout rendering path
  (`classify.finding_line`, `summary.cause_line`, `contracts.HALT_LINES`) the same way
  `unenforced_disallowed` already does, so it prints a real sentence instead of `?` placeholders.

### Scope Boundaries

**In scope:** the disallow-list addition; the post-hoc self-kill scan wired into
`_mark_crashed`; the new finding class and its summary line; tests for both.

**Out of scope (see KTD3 and KTD4 for why):**
- Running the Runner in a process group the task cannot address. Rejected as ineffective for
  this failure mode (KTD3).
- Detecting a self-kill of the `caffeinate` PID specifically. `caffeinate`'s PID is never
  recorded in state (KTD2's Approach); only the Runner's own `holder_pid` is checked.
- Backend-specific transcript parsing (Codex's `--output-last-message` file, Grok's session
  file). The scan reads the raw stdout log generically (KTD2).
- Adding `kill*`/`pkill*`/`killall*` to `DESTRUCTIVE_TOOLS` (KTD4).

### Deferred to Follow-Up Work

None. This is a small, self-contained defensive fix touching `contracts.py`, `classify.py`,
and `state.py`, with no adjacent cleanup identified during research.

## Planning Contract

### Key Technical Decisions

**KTD1. Add the kill family to `DISALLOWED_TOOLS`, not a new list.**
`contracts.DISALLOWED_TOOLS` is already the single list both enforcement paths read: it becomes
the `--deny`/`--disallowedTools` flag for backends where `enforces_at_launch` is true (Claude,
Grok), and it drives `classify.classify`'s post-hoc audit for the one backend where it is false
(Codex), producing an `unenforced_disallowed` finding automatically. Adding the three new glob
entries to the existing list gets both defenses for free with no new plumbing.
`Bash(git clean*)` already shows a non-catastrophic-but-still-worth-blocking pattern living in
the parent list without also entering `DESTRUCTIVE_TOOLS`; the kill family follows that
precedent (KTD4).

Accepted tradeoff: this also removes a task's ability to kill a process it legitimately spawned
and needs to end, including the hung child #40 was originally chasing before it also killed the
Runner. No narrower pattern (e.g. scoped to descendant PIDs only) is available at the disallow
list's glob-matching granularity, and the task text's own directive is to block the whole
family. A task that hits a stuck-child scenario after this ships can no longer resolve it
itself; it stalls until the task timeout fires. That is judged an acceptable cost against a
Runner-ending crash, which is worse.

**KTD2. Scan the crashed task's raw stdout log for a self-targeting kill, not its transcript.**
`run.py` only writes `session_id` and `transcript_path` onto a task's record *after*
`launch.launch()` returns (`run.py` around the post-launch `store.upsert`). A task that kills the
Runner mid-run never reaches that line. The process implementing the upsert is dead. So a
record reclaimed as `runner_crashed` can have `transcript_path: null`. The one thing that *does*
exist on disk regardless is the raw stdout log at the deterministic path
`<state_dir>/logs/<task_id>.stdout.log` (`store.path("logs", task_id + ".stdout.log")`,
`launch.py`'s `log_path` argument), which is opened and flushed line-by-line as the task's own
subprocess writes to it. This is the exact `logs/40.stdout.log` the task's own incident report
names as evidence.

Approach: add `classify.scan_self_kill(log_path, victim_pid)`. It parses the log with the
existing `backends.read_jsonl` (already backend-agnostic: one JSON object per line, malformed
lines skipped), then recursively walks every string leaf in each parsed object, not a
backend-specific `message.content[].input.command` path, so the same function works regardless
of which backend produced the line. For each leaf, it splits the leaf into single-command
segments (the same split `classify._command_candidates` uses for `&&`/`||`/`;`/newline-joined
shell lines) and matches each segment against the three kill-family command names from
`contracts.KILL_LIKE_TOOLS`, anchored to the start of the segment with a required trailing space
or end of string. A code-review pass on this feature found that the disallow-list's own glob form
(`kill*`) is right for its job but wrong for scanning arbitrary log text, since `fnmatch` has no
word-boundary concept and "kill*" also matches "killing"; the anchored match fixes that, and
matching per segment rather than the whole leaf also stops a PID named later in a compound
command from being misread as one `kill` itself named. On a match, it extracts PID-shaped tokens
(two or more digits, to skip signal flags like `-9`) from that segment and checks whether the
previous lease holder's PID is among them. `state.py` calls this from `_mark_crashed`, which
already has `self.path(...)` and the previous holder's `holder_pid`, wrapped in a broad
`except Exception` so a pathological log (deeply nested JSON, for one) can never raise out of the
flock-held critical section `_mark_crashed` runs inside.

This is a best-effort forensic scan, not a gate: a false match (an assistant's prose text that
happens to echo the literal string `kill -9 <pid>`) only adds a finding to an already-halted
record, never blocks or changes a decision. `caffeinate`'s own PID is not checked because state
never records it (only the Runner's own `os.getpid()` becomes `holder_pid`).

This scan is not a backup behind KTD1 for every backend. `classify.classify()`, the only code
path that can turn a matched `DISALLOWED_TOOLS` entry into an `unenforced_disallowed` finding on
a backend where `enforces_at_launch` is false (Codex, today's one such backend), runs in
`run.py` only after `launch.launch()` returns, in the same runner process a self-kill just
ended. If the kill succeeds, that process never reaches `classify.classify()` for the task that
killed it. On Codex, KTD1 provides no protection against a self-kill, preventive or same-run
forensic, so `scan_self_kill` at the next reclaim is the *only* place a Codex self-kill is ever
recorded, not a second layer behind one that already worked. For Claude and Grok
(`enforces_at_launch` true), KTD1's `--deny`/`--disallowedTools` flag is the real defense. It
stops the call before it runs. `scan_self_kill` there is genuine defense in depth, catching
only a call phrased to slip past the glob.

**KTD3. Do not run the Runner in a process group the task cannot address.**
Considered and rejected. On the same host and the same Unix user, `kill -9 <pid>` targets an
arbitrary PID directly; it does not go through the process group at all, so wrapping the Runner
in a group the task's shell cannot signal would not have stopped incident #40. Task #40 read
the Runner's PID straight out of `ps aux` and killed it by number. Process-group isolation only
defends against a *group-wide* signal (`kill -- -<pgid>`), a different attack shape than the one
observed. The only mechanism that would truly stop a same-user `kill -9 <pid>` is running the
Runner under a different OS user or with elevated protection, which is out of proportion to this
incident and not pursued here.

**KTD4. Kill-family entries stay out of `DESTRUCTIVE_TOOLS`.**
`DESTRUCTIVE_TOOLS` is the named subset of `DISALLOWED_TOOLS` whose match on an unenforced
backend refuses the *landing* outright rather than only recording a finding (force push, hard
reset, `rm -rf`, and spelling variants, irreversible damage to repo content or history). A kill
command does not touch repo content, and a self-kill of the Runner already ends the run before
any landing decision is reachable, so there is nothing left for `DESTRUCTIVE_TOOLS`'s "refuse
the landing" behavior to act on. `git clean` and `git checkout -- .*` already establish the
precedent of a disruptive-but-not-history-destroying command staying in the parent tuple only;
the kill family follows it.

### Assumptions

- The incident's task ran on a backend where `enforces_at_launch` is true (most likely Claude,
  Relay's default). KTD1's disallow-list fix is the primary, preventive defense for that path.
  On Codex (`enforces_at_launch` false), KTD1 gives no protection at all. KTD2's scan is the
  sole detector of a Codex self-kill, not a backup. KTD2 is genuine defense in depth only on the
  two enforcing backends, where it catches a call phrased to slip past the deny glob.
- `previous["holder_pid"]` is always present on a reclaimed lease's previous-holder dict, because
  `StateStore._holder()` always sets it. No defensive `None`-check beyond skipping the scan when
  it is absent is needed.

### Open Questions

- **Deferred, non-blocking.** `scan_self_kill` is a best-effort log scan, and on Codex it is the
  sole detector of a self-kill (KTD2), not a backup behind a preventive layer. Whether that
  single detector needs a stronger guarantee than log scanning, e.g. a synchronous flush
  contract on the disallowed-call path, is a real question this plan does not resolve. It is
  deferred because "at least detect" is the task's own stated bar and best-effort scanning meets
  it; a stronger guarantee is follow-up work, not a blocker to this fix.

### High-Level Technical Design

```mermaid
sequenceDiagram
    participant T as Task process (killed itself's Runner)
    participant OS as OS process table
    participant R2 as Later `relay run`
    participant SS as StateStore.acquire()
    participant MC as _mark_crashed
    participant CL as classify.scan_self_kill

    Note over T,OS: Incident (round six #40)
    T->>OS: kill -9 <hung child> <Runner pid> <caffeinate pid>
    OS--)T: Runner and caffeinate die; T's own log line already flushed to disk

    Note over R2,CL: Next runner start
    R2->>SS: acquire()
    SS->>SS: lease stale -> reclaim
    SS->>MC: _mark_crashed(state, previous_holder)
    loop each in-flight record
        MC->>MC: set status=halted, halt_class=runner_crashed
        MC->>CL: scan_self_kill(logs/<task_id>.stdout.log, previous_holder_pid)
        CL->>CL: _read_jsonl + walk string leaves + matches_disallow_pattern(KILL_LIKE_TOOLS)
        CL--)MC: finding {class: runner_self_kill, command, pids, victim_pid} or None
        MC->>MC: append finding to record["findings"] when present
    end
```

---

## Implementation Units

### U1. Add the kill family to the default disallow list

**Goal:** deny `kill`, `pkill`, and `killall` Bash invocations by default, on both the
launch-time enforcement path and the post-hoc audit path, per R1.

**Requirements:** R1. Governs KTD1.

**Dependencies:** none.

**Files:**
- `skills/relay/scripts/relay/contracts.py`
- `tests/test_contracts.py`

**Approach:**
1. Add a `KILL_LIKE_TOOLS` tuple above `DISALLOWED_TOOLS`: `("Bash(kill*)", "Bash(pkill*)",
   "Bash(killall*)")`, with a comment naming the round six #40 incident as the source.
2. Extend `DISALLOWED_TOOLS` to include `KILL_LIKE_TOOLS` (`DISALLOWED_TOOLS = (... existing
   entries ...) + KILL_LIKE_TOOLS`) so both stay one source of truth. U2's scanner reuses
   `KILL_LIKE_TOOLS` directly rather than re-deriving the same three globs.
3. Do not add the new entries to `DESTRUCTIVE_TOOLS` (KTD4).

**Test scenarios:**
- Happy path: `contracts.DISALLOWED_TOOLS` contains `Bash(kill*)`, `Bash(pkill*)`, and
  `Bash(killall*)` (extend the existing fragment-based assertion style in
  `test_disallow_list_covers_the_four_r10_operations`, or add a sibling test).
- Edge case: `contracts.KILL_LIKE_TOOLS` is a subset of `contracts.DISALLOWED_TOOLS` (mirrors the
  existing `DESTRUCTIVE_TOOLS` subset test).
- Edge case: none of `Bash(kill*)`, `Bash(pkill*)`, `Bash(killall*)` appear in
  `contracts.DESTRUCTIVE_TOOLS` (mirrors the existing `git clean` exclusion assertion).

**Verification:** `python3 -m unittest test_contracts` from `tests/` passes with the new
assertions included.

---

### U2. Scan a crashed task's stdout log for a self-targeting kill

**Goal:** a function that reads a task's raw stdout log and reports whether a kill-family
command in it named a given victim PID, per R2 and R3.

**Requirements:** R2, R3. Governs KTD2.

**Dependencies:** U1 (reuses `contracts.KILL_LIKE_TOOLS`).

**Files:**
- `skills/relay/scripts/relay/classify.py`
- `tests/test_classify.py`

**Approach:**
1. Add a module-level `_PID_TOKEN_RE = re.compile(r"\b\d{2,}\b")` (two or more digits, so a bare
   `-9` signal flag is not read as a PID).
2. Add a small recursive helper that yields every string leaf from a parsed JSON value (dict,
   list, or string), for walking a decoded log line without assuming a backend-specific shape.
3. Factor the shell-separator split `_command_candidates` already does (`&&`/`||`/`;`/newline)
   into a small `_shell_parts(command)` helper, reused by both `_command_candidates` and
   `scan_self_kill`.
4. Add a module-level `_KILL_COMMAND_RE`, anchoring the three `contracts.KILL_LIKE_TOOLS` command
   names to the start of a string with a required trailing space or end of string. A plain glob
   match (`kill*`) is right for `DISALLOWED_TOOLS`'s own job but too loose for scanning arbitrary
   log text, since it also matches "killing" or "killed".
5. Add `scan_self_kill(log_path, victim_pid)`:
   - Read the log with `backends.read_jsonl(log_path)`; return `None` immediately if it did not
     open.
   - For each decoded line, for each string leaf containing the substring `"kill"` (a cheap
     prefilter), split the leaf into single-command segments with `_shell_parts` and test each
     segment against `_KILL_COMMAND_RE`.
   - On a match, extract PID tokens from that segment (not the whole leaf) with `_PID_TOKEN_RE`;
     if `str(victim_pid)` is among them, return a finding dict: `{"class":
     contracts.RUNNER_SELF_KILL, "command": segment, "pids": " ".join(tokens), "victim_pid":
     str(victim_pid)}`. `pids` is a joined string, not a list, because `summary.line_fields`
     drops list/dict values when filling a cause-line template. Matching per segment, not the
     whole leaf, keeps a PID named later in a compound command (`kill -9 100 && echo pid 61799`)
     from being misread as one the kill itself named.
   - No match anywhere: return `None`.

**Technical design:**
```text
scan_self_kill(log_path, victim_pid):
    lines, _malformed, opened = backends.read_jsonl(log_path)
    if not opened: return None
    for _n, obj in lines:
        for leaf in _string_leaves(obj):
            if "kill" not in leaf: continue
            for segment in _shell_parts(_unwrap_command(leaf)):
                if _KILL_COMMAND_RE.match(segment):
                    pids = _PID_TOKEN_RE.findall(segment)
                    if str(victim_pid) in pids:
                        return {finding dict}
    return None
```
Directional only. Exact loop shape and early-return points are the implementer's call.

**Test scenarios:**
- Happy path: a log file with one JSONL line shaped like a Claude tool_use block
  (`{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Bash","input":{"command":"kill
  -9 57246 61799 61800"}}]}}`) and `victim_pid=61799` returns a finding whose `pids` contains
  `"61799"` and whose `command` is the literal kill command.
- Edge case: the same log, but `victim_pid` is a PID not present in the command (e.g. `99999`).
  Returns `None`.
- Edge case: a log whose only Bash command is unrelated (e.g. `git status`). Returns `None`
  even when `victim_pid` happens to appear elsewhere in the line as an unrelated number.
- Edge case: prose text that merely starts with the word "killing" (not a command) does not
  match, because `_KILL_COMMAND_RE` requires a space or end of string right after the command
  name.
- Edge case: a compound command (`kill -9 100 && echo pid 61799 done`) returns `None` for a
  victim PID that only appears in the trailing `echo`, and returns a finding whose `pids` is
  `"100"` (not including 61799) for the PID the `kill` segment actually named.
- Edge case: a `killall` command with no literal PID in it never matches, since `killall` kills
  by process name. Documented as a structural limit of a PID-token scan, not asserted as a defect.
- Edge case: a signal-only numeric token (`-9`) never registers as a false PID match on its own
  (i.e., a victim PID of `9` is not the scenario under test; instead assert the token filter:
  `_PID_TOKEN_RE` does not extract single-digit tokens).
- Edge case: a differently-nested JSON shape (command string several levels deeper than the
  Claude shape used in the happy path) still matches, proving the string-leaf walk is
  backend-agnostic rather than keyed to one field path.
- Error path: a missing or unreadable log file returns `None` rather than raising.
- Error path: a log file that is present but contains only malformed JSON lines returns `None`.

**Verification:** `python3 -m unittest test_classify` passes with the new scenarios.

---

### U3. Wire the scan into stale-lease reclaim and give the finding a summary line

**Goal:** a record halted as `runner_crashed` by a reclaimed lease carries the self-kill finding
from U2 when one exists, and that finding renders a real sentence, per R2 and R4.

**Requirements:** R2, R4. Governs KTD2.

**Dependencies:** U1, U2.

**Files:**
- `skills/relay/scripts/relay/contracts.py`
- `skills/relay/scripts/relay/state.py`
- `tests/test_state.py`
- `tests/test_contracts.py`

**Approach:**
1. In `contracts.py`, add a new finding-only class constant near `UNENFORCED_DISALLOWED`:
   `RUNNER_SELF_KILL = "runner_self_kill"`, with a comment noting it is a finding class, not a
   record halt class (the record's `halt_class` stays `runner_crashed`, per KTD6/CLAUDE.md's
   closed-set rule).
2. Add `RUNNER_SELF_KILL` to the `FINDING_CLASSES` tuple and to the `LINE_CLASSES` tuple (append
   it to the same trailing group as `UNENFORCED_DISALLOWED`).
3. Add a `HALT_LINES[RUNNER_SELF_KILL]` template, e.g. `"self-kill: {command} named the
   runner's own pid {victim_pid} among {pids}"`.
4. In `state.py`, import `classify` (`from . import classify` alongside the existing `from .
   import contracts`; safe, `classify.py` does not import `state.py`).
5. In `_mark_crashed`, after setting `record["halt_class"] = contracts.HALT_RUNNER_CRASHED` for
   an in-flight record, look up `previous.get("holder_pid")`; when it is not `None`, call
   `classify.scan_self_kill(self.path("logs", task_id + ".stdout.log"), victim_pid)` inside a
   broad `try/except Exception`, treating any exception as no finding. `_mark_crashed` runs
   inside `_mutate`'s `fcntl.flock`-held critical section, and a crashed task's log is untrusted
   input the runner never validated (a code-review pass found a deeply nested JSON value in the
   log raises an uncaught `RecursionError` on this path otherwise), so the scan must fail closed
   rather than let an exception skip the state write and strand the same stale lease. When the
   scan returns a finding, append it to `record["findings"]` (the record's list, already `[]`
   from `new_record`/the running-status upsert).

**Test scenarios:**
- Happy path (integration, `test_state.py`): acquire a lease as `pid=100`, upsert a task to
  `STATUS_RUNNING`, write a fake JSONL log at `store.path("logs", "<task_id>.stdout.log")`
  containing a Bash command that kills pid `100`, advance the clock past the TTL, acquire again
  as a new pid, and assert the reclaimed record's `findings` contains a `runner_self_kill` entry
  whose `victim_pid == "100"`.
- Edge case (integration): same setup, but the log's kill command does not name pid `100`.
  Assert `findings` stays empty and `halt_class` is still `runner_crashed` (existing behavior
  from `test_stale_lease_with_merging_record_marks_runner_crashed` must keep passing unchanged).
- Edge case (integration): no log file exists at all for the reclaimed task (crash happened
  before any output was flushed). Assert reclaim still succeeds and `findings` stays empty,
  no exception.
- Edge case (integration): `classify.scan_self_kill` itself raises (force it, e.g. with a
  monkeypatched `side_effect`, rather than trusting the guard by inspection). Assert the reclaim
  still completes, `halt_class` is still `runner_crashed`, and `findings` stays empty.
- Happy path (`test_contracts.py`): `contracts.RUNNER_SELF_KILL` is in both `FINDING_CLASSES`
  and `LINE_CLASSES`, and `summary.cause_line(contracts.RUNNER_SELF_KILL, {"command": "kill -9
  100", "pids": "100", "victim_pid": "100"})` renders a sentence with no literal `{` braces left
  in it (covers R4; the existing `test_every_class_that_can_print_has_a_cause_line_and_nothing_else_does`
  and the finding-classes-sit-inside-line-set test already re-verify this generically once the
  constant is added to both tuples).

**Verification:** `python3 -m unittest test_state test_contracts` (and the full suite) passes;
manually trace one fixture run to confirm a `runner_self_kill` finding appears in
`digests/<task_id>.json`-shaped output where relevant (the scan only fires at reclaim time, in
`_mark_crashed`, not in `classify.classify`'s normal per-run digest, so no digest-writing change
is required for this unit).

---

## Verification Contract

- Run the full suite from the repo root: `python3 -m unittest discover -s tests`. It normally
  takes about two and a half minutes.
- Single-module runs during development: `python3 -m unittest test_contracts`,
  `python3 -m unittest test_classify`, `python3 -m unittest test_state` from `tests/`.
- No live task run is required for this change: it touches no envelope grammar, closeout
  terminal line, brief template, or halt record shape the
  `docs/solutions/logic-errors/stubbed-seams-agree-by-construction-first-live-run-found-five-contract-defects.md`
  warning covers. The disallow-list addition is data (a new glob string) and the scan is new,
  additive, and exercised entirely by the stub-based suite.

## Definition of Done

- `contracts.DISALLOWED_TOOLS` denies `kill`, `pkill`, and `killall`; `DESTRUCTIVE_TOOLS` does
  not.
- `classify.scan_self_kill` exists, is covered by the U2 test scenarios, and is reused (not
  duplicated) by `state._mark_crashed`.
- A stale-lease reclaim whose crashed task's own log named the previous Runner's PID in a
  kill-family command produces a `runner_self_kill` finding on the halted record; one whose log
  does not still produces the existing bare `runner_crashed` halt with no finding, unchanged from
  today.
- `python3 -m unittest discover -s tests` passes.
- No dead-end or experimental code from an approach that did not pan out is left in the diff.
