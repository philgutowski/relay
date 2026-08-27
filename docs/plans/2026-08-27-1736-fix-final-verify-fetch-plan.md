---
title: Final Landing Verify Fetches Before Reading Remote - Plan
type: fix
date: 2026-08-27
artifact_contract: ce-unified-plan/v1
artifact_readiness: implementation-ready
product_contract_source: ce-plan-bootstrap
execution: code
---

## Goal Capsule

**Objective:** the landing verdict the runner records for a task is decided
against the remote's actual current state, not against a locally cached
tracking ref that only reflects the runner's own last push.

**Means:** pass `do_fetch=True` to the one `verify.verify` call in the run
loop that decides landing (`run.py:449`), leaving the two pre-push
`SCOPE_CODE` calls and `verify.startup_reverify` on the default (KTD1).

**Authority hierarchy:** the task text is the source of truth; this plan adds
no product scope beyond it.

**Stop conditions:** any change to a halt class, or any change to
`verify.startup_reverify`'s call shape, is out of bounds and must stop before
writing.

**Execution profile:** single-session code fix plus tests, run to green
locally.

**Tail ownership:** this plan's implementer commits the fix; it does not
merge, push, or open a follow-up.

## Product Contract

### Summary

`run.py`'s final full-scope verify, which is the check that decides whether a
task is recorded as landed, reads `origin/<default>` from the local git
tracking ref without fetching first. That ref was last updated by the
runner's own push earlier in the same task's run, so the check can prove the
push succeeded but cannot see a remote that moved since, including a
concurrent force push. Add `do_fetch=True` to that one call.

### Problem Frame

`verify.verify` defaults `do_fetch=False` (`verify.py:117`). Fetching before
reading is opt-in per caller, and only `cli.py`'s `verify` verb opts in
(`cli.py:219`). All three run-loop call sites (`run.py:375`, `run.py:422`,
`run.py:449`) use the default. The two `SCOPE_CODE` calls (375, 422) run
before or in place of a push, so a stale tracking ref there is not a
correctness gap — nothing has been pushed yet, or the push just happened
(422) and immediate divergence is not something those checks claim to catch.
The `SCOPE_FULL` call at 449 is different: it runs after both the merge push
(`run.py:421`, inside `gitwrite.local_merge_tail`) and the closeout's own
push (`run.py:539`), and its `landed` verdict is what the runner and the
summary treat as ground truth for whether the task shipped.

### Requirements

- **R1.** The final full-scope verify in `_merge_route` (`run.py:449`) calls
  `verify.verify(..., do_fetch=True, ...)`, so `head_equals_remote` is read
  after a fresh fetch of the actual remote state. `gitread.fetch` defaults to
  `remote="origin"` (`gitread.py:84-85`), so this covers `origin/<default>`
  only. `mirror_equals_head` reads whatever remote name
  `manifest.project.mirror` names (`gitwrite.py:194-207` — not necessarily
  `origin`), so it is out of scope for this fix; see the non-goal in Scope
  Boundaries.
- **R2.** The two `SCOPE_CODE` verify calls in `run.py` (`_timeout_route` at
  line 375, `_merge_route` at line 422) keep `do_fetch`'s default
  (`False`). No behavior change at those call sites.
- **R3.** `verify.startup_reverify` (`verify.py:331`) is unchanged: its own
  `verify.verify` call keeps the default `do_fetch=False`.
- **R4.** No `contracts.py` halt class is added, removed, or renamed. The
  existing `HALT_PARTIAL_LANDING` / verdict-derived halt classes continue to
  cover a remote that fails the post-fetch check.
- **R5.** A fetch failure at `run.py:449` does not crash the run. This
  already holds structurally: `verify.verify`'s own fetch call
  (`verify.py:128-132`) swallows `gitread.GitError` and proceeds to read
  whatever tracking ref state exists, so wiring `do_fetch=True` at the call
  site adds no new failure mode.

### Success Criteria

- A test proves the final verify call fetches before reading remote state
  (R1).
- A test proves that when the remote diverges after the runner's own pushes
  land, the run's recorded outcome for that task is not landed, where the
  same scenario without the fetch would have read as landed.
- `python3 -m unittest discover -s tests` stays green.

### Scope Boundaries

- No change to `verify.py`'s check logic, `Verdict` shape, or
  `_finish`/`hand_landing` behavior. The fix is call-site wiring only.
- No change to `cli.py`'s `verify` verb (already correct).
- No change to `_timeout_route`, `_blocked_route`, or any halt-class mapping.
- A follow-up task is not opened; the task text names exactly one call site
  and this plan implements exactly that.

## Planning Contract

### Key Technical Decisions

- **KTD1. Flip `do_fetch` at the call site, not the default.** `run.py:449`
  becomes `verify.verify(ctx.manifest, ctx.store.get(ctx.task.id), ctx.adapter, scope=verify.SCOPE_FULL, do_fetch=True, env=ctx.env, now=ctx.now)`.
  Rejected alternative: flipping `verify.verify`'s own default to `True`
  would also fetch on both `SCOPE_CODE` calls and inside
  `startup_reverify`, which the task explicitly excludes and which would add
  network calls to paths that do not need them (Requirements R2, R3).

### Existing Patterns to Follow

- `cli.py:219`'s `cmd_verify` is the existing precedent for
  `do_fetch=True`; `run.py:449` becomes the same shape, keyword-for-keyword
  aside from `env`/`now`.
- `verify.verify`'s fetch step (`verify.py:128-132`) already wraps
  `gitread.fetch` in a `try/except gitread.GitError: pass`, so no new
  error handling is needed at the call site.

### Non-goals

- `mirror_equals_head` staying fresh is out of scope. `gitread.fetch` only
  fetches `origin` (`gitread.py:84-85`); a manifest whose `project.mirror`
  names a different remote still reads that remote's locally cached tracking
  ref at `run.py:449`, exactly as it does today. The task names one call
  site's `do_fetch` argument, not a fetch of every configured remote; widening
  the fetch is a separate task if a manifest ever configures `project.mirror`
  on a non-`origin` remote. No manifest in this repo does today.

### Sequencing

Single unit; no dependencies.

## Implementation Units

### U1. Fetch before the final landing verify

**Goal:** `run.py:449`'s verify call fetches the remote before deciding
`landed`, and every other `verify.verify` call site keeps its current
`do_fetch` value (R1, R2, R3, R4).

**Requirements:** R1, R2, R3, R4, R5

**Files:**
- `skills/relay/scripts/relay/run.py` (the `_merge_route` final verify call,
  line 449)
- `tests/test_run.py` (new test: diverged remote after push yields non-landed)
- `tests/test_verify.py` (new test: `do_fetch=True` observably fetches
  before reading)

**Approach:**

Add `do_fetch=True` to the `verify.verify(...)` call at `run.py:449`. Leave
`run.py:375`, `run.py:422`, and `verify.py:331`'s internal call untouched.

**Test scenarios:**

1. **`do_fetch=True` picks up remote state a stale local tracking ref would
   miss** (`tests/test_verify.py`). Build a real repo with a real bare
   `origin` (`_repo.make_repo`), matching the existing suite's no-mock,
   real-git convention (no test in this repo uses `unittest.mock`). Advance
   `origin/main` from a second location (a second clone, or a direct
   `git -C <bare> update-ref`) without running `git fetch` in the repo under
   test, so the repo's local `refs/remotes/origin/main` is stale relative to
   the bare repo's actual `main`. `gitread.fetch` permanently updates the local tracking ref, so call
   order is load-bearing: call `verify.verify(manifest, record, adapter,
   scope=verify.SCOPE_FULL, do_fetch=False)` **first** and assert
   `head_equals_remote`'s evidence still shows the stale sha (the control).
   Only then call the same verify with `do_fetch=True` and assert its
   evidence shows the *new* remote sha (proving the fetch ran). Running the
   `do_fetch=True` call first would resolve the tracking ref before the
   `do_fetch=False` call ever reads it, collapsing the comparison. Keep both
   assertions in the same test so it fails loudly if fetch stops running.

2. **A remote that diverges after the runner's own pushes is not landed**
   (`tests/test_run.py`, extending the `RunCase` fixture used by
   `EndToEnd`). Give the bare `origin` repo (`<repo>.git`, created by
   `_repo.make_repo`) a `post-receive` hook that force-updates
   `refs/heads/<default>` to a pre-pushed divergent ref (e.g.
   `refs/heads/rogue`, pushed to the bare repo once during test setup, from
   an unrelated commit, before the hook is installed) instead of leaving it
   at what the client just pushed. A post-receive hook fires on every push to
   the bare repo, not just pushes to `refs/heads/<default>`, so the hook must
   read the updated ref names from its stdin (`old new refname` lines) and
   act only when one of them is `refs/heads/<default>`; count *those*
   invocations with a marker file under `$GIT_DIR`, ignoring any update to
   `refs/heads/rogue`. Trigger the divergence on the second such invocation —
   the closeout's push at `run.py:539` (the merge push at `run.py:421` is the
   first). Filtering by ref name this way means the setup-time rogue push and
   push order cannot miscount invocations: an early trigger would make the
   closeout's own push a non-fast-forward and halt the run before reaching
   the code under test. Run a normal landing task through `self.go()`. Assert the task's
   final record is **not** `contracts.STATUS_LANDED` (e.g. it halts with
   `contracts.HALT_PARTIAL_LANDING` or the verdict's own `halt_class`, per
   `verify._finish`), and that `record["verify"]["checks"]["head_equals_remote"]["result"]`
   is `verify.FAIL` with evidence naming the rogue sha, not the sha the
   runner pushed. Name in a comment or docstring that this same fixture
   would report `landed` if `do_fetch` were `False`, so a future regression
   that reverts R1 has a named failure mode to point at.

**Verification:** `python3 -m unittest test_verify` and
`python3 -m unittest test_run` from `tests/`, then the full suite.

## Verification Contract

- `python3 -m unittest discover -s tests` from the repo root, full green,
  per this repo's standing gate.
- No new external dependencies; the divergent-remote test uses only `git`
  plumbing already available to the existing `_repo.py` fixture helper.
- This is the one change class in `CLAUDE.md` that names a required live
  check ("After changing a contract between processes... run one live task
  against a throwaway target"). `do_fetch=True` is a call-site argument
  change inside one process (the runner), not a contract change between the
  Task process, the Closeout process, and the runner — the envelope
  grammar, brief templates, closeout terminal line, halt record, and
  classify digest keys are all untouched. A live run is not required by that
  rule, but running one against the throwaway proof target
  (`~/Documents/PhilAI/relay-proof`, per memory) is a reasonable extra
  confidence check if time allows; it is not blocking for this plan.

## Definition of Done

- `run.py:449` passes `do_fetch=True`; `run.py:375`, `run.py:422`, and
  `verify.py:331` are byte-for-byte unchanged in their `verify.verify` call
  arguments.
- Both new tests exist, are named for what they prove, and pass.
- No halt class in `contracts.py` was added, removed, or renamed.
- `python3 -m unittest discover -s tests` passes with no regressions.
- No leftover scratch files, debug prints, or abandoned hook scripts from
  building the divergent-remote fixture.
