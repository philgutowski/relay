---
title: Reusing the DISALLOWED_TOOLS glob to scan forensic log text matched "killing worker 4821" as a kill command
date: 2026-08-31
category: logic-errors
module: classify
problem_type: logic_error
component: runner
severity: medium
root_cause: wrong_api
resolution_type: code_fix
related_components: [state, contracts]
symptoms:
  - "a self-kill scan over a crashed task's raw stdout log would flag ordinary prose like \"killing worker 4821\" as a kill-family command"
  - "matching a whole compound shell leaf (`kill -9 100 && echo pid 61799 done`) could read an unrelated number named later in the chain as a PID the kill itself targeted"
tags: [fnmatch, glob, word-boundary, self-kill, disallow-list, forensic-scan]
---

# Reusing the DISALLOWED_TOOLS glob to scan forensic log text matched "killing worker 4821" as a kill command

## Problem

Relay #47 added `kill`, `pkill`, and `killall` to `DISALLOWED_TOOLS` (`contracts.py`) after a
task killed its own Runner process (round six #40). A companion feature,
`classify.scan_self_kill` (`skills/relay/scripts/relay/classify.py:176`), reads a crashed task's
raw stdout log and attaches a `runner_self_kill` finding when the task's own commands named the
old holder's PID.

The first draft reused the same `"kill*"` glob `DISALLOWED_TOOLS` already carried, matched with
`fnmatch` against whole string leaves pulled out of the task's JSON log. `fnmatch` has no
word-boundary concept, so `"kill*"` matches `"killing worker 4821"` as readily as it matches a
real `kill` invocation. A leaf of ordinary prose describing that a worker process was killed
would fabricate a `runner_self_kill` finding on a task that never ran `kill` at all. A related
gap: matching against the whole (possibly compound) log leaf, rather than a single command
segment, let an unrelated number named later in a chained command (`kill -9 100 && echo pid
61799 done`) be misread as a PID the `kill` itself targeted, since `61799` sits in the same
string as the real `kill -9 100`.

## Why This Works

The two globs (`DISALLOWED_TOOLS`'s `"kill*"` and its two siblings) are right for the job they
were built for: matching every flag spelling of a command name against the CLI's own
`--deny`/`--disallowedTools` flag grammar, where the candidate strings are single command
segments the runner itself constructed, not arbitrary prose. Reusing that same glob to scan
free-text log content is a different job with a different failure mode, and the glob's own
looseness, harmless in its original context, becomes a false-positive source in the new one.

The fix, in `classify.py:146-159`, is `scan_self_kill`'s own stricter regex:

```python
_PID_TOKEN_RE = re.compile(r"\b\d{2,}\b")

_KILL_COMMAND_RE = re.compile(
    r"^(?:%s)(?:\s|$)" % "|".join(
        re.escape(contracts.disallow_inner(p).rstrip("*")) for p in contracts.KILL_LIKE_TOOLS
    )
)
```

anchored to the start of a single command segment and requiring a following space or end of
string, so `"killing worker 4821"` never matches. It is matched against `_shell_parts`, the same
single-command segments `_command_candidates` already splits a compound line into
(`classify.py:199`), not the whole leaf, so a PID named later in a chained command is never
attributed to an earlier `kill`. A cheap prefilter (`"kill" not in leaf: continue`,
`classify.py:197`) skips the regex entirely on leaves that cannot match, since unwrapping or
splitting a leaf never invents that substring.

`scan_self_kill` is forensic and best-effort, called from inside `StateStore._mutate`'s
`flock`-held critical section (`state.py:282-313`, on the reclaim path that marks a stale lease
`runner_crashed`). It is wrapped in a broad `except Exception: finding = None` there, on the
existing repo lesson that a helper called inside a lock-held region must never raise (see
`docs/solutions/logic-errors/version-probe-between-lease-acquire-and-try-finally-must-never-raise.md`):
an uncaught exception from scanning untrusted, adversarial-shaped log JSON would skip the state
write and strand the same stale lease on every future reclaim.

## Prevention

**A glob or regex built for one matching job is not automatically sound for a second job, even
one that looks similar.** `DISALLOWED_TOOLS`'s globs match constructed command-flag strings
against a CLI flag grammar; free text pulled from a log is a different domain, and `fnmatch`'s
lack of word-boundary semantics is invisible until something reuses the pattern against prose.
Before reusing an existing pattern for a new kind of input, ask what shapes the new input can
take that the original never had to handle, and write a purpose-built regex when the answer
includes ordinary text.

## Related Issues

- `docs/solutions/logic-errors/version-probe-between-lease-acquire-and-try-finally-must-never-raise.md`
  is the reused lesson behind `scan_self_kill`'s fail-closed wrapper: a helper called inside a
  lock-held critical section must never raise.
