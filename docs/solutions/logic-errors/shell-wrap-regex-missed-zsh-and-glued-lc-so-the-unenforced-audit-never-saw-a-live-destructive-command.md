---
title: The shell-wrap regex only saw bash and sh, so a live /bin/zsh -lc destructive command with no && never reached the unenforced audit
date: 2026-08-30
category: logic-errors
module: classify
problem_type: logic_error
component: runner
severity: high
root_cause: missing_validation
resolution_type: code_fix
related_components: [contracts, classify, gitread, run]
symptoms:
  - "a live Codex Task ran `/bin/zsh -lc 'rm -rf /tmp/x'` with no `&&` and the unenforced-restrictions audit never flagged it"
  - "the suite stayed green throughout, because every fixture either used bash/sh or joined its destructive command with `&&`"
tags: [shell-wrap-regex, codex-backend, zsh, unenforced-restrictions, self-authored-fixtures, first-live-run]
---

# The shell-wrap regex only saw bash and sh, so a live /bin/zsh -lc destructive command with no && never reached the unenforced audit

## Problem

Relay's U10 unit (`docs/plans/2026-08-28-1101-feat-pluggable-cli-backends-plan.md`) audits a
Codex Task's `tool_use` calls after exit against Relay's `DISALLOWED_TOOLS` glob list, since
Codex cannot enforce that list at launch the way Claude Code can. The audit only works if
`classify._command_candidates` can pull the real inner command out of Codex's shell wrapper,
because Codex wraps every command as `/bin/zsh -lc '<command>'` before it ever reaches the
transcript.

The unwrap regex, `classify._SHELL_WRAP`, only matched `(?:ba)?sh` (`bash` or `sh`), never
`zsh`, and required the flag as a single character (`-l` or `-c`), never Codex's glued `-lc`.
Neither gap raised anywhere in the suite. 707 tests passed. A live task then ran
`/bin/zsh -lc 'rm -rf /tmp/x'`, a destructive command with no `&&` joining it to anything
else, and the audit never saw it.

## What Didn't Work

**Trusting that "the suite is green" meant the unwrap regex was covered.** It was covered for
every fixture that existed, and every fixture existed because it had been hand-written to a
command shape the regex already matched: `bash -lc` or `sh -c`, or a destructive command
chained with `&&` after something else.

**Why the `&&` cases stayed green even though the unwrap itself was also broken for them.**
`_command_candidates` splits on `&&` / `||` / `;` / newline as a second, independent pass over
whatever `_unwrap_command` returns. When unwrap fails, it returns the wrapped string
unchanged, quotes and all: `"/bin/zsh -lc 'true && rm -rf /tmp/x'"`. Splitting that raw string
on the literal `&&` still produces a segment that starts with `rm -rf` (`" rm -rf /tmp/x'"`
stripped to `"rm -rf /tmp/x'"`), and `fnmatch.fnmatch` against the glob `rm -rf*` matches a
leading substring regardless of what trails it, leftover closing quote included. So a
two-command line reached the glob by an accident of splitting, while a single destructive
command with nothing to split on had no such accident to save it, and was the one shape no
fixture had ever tried.

## Solution

Landed in `06622f2`.

`classify._SHELL_WRAP` in `skills/relay/scripts/relay/classify.py` now matches `zsh` alongside
`bash`/`sh`, and accepts a glued flag cluster (`-[lc]+`) instead of a single character:

```python
_SHELL_WRAP = re.compile(
    r"^(?:/(?:usr/)?bin/)?(?:[\w.+-]+/)*(?:zsh|bash|sh)(?:\s+-l)?(?:\s+-[lc]+)\s+(.*)\Z",
    re.DOTALL | re.IGNORECASE,
)
```

The same commit adds a `_GIT_C` strip so `git -C <dir> reset --hard` is recognized under the
`git reset --hard*` glob the same as a bare `git reset --hard`, adds `-m` to
`gitread.paths_touched_in_range` so the Task path bound sees files a merge commit published
(a merge's own diff is otherwise empty), and turns unreadable Codex evidence into a halt
(`HALT_UNEXPECTED_ERROR`) instead of a silent skip, since the audit's absence must never look
like the audit's pass.

Pinned by `tests/test_classify.py`, `test_a_zsh_lc_rm_rf_without_and_still_matches` (single
command, no `&&`, the exact live shape) and `test_git_c_reset_hard_matches_the_destructive_glob`.

## Why This Works

This is the same family as
`docs/solutions/logic-errors/stubbed-seams-agree-by-construction-first-live-run-found-five-contract-defects.md`
and
`docs/solutions/logic-errors/denial-regex-anchored-immediately-after-tool-name-missed-real-bash-denials.md`:
a regex written and pinned entirely against self-authored fixtures agrees with those fixtures
by construction, and the fixtures were shaped by whoever wrote the regex, so they can only
confirm what the author already believed the input looked like. Here the belief was "Codex
wraps in bash or sh, with a single flag character", which was true of no real Codex process,
and the `&&`-chained fixtures accidentally passed for a reason unrelated to the thing being
tested, which hid that the unwrap itself was already broken.

## Prevention

**A live run against a real backend is the only instrument that finds a shell-wrap regex gap,
same as the neighboring doc's rule.** No fixture invented for this glob list would have caught
a vendor CLI's actual wrapping shape without first observing that shape from a real process.

**When a glob or regex match still succeeds for the wrong reason, a passing test can hide a
broken step upstream of it.** The `&&`-chained fixtures passed because splitting on a literal
`&&` inside an unstripped wrapper string happens to leave a matching prefix, not because the
wrapper was correctly stripped. A fixture that isolates the single-command, no-separator case
is what exposes that the earlier step was never exercised on its own.

## Related Issues

- `docs/solutions/logic-errors/stubbed-seams-agree-by-construction-first-live-run-found-five-contract-defects.md`
  is the general rule this case follows: a contract between Relay and a real external process
  cannot be verified by a fixture the same author wrote to match their own parser.
- `docs/solutions/logic-errors/denial-regex-anchored-immediately-after-tool-name-missed-real-bash-denials.md`
  is the same shape one unit earlier: a regex pinned only against self-authored fixtures missed
  a real transcript shape until a live run found it.
