---
artifact_contract: ce-unified-plan/v1
artifact_readiness: implementation-ready
product_contract_source: ce-plan-bootstrap
created: 2026-08-27
depth: standard
---

# Observed CLI version in the terminal record

## Problem Frame

`state.StateStore.write_terminal()` takes a `cli_version` parameter, and every one of its three
call sites in `run.py` passes `contracts.CLI_VERSION_TESTED` — the pinned constant Relay was
tested against, not a measurement of anything. The terminal record in `state.json` therefore
always reports the same string regardless of which `claude` binary actually ran the tasks. If
the installed CLI drifts from the pinned version, nothing in `state.json` shows it; the field
looks like telemetry but is a hardcoded literal.

## Scope

**In scope:**
- Read the real `claude --version` output once per run, via a subprocess argument list (never a
  shell string), matching the wrapper style `launch.py` already uses for the `claude` binary.
- Record the observed version in the terminal record as a new field, alongside the existing
  pinned `cli_version` field (unchanged).
- Cover the read with a failure-tolerant fallback (`None`) so a missing binary, a nonzero exit,
  or a timeout never turns into an unhandled exception that changes the run's outcome.
- A test that fakes `claude --version` output in the test harness and asserts both fields land
  in the terminal record and differ when the fake reports a different version than
  `contracts.CLI_VERSION_TESTED`.

**Out of scope:**
- Changing `contracts.CLI_VERSION_TESTED` itself, or any logic that compares the two versions
  and acts on a mismatch (e.g., warning, halting). The task asks only for visibility in
  `state.json`; a consumer (a human, or a future halt class) reads it from there.
- Changing what `relay status`/`relay summary` print by default — `summary.py` gains the new
  field in its data dict for completeness, but no new CLI flag or formatted output is required.
- Re-reading the version more than once per run (e.g., once per task) — the CLI binary the
  runner launched does not change mid-run.

## Assumptions

- "The same subprocess wrapper `launch.py` already uses" means the same *style* of wrapper —
  an argument list passed to `subprocess`, never a shell string, matching the pattern
  `launch.py`'s own `launch()` and `gitread.run()` already use for git — not literally routing a
  `--version` probe through the full `launch()` function (session id, transcript discovery,
  timeout thread, lease heartbeat). Those exist to bound a long-running task/closeout process;
  a version check is a single fast synchronous call and does not need them. The new function
  lives in `launch.py` so it is colocated with the other code that invokes the `claude` binary.
- The observed version is read using the same scrubbed child environment (`launch.child_env(...)`)
  the task and closeout processes run under, since that is the `claude` binary Relay actually
  launches, and `run.py` already computes that `env` once at the top of `run()`.
- On any failure to obtain the version (missing binary, nonzero exit, timeout, unparseable
  output), the observed field is `None` rather than raising — consistent with the existing
  `cli_version` default of `None` in `_mark_crashed`'s terminal record, and because a version
  probe failing is not itself a reason to halt a run that otherwise would have succeeded.
- The real CLI's `--version` output is a single line like `2.1.247 (Claude Code)`; the observed
  field stores only the leading version token (`2.1.247`), parsed with a simple regex, so it is
  directly comparable to `contracts.CLI_VERSION_TESTED` (`"2.1.245"`, no suffix). Storing the raw
  line instead would make the two fields differ on formatting alone even when the version number
  matches, which defeats the purpose of the field.
- The new field is additive only. Existing readers of the terminal record (`summary.py`,
  `relay status`) that don't know about `cli_version_observed` keep working unchanged; no
  existing field is renamed or removed.

## Current State (research)

- `contracts.py:14` — `CLI_VERSION_TESTED = "2.1.245"`, the pinned constant.
- `state.py:394-403` — `write_terminal(self, run_status, halt_task=None, halt_class=None,
  cli_version=None)` builds the terminal dict (`run_status`, `halt_task`, `halt_class`,
  `cli_version`, `written_at`) and mutates it into `state["terminal"]`. No schema/completeness
  test constrains this dict's keys.
- `state.py:254-280` — `_mark_crashed` builds a terminal dict directly (not via
  `write_terminal`) when a stale lease is reclaimed, with `"cli_version": None` hardcoded, for
  the case where no run in this process ever reached a terminal write.
- `run.py:185-186, 190, 199-201` — the three call sites, all passing
  `contracts.CLI_VERSION_TESTED` as `cli_version`: the halted path, the completed path, and the
  crashed path inside the `finally` block (guarded by `wrote_terminal`).
- `run.py:121` — `env = launch.child_env(manifest, base_env, home)` is computed once near the
  top of `run()`, before the per-task loop, and is the environment every task/closeout process
  (and the version probe) should run under.
- `launch.py` — has no `--version`-style helper yet. `child_env()` builds the scrubbed
  environment; `build_args()`/`launch()` are for the full task/closeout process. `gitread.run()`
  (`gitread.py:23-32`) is the smaller, comparable pattern: `subprocess.run(cmd, capture_output=True,
  text=True, env=env, timeout=..., stdin=subprocess.DEVNULL)`, called through a small typed
  wrapper, not a shell string.
- `summary.py:151` — `"cli_version": terminal.get("cli_version")` in the summary data dict; no
  existing test in `tests/test_summary.py` asserts this key specifically.
- `tests/stub-claude/claude` — the fake `claude` binary the whole suite runs `claude` as, via
  `PATH=_paths.STUB_DIR + os.pathsep + ...` (`test_run.py:167`). It currently parses the
  runner's task/closeout flags and drives a queue protocol; it has no `--version` handling, so
  `claude --version` against it today falls through to the queue-consumption path and would
  either hang on a missing queue entry or consume a queue slot meant for a task.
- Real CLI output observed in this environment: `claude --version` → `2.1.247 (Claude Code)`,
  exit 0.
- `tests/test_launch.py` — the pattern for testing `launch.py` functions with an injected
  fake in place of the real subprocess call (`PopenContract`, `LaunchFailure` classes).
- `tests/test_state.py:213-231` (`Terminal` class) — the existing `write_terminal` test,
  currently only passing `cli_version="2.1.245"`.
- `tests/test_run.py` — the full-loop integration test that runs the real stub `claude` binary
  through `run.run()`; the only place a new `--version` call in `run()` will actually execute
  end-to-end rather than through an injected fake.

## Design

### 1. `launch.py` — add `cli_version()`

Add near the top-level constants (beside `SIGKILL_GRACE_SECONDS`):

```python
CLI_VERSION_TIMEOUT_SECONDS = 10
```

Add a small function, following `gitread.run()`'s shape (argument list, no shell, an injectable
runner for tests):

```python
_VERSION_TOKEN_RE = re.compile(r"^(\d[\w.\-]*)")


def cli_version(env, run=subprocess.run, timeout=CLI_VERSION_TIMEOUT_SECONDS):
    """The installed `claude` binary's own version, read once per run so drift from
    CLI_VERSION_TESTED shows up in state.json instead of staying silently invisible.
    Returns None on any failure -- a missing binary, a nonzero exit, a timeout, or output whose
    leading token doesn't start with a digit -- rather than raising, since a version probe
    failing is not a reason to fail the run, and a plausible-looking non-version word (a banner
    or update notice ahead of the real version line) is worse than a visible None."""
    try:
        proc = run(["claude", "--version"], capture_output=True, text=True, env=env,
                    timeout=timeout, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    match = _VERSION_TOKEN_RE.match(proc.stdout.strip())
    return match.group(1) if match else None
```

The pattern requires the leading token to start with a digit, so a banner line or update notice
ahead of the actual version (should the CLI ever print one) yields `None` instead of silently
storing a non-version word.

Requires `import re` at the top of `launch.py` (not currently imported). `subprocess` is already
imported. The `run` parameter name deliberately mirrors `gitread.run`'s injection style, distinct
from `launch()`'s own `popen` parameter, because this wraps `subprocess.run` (a single blocking
call), not `subprocess.Popen` (the long-running, streamed process `launch()` manages).

### 2. `run.py` — call it once, thread it through the three terminal writes

Right after `env = launch.child_env(manifest, base_env, home)` in `run()`:

```python
observed_cli_version = launch.cli_version(env)
```

Update the three `write_terminal` call sites to also pass `cli_version_observed`, without
changing how each already passes `cli_version` (positional at the halted site, keyword at the
other two):

- `run.py:185-186` (halted path):
  `store.write_terminal(contracts.RUN_HALTED, halt.task_id, halt.halt_class,
  contracts.CLI_VERSION_TESTED, cli_version_observed=observed_cli_version)`
- `run.py:190` (completed path):
  `store.write_terminal(contracts.RUN_COMPLETED, cli_version=contracts.CLI_VERSION_TESTED,
  cli_version_observed=observed_cli_version)`
- `run.py:200-201` (crashed path, inside `finally`):
  `store.write_terminal(contracts.RUN_CRASHED, cli_version=contracts.CLI_VERSION_TESTED,
  cli_version_observed=observed_cli_version)`

### 3. `state.py` — accept and store the new field

`write_terminal` gains a fifth parameter and one new dict key:

```python
def write_terminal(self, run_status, halt_task=None, halt_class=None, cli_version=None,
                    cli_version_observed=None):
    record = {
        "run_status": run_status,
        "halt_task": halt_task,
        "halt_class": halt_class,
        "cli_version": cli_version,
        "cli_version_observed": cli_version_observed,
        "written_at": _iso(self.now()),
    }
    self._mutate(lambda state: state.update(terminal=record))
    return record
```

`_mark_crashed` (`state.py:254-280`) builds its terminal dict directly, for the case where a
stale lease is reclaimed before any run in this process reached a terminal write, so there is no
observed version to record. Add the key there too, set to `None`, so every terminal record has
the same shape regardless of which code path wrote it:

```python
state["terminal"] = {
    "run_status": contracts.RUN_CRASHED,
    "halt_task": ids[0] if ids else None,
    "halt_class": contracts.HALT_RUNNER_CRASHED if ids else None,
    "cli_version": None,
    "cli_version_observed": None,
    "previous_holder": previous,
    "written_at": _iso(self.now()),
}
```

### 4. `summary.py` — pass the field through

`build()` (`summary.py:143-155`) already surfaces `terminal.get("cli_version")`. Add the sibling
read immediately after it:

```python
"cli_version": terminal.get("cli_version"),
"cli_version_observed": terminal.get("cli_version_observed"),
```

### 5. `tests/stub-claude/claude` — answer `--version`

Add a branch at the top of `main()`, before queue consumption, so a version probe never touches
the queue counter (which is reserved for task/closeout entries) and works even when no queue is
configured:

```python
def main(argv):
    if argv[:1] == ["--version"]:
        version = os.environ.get("RELAY_STUB_CLI_VERSION", "2.1.245 (Claude Code)")
        print(version)
        return 0

    flags = parse_args(argv)
    ...
```

Document the new knob in the module docstring's "Other knobs" list:
`RELAY_STUB_CLI_VERSION=<text>   overrides the fake --version output (default matches
CLI_VERSION_TESTED)`. Defaulting to a string that parses to the same value as
`contracts.CLI_VERSION_TESTED` keeps every existing test's terminal record showing matching
versions unless a test opts into drift by setting the env var.

## Test Scenarios

1. **`tests/test_launch.py` — `cli_version()` unit tests** (new test class, e.g. `CliVersion`):
   - Injected fake `run` returning `CompletedProcess` with `stdout="2.1.247 (Claude Code)\n"`,
     `returncode=0` → `cli_version(env, run=fake)` returns `"2.1.247"`.
   - Fake returning `returncode=1` → returns `None`.
   - Fake raising `FileNotFoundError` (missing binary) → returns `None`.
   - Fake raising `subprocess.TimeoutExpired` → returns `None`.
   - Fake returning empty `stdout` → returns `None`.
   - Fake returning `stdout="Update available: run claude update\n2.1.247 (Claude Code)\n"`
     (a non-version line ahead of the real one) → returns `None`, not `"Update"`.
2. **`tests/test_state.py` — `Terminal` class**: extend the existing
   `test_status_word_distinguishes_completed_halted_and_crashed` (or add a sibling test) to pass
   `cli_version_observed="2.1.247"` to `write_terminal` and assert
   `store.terminal()["cli_version_observed"] == "2.1.247"` alongside the existing
   `cli_version` assertion. Add a case with `cli_version_observed=None` (the failure path) to
   confirm the key is present and `None`, not omitted.
3. **`tests/test_run.py` — full-loop, real stub `claude` binary (the scenario the task names
   explicitly)**: in one of the existing full-run tests (or a new small one reusing the existing
   `MANIFEST`/`TRACKER_MD` fixtures), set `RELAY_STUB_CLI_VERSION` in the child env to a value
   that differs from `contracts.CLI_VERSION_TESTED` (e.g. `"9.9.9 (Claude Code)"`), run the
   manifest to completion, then read `store.terminal()` (or `state.json` directly) and assert:
   - `terminal["cli_version"] == contracts.CLI_VERSION_TESTED`
   - `terminal["cli_version_observed"] == "9.9.9"`
   - the two values differ.
   Also add (or extend an existing) run where `RELAY_STUB_CLI_VERSION` is unset, asserting the
   default resolves to `"2.1.245"` and equals `contracts.CLI_VERSION_TESTED` (both fields land,
   no drift) — covers the no-drift case the same scenario tests for symmetry.
4. **No existing test's terminal-record assertions should need to change** beyond adding the new
   key where a test builds its own expected terminal dict from scratch (grep test files for
   `"cli_version"` and `write_terminal(` before finishing implementation, per the task's own
   pattern of checking before adding).

## Files

- `skills/relay/scripts/relay/launch.py` — add `import re`, `CLI_VERSION_TIMEOUT_SECONDS`, and
  `cli_version()`.
- `skills/relay/scripts/relay/run.py` — compute `observed_cli_version` once; thread it into the
  three `write_terminal` call sites.
- `skills/relay/scripts/relay/state.py` — `write_terminal()` gains `cli_version_observed`;
  `_mark_crashed()`'s inline terminal dict gains the same key set to `None`.
- `skills/relay/scripts/relay/summary.py` — surface `cli_version_observed` in `build()`.
- `tests/stub-claude/claude` — handle `--version`; document `RELAY_STUB_CLI_VERSION`.
- `tests/test_launch.py` — new `cli_version()` unit tests.
- `tests/test_state.py` — extend `Terminal` coverage for the new field.
- `tests/test_run.py` — new/extended full-loop assertion that both fields land and can differ.

## Risks

- **The real CLI's `--version` format changes** (e.g., drops the trailing `(Claude Code)`, adds
  a prefix). The regex only takes the leading non-whitespace token, so a format change that
  keeps the version number first still parses correctly; a format change that moves the version
  number elsewhere would need a follow-up fix. Low risk, and `docs/solutions/` already documents
  the general hazard of trusting an unversioned external contract — this plan does not need a
  new entry for it, since the fallback-to-`None` path means a format change degrades to
  "observed unknown" rather than a crash.
- **Overhead per run**: one extra subprocess call (`claude --version`, typically well under a
  second) added to every run's startup, before the first task launches. Negligible against task
  timeouts measured in minutes.
- **Stub default drift**: the stub's default `RELAY_STUB_CLI_VERSION` value must be kept in sync
  with `contracts.CLI_VERSION_TESTED` by eye (no shared import — the stub is a standalone script
  the suite invokes as a subprocess, not a module it imports). If a future bump changes
  `CLI_VERSION_TESTED`, the stub default should be bumped in the same diff, or every existing
  test that doesn't set `RELAY_STUB_CLI_VERSION` will start observing (harmless but misleading)
  drift. Note this in the stub's docstring so it isn't missed silently.

## Verification

Run the full suite from the repo root: `python3 -m unittest discover -s tests`. Confirm the new
`cli_version()` unit tests, the extended `Terminal` state test, and the new/extended full-loop
`test_run.py` assertion all pass, and no existing test's expectations changed unintentionally.
