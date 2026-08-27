---
artifact_contract: ce-unified-plan/v1
artifact_readiness: implementation-ready
product_contract_source: ce-plan-bootstrap
created: 2026-08-27
depth: lightweight
---

# `comments_since` treats a missing baseline as unresolved, not "everything"

## Problem Frame

`comments_since(task_id, baseline_comment_id)` in the GitHub and Jira adapters looks up
`baseline_comment_id` in the fetched comment list and returns everything after it. When the
baseline id is *not* found in the list — because the comment was deleted, or edited away, or
any other reason the id no longer appears — both adapters fall through to `return entries`,
returning the full comment list as if the baseline were the very first comment.

`closeout.confirm_blocked_comment` (`closeout.py:267-279`) calls `comments_since` after a
blocked closeout and treats a non-empty result as "the blocker reached the card": `if newer:
return None`. Any pre-existing comment on the card — including ones that predate the run
entirely — makes `newer` non-empty when the baseline is missing, so a deleted or edited-away
baseline silently satisfies the check even when the closeout process wrote nothing. This is a
false pass on the one signal (`BLOCKED_UNRECORDED`) that exists specifically to catch an
operator-invisible blocked task (R42).

Already flagged as a residual in `docs/ideation/2026-08-25-relay-review-residuals.md:98-100`.

## Scope

**In scope:**
- `GithubAdapter.comments_since` (`skills/relay/scripts/relay/adapters/github.py:129-137`):
  when `baseline_comment_id` is given but not found in the fetched ids, return `[]` instead of
  `entries`.
- `JiraAdapter.comments_since` (`skills/relay/scripts/relay/adapters/jira.py:151-159`): the
  identical change.
- One new test per adapter in `tests/test_adapters.py` pinning the new behavior: a baseline id
  absent from the fetched comments returns `[]` (or a falsy result), not the full list.

**Out of scope:**
- `MarkdownAdapter.comments_since` (`adapters/markdown.py:125-136`). Its baseline is a plain
  integer count-of-comments-already-seen, not a comment id looked up by identity, so there is no
  "id not found" case to mishandle — an unparseable baseline is a different, already-handled
  failure mode (`except (TypeError, ValueError): return list(entry["comments"])`), and the task
  does not ask to change it.
- The `baseline_comment_id is None` branch in either adapter — "no baseline was given" must keep
  returning every comment, unchanged.
- The found-baseline branch (`entries[ids.index(baseline) + 1:]`) — unchanged.
- `closeout.confirm_blocked_comment` itself. It already treats an empty `newer` list as
  unresolved (falls through to the `BLOCKED_UNRECORDED` finding at `closeout.py:278-279`), so
  fixing the adapters to return `[]` on a missing baseline is sufficient; no change needed there.
- Any change to `run.py`'s call sites (including the closeout-brief `comments` call at
  `run.py:502`, see Current State) or the `BLOCKED_UNRECORDED` finding shape — both already
  consume the adapters' fixed return value correctly with no code change of their own.
- `closeout.render`'s `comments` parameter and template rendering — unchanged; it already
  accepts whatever list its caller passes.

**Amended during code review:** `tests/_fakes.py`'s `FakeAdapter.comments_since` carried the
identical pre-fix fallback (`return entries` on a missing baseline), and both existing
`BlockedCommentConfirmation` tests use baseline `"c1"`, which is present in their fixture's ids
— so neither the real adapters' fix nor the fake's stale mirror of it was exercised through
`confirm_blocked_comment` at all. Brought into scope: `FakeAdapter.comments_since` now returns
`[]` on a missing baseline too, matching the real adapters, and a new
`BlockedCommentConfirmation` test (`test_a_baseline_deleted_from_the_card_is_a_finding_not_a_silent_pass`)
exercises exactly that path.

## Current State (research)

- `skills/relay/scripts/relay/adapters/github.py:129-137` — `comments_since`, the buggy fallback
  is the final `return entries` on line 137.
- `skills/relay/scripts/relay/adapters/jira.py:151-159` — same shape, buggy fallback on line 159.
- `skills/relay/scripts/relay/adapters/markdown.py:125-136` — the third adapter's
  `comments_since`, out of scope (see above).
- `skills/relay/scripts/relay/closeout.py:267-279` — `confirm_blocked_comment`, the consumer the
  task names. `if newer: return None` already treats an empty list as "not confirmed," so `[]`
  on a missing baseline flows through correctly with no other code change.
- `skills/relay/scripts/relay/run.py:502` — `_run_closeout` also calls
  `ctx.adapter.comments_since(ctx.task.id, ctx.baseline_comment_id)`, on every outcome (landed or
  blocked), and passes the result into `closeout.render`'s `comments` field
  (`closeout.py:181`, via `_comment_lines`), which is written into the closeout brief the
  Closeout process reads. This is a second consumer of the same missing-baseline branch, not
  just `confirm_blocked_comment`. Today, a deleted or edited-away baseline makes this call
  return the full comment history (potentially including comments that predate the run) into
  that brief; after this fix it returns `[]` in that same case. The narrower, honest result is
  correct here too — a brief that can't identify what's new since the baseline should say so,
  not pad itself with unrelated history — so this call site needs no separate code change, only
  this note that the fix is not confirm_blocked_comment-only.
- `tests/test_adapters.py:213-221` (Jira) and `:301-304` (GitHub) — the existing
  `comments_since` coverage: found-baseline and `None`-baseline cases only. No existing test
  covers a baseline id absent from the fetched list.
- `tests/test_closeout.py:291-311` — `BlockedCommentConfirmation`, the test class covering
  `confirm_blocked_comment` against a `FakeAdapter`; no change needed here since the fix is at
  the adapter layer and this class already exercises the empty-`newer` path
  (`test_no_new_comment_after_a_blocked_closeout_is_a_finding_naming_the_card`).
- `tests/_fakes.py` — has the `FakeAdapter` used by closeout tests; not used by the
  adapter-level tests being added (those use each adapter's own fixture/fake HTTP opener, per
  the existing test classes in `test_adapters.py`).

## Design

Both adapters get the identical one-line change, changing the miss branch from returning the
full list to returning an empty one:

```python
def comments_since(self, task_id, baseline_comment_id):
    entries, _ = self._comments(task_id)
    if baseline_comment_id is None:
        return entries
    ids = [entry["id"] for entry in entries]
    baseline = str(baseline_comment_id)
    if baseline in ids:
        return entries[ids.index(baseline) + 1:]
    return []
```

No signature change, no new parameter, no new return shape — callers that already treat the
return value as "a list of comment dicts, iterate or check truthiness" (both `closeout.py` and
any future caller) see an empty list exactly as they would for a card that truly has no newer
comments. This keeps the contract `comments_since` documents to `verify.py` unchanged:
"the comments newer than a baseline id" — an unfindable baseline has no comments newer than it
that this adapter can vouch for, so `[]` is the honest answer, not `entries`.

## Test Scenarios

1. **`tests/test_adapters.py` — Jira**, alongside
   `test_comments_since_a_baseline_returns_exactly_the_newer_ones_in_order` and
   `test_comments_since_none_returns_every_comment`: add
   `test_comments_since_an_absent_baseline_is_unresolved_not_everything`, using the same
   `self.jira(self.opener())` fixture as the existing tests (baseline ids `10001`-`10003` are
   the fixture's known comments), asserting `adapter.comments_since("IW-83", "99999")` (an id not
   in the fixture) returns `[]`.
2. **`tests/test_adapters.py` — GitHub**, alongside
   `test_comments_since_a_baseline_returns_the_newer_comment`: add
   `test_comments_since_an_absent_baseline_is_unresolved_not_everything`, using
   `self.github(self.run_for())`, asserting `adapter.comments_since("12", "IC_999")` (an id not
   in the fixture, which has `IC_1`/`IC_2`) returns `[]`.
3. No new test needed for `closeout.confirm_blocked_comment` — its existing
   `test_no_new_comment_after_a_blocked_closeout_is_a_finding_naming_the_card` already proves an
   empty `comments_since` result produces the `BLOCKED_UNRECORDED` finding; the adapter fix makes
   the missing-baseline case route into that already-tested path.

## Files

- `skills/relay/scripts/relay/adapters/github.py` — `comments_since`, change the final
  `return entries` to `return []`.
- `skills/relay/scripts/relay/adapters/jira.py` — `comments_since`, same change.
- `tests/test_adapters.py` — one new test per adapter (Jira and GitHub) pinning the
  absent-baseline behavior.

## Risks

- None beyond the fix's own scope. The change is a single-line behavior narrowing (fewer cases
  return non-empty), guarded by the found-baseline and `None`-baseline tests already in the
  suite, which the plan does not modify.
- `run.py:502`'s closeout-brief call (see Current State) inherits the same narrowing: a landed
  task whose baseline comment was deleted or edited away will now show an empty comment history
  in its closeout brief instead of the full (inaccurate) one it showed before. This is the
  correct behavior for the reason given above, not a regression, but it is a second observable
  effect of a fix framed around one call site.

## Verification

Run the full suite from the repo root: `python3 -m unittest discover -s tests`. Confirm the two
new `comments_since` tests pass, and the existing found-baseline and `None`-baseline tests for
both adapters, plus the full `BlockedCommentConfirmation` class in `tests/test_closeout.py`,
still pass unchanged.
