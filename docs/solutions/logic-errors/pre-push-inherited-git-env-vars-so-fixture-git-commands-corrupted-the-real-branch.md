---
title: The pre-push hook inherited git's own environment variables, so the fixture suite's git commands corrupted the branch the hook was protecting
date: 2026-08-28
category: logic-errors
module: runner
problem_type: logic_error
component: runner
severity: high
root_cause: config_error
resolution_type: code_fix
related_components: [gitwrite, tests._repo, pre-push-hook, external-gate, branch-state]
symptoms:
  - "git push was refused with \"Ran 508 tests ... FAILED (errors=319)\" in about 12 seconds, reading exactly like a suite regression"
  - "the branch ref had silently advanced through eight \"initial\" commits across repeated push attempts, stacked on top of real work"
  - "git status showed every previously tracked file (skills/, tests/, docs/, CLAUDE.md, CONCEPTS.md) as untracked, because the working tree was being diffed against the wrong committed tree"
  - "the identical suite run outside the hook's environment passed 508 tests in 265 seconds, a 22x speed mismatch that was the actual tell"
  - "a first fix attempt (unsetting GIT_DIR and its siblings) still failed the same way, because the one-shot override used to install it was itself leaking back in through GIT_CONFIG_PARAMETERS"
tags: [subprocess-environment, git-config-leak, pre-push-hook, fixture-isolation, git-dir-override, branch-corruption, silent-failure, external-gate]
---

# The pre-push hook inherited git's own environment variables, so the fixture suite's git commands corrupted the branch the hook was protecting

## Problem

Relay's `pre-push` hook is deliberately untracked (`.git/hooks/pre-push`, not version controlled), added 2026-08-27 specifically so that no commit, including one from a Relay-run task, can disable the gate protecting `main` (session history). It runs the full suite before allowing a push:

```sh
#!/bin/sh
# Local-only gate, not tracked by git, so a commit inside the repo cannot disable it.
# Added 2026-08-27 before the first Relay-on-Relay self-run.
echo "pre-push: running the unittest suite"
python3 -m unittest discover -s tests
```

It ran that command with no environment cleanup. Git exports `GIT_DIR` into a hook's environment when the push that triggered it was made from a linked worktree rather than the primary checkout — documented git behavior, not a git defect, and confirmed directly this session: pushing from an ordinary checkout, a diagnostic hook's captured environment carried no `GIT_DIR` at all; pushing the same way from a worktree, it carried `GIT_DIR` set to that worktree's own git directory, and still no `GIT_WORK_TREE` in either case. `GIT_DIR` overrides `git -C <path>`, so any `git -C <somewhere>` call made from inside the hook's process tree is silently redirected to the repository the hook exists to protect, no matter what path was passed on the command line.

The suite's shared fixture helper, `tests/_repo.py`, builds a real git repository on disk for almost every test that touches git. Its `git()` wrapper (`tests/_repo.py:11-13`) never sets an explicit environment either, so it inherits the ambient one exactly like a raw `subprocess.run` call would:

```python
def git(repo, *args, check=True):
    return subprocess.run(["git", "-C", repo] + list(args), capture_output=True, text=True, check=check,
                          stdin=subprocess.DEVNULL)
```

Under `GIT_DIR`, a `git()` call is redirected exactly as readily as a raw one; the wrapper carries no protection against this class of leak; the only thing it protects against is a caller forgetting `-C`. `make_repo()`'s very first git call, `git(repo, "init", "-q", "-b", "main")` at `tests/_repo.py:27`, is redirected under `GIT_DIR` too, but that redirection is silent: `git init` against a repository that already exists is close to a no-op, so nothing about it is visible in the failure. The call that makes the leak visible is the one further down that actually writes new state. `make_repo()` needs a synthetic commit identity for its `identity=False` case (per its own docstring, so the test environment is scrubbed of the global identity and the check is real), so it hand-rolls a second, separate `subprocess.run` call instead of reusing `git()`, and seeds that call's environment from the ambient process environment (`tests/_repo.py:36-44`):

```python
git(repo, "add", "-A")
commit_env = dict(os.environ)
if not identity:
    commit_env.update({
        "GIT_AUTHOR_NAME": "x", "GIT_AUTHOR_EMAIL": "x@example.invalid",
        "GIT_COMMITTER_NAME": "x", "GIT_COMMITTER_EMAIL": "x@example.invalid",
    })
subprocess.run(["git", "-C", repo, "commit", "-q", "-m", "initial"], check=True, env=commit_env,
               capture_output=True, stdin=subprocess.DEVNULL)
```

`commit_env = dict(os.environ)` makes the inheritance explicit here, but it changes nothing that was not already true of the `git()` calls around it; under a normal terminal all of it is harmless, because `GIT_DIR` usually is not set. Under the `pre-push` hook, triggered from a worktree, it is set, by git itself, to the repository the hook is protecting. So this `git -C <tmp> commit` call silently commits into that repository instead of the throwaway fixture directory, for every fixture repository the suite creates, and it is the one whose redirection an operator actually sees, because a commit against an already-existing repository leaves a new, real commit behind where the earlier redirected calls left nothing visible at all.

## Symptoms

Two symptoms, and the first one actively hid the second.

1. `git push` was refused by the hook with output resembling `Ran 508 tests ... FAILED (errors=319)`, completing in about 12 seconds. Read at face value this looks exactly like a real regression, a large fraction of the suite failing right after a plan-authoring session had landed several commits, which is exactly when you would expect to suspect your own diff.

2. Separately, and more dangerous, the branch ref had silently advanced through eight "initial" commits across repeated push attempts in the same session, each one another fixture's stray commit landing on the real branch. `git log --oneline` showed a chain of commits titled "initial" stacked on top of real work. Because only the branch ref moved and the working tree was never touched by these stray commits, `git status --short` showed every previously tracked file (`skills/`, `tests/`, `docs/`, `CLAUDE.md`, `CONCEPTS.md`) as untracked (`??`), because git was diffing the intact working tree against the committed tree of an unrelated, nearly-empty "initial" fixture commit.

The tell that separated "the suite actually regressed" from "the harness is corrupting itself" was speed. Running the identical command (`python3 -m unittest discover -s tests`) outside the hook's environment gave 508 tests, all green, in 265 seconds. Inside the hook: 319 of 508 errors in 12 seconds. A suite that normally takes 265 seconds and instead fails in 12 is failing at setup or fixture-creation time across nearly every test class, not on real assertions. That timing mismatch alone is the tell, before reading a single traceback.

## What Didn't Work

- First instinct, on seeing "319 errors," was to suspect the branch's own recent changes had broken something real.
- Running `test_manifest` alone in isolation passed clean (29/29). That was the first signal this was not a real regression, a broken branch should have failed an isolated module too, but this check ran outside the hook's environment, so it did not yet explain the mismatch.
- The actual repro: manually reproducing the hook's exact environment by setting `GIT_DIR` to a throwaway `git init`'d temp directory and running `test_gitread` and `test_verify` directly, bypassing the hook entirely. That alone reproduced the exact failure signature, `git -C <tmp> commit` raising `CalledProcessError`, "returned non-zero exit status 1", in under a second, isolating the cause to the environment variable itself rather than to hook logic or suite code.
- A first fix attempt added `unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_PREFIX` at the top of the hook. Because this session worked from an isolated git worktree and could not write directly into the shared checkout's `.git/hooks/pre-push`, the fix was pushed for verification via a one-shot `git -c core.hooksPath=<path>` override rather than editing the hook file in place. That push still failed, with the same signature (322 errors that time). A throwaway diagnostic hook that does nothing but `env | grep '^GIT_' > <file>; exit 1` (dump-and-abort, before running anything real) showed `GIT_CONFIG_PARAMETERS` still present in the hook's environment, and it was carrying the very `-c core.hooksPath=...` override used to install the diagnostic hook itself. `GIT_CONFIG_PARAMETERS` is git's mechanism for propagating `-c` overrides into every subprocess git spawns, so it re-injected the `hooksPath` override into the fixtures' own `git -C <tmp> commit` calls too, a second, more surprising leak channel than `GIT_DIR` alone, and one the first fix attempt missed entirely.

## Solution

Add one line as the first substantive line of the `pre-push` hook, before running the suite:

```sh
unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_PREFIX GIT_CONFIG_PARAMETERS
```

then run the suite exactly as before. This was verified working: a push through the corrected hook (installed the same way, via a one-shot `core.hooksPath` override, since this session could not write into the shared checkout's real hooks directory from inside a worktree) completed with the suite passing and the branch landing on the remote correctly.

Recovery from the corruption itself needed nothing destructive: `git reset --mixed <last-good-sha>` restored the branch ref with zero data loss, because only the ref had moved. The stray "initial" commits never touched the working tree, so nothing there needed repair.

## Why This Works

`GIT_DIR`, `GIT_WORK_TREE`, `GIT_INDEX_FILE`, and `GIT_PREFIX` are the standard git environment variables that let git operate on a repository it is not currently sitting inside. `GIT_DIR` is confirmed set for a hook whose triggering push came from a linked worktree, so the hook script itself can find the repository it is gating without depending on its own current directory; a push from the primary checkout carried none of the four in this session's own tests. Whichever of them git does set for a given invocation, a hook's git identity has no business leaking into work the hook merely orchestrates as a subprocess, here, an entire test suite that creates and manipulates dozens of its own independent git repositories as fixtures. Once unset, each fixture's `git -C <tmp>` calls behave exactly as their explicit `-C` flag says, because nothing in the environment is contending with it, regardless of which of the four git happened to set for this particular push.

`GIT_CONFIG_PARAMETERS` is a different, complementary leak channel: it is git's documented mechanism for passing `-c key=value` overrides down through subprocess chains, used for example so `git -c protocol.version=2 fetch` properly propagates through submodule operations. The same propagation applies to hooks and everything they spawn. An operator's own `-c core.hooksPath=...` override, needed here specifically because the session installing the fix could not write to the real hooks directory, was itself leaking into the fixture commits and re-triggering the same hook recursively inside them. Clearing it alongside the `GIT_DIR` family closes both channels at once.

## Prevention

- Any git hook that shells out to run a test suite, or any other subprocess tree, which itself creates or manipulates git repositories, must clear `GIT_DIR`, `GIT_WORK_TREE`, `GIT_INDEX_FILE`, `GIT_PREFIX`, and `GIT_CONFIG_PARAMETERS` first. Git hooks always run with these set, and they are not scoped to the hook's own git operations, they apply to every subprocess the hook spawns, transitively.
- A test suite that constructs and commits into its own temporary git repositories is only isolated from the real repository if the temp repos' git invocations cannot be redirected by anything in the ambient environment, both env vars (the `GIT_DIR` family) and, less obviously, any `-c` config the outer invocation carries (`GIT_CONFIG_PARAMETERS`). Building the child environment explicitly (a clean env plus only what is needed) rather than inheriting `os.environ` wholesale, as `tests/_repo.py:37` currently does with `commit_env = dict(os.environ)`, would be a second, complementary defense inside the test helper itself. The hook-level `unset` is sufficient on its own and is the fix verified this session; hardening the helper is a follow-up, not yet done.
- Diagnostic technique worth reusing: when a hook or CI gate produces a suspiciously fast failure with a suspiciously large error count, compare its wall-clock time against a known-good baseline run of the identical command outside the hook. A large speed mismatch (12 seconds against 265 here) is strong evidence of setup or import-time failure across many tests at once, not a real behavioral regression, and is faster to check than reading any individual traceback.
- Diagnostic technique for isolating an unknown environment leak into a hook: write a throwaway hook that does nothing but dump every `GIT_*` (or other suspect-prefix) variable from its own environment to a file and then abort (`exit 1`) before running anything real. This is faster and more certain than guessing which variable is responsible from documentation alone, and it is what caught the `GIT_CONFIG_PARAMETERS` leak that the first fix attempt missed.
- A self-test worth adding to the suite: a test that runs a trivial fixture-repo-creating test with `GIT_DIR` deliberately set in its own environment first, and asserts the fixture's "initial" commit did not land in the real repository. That would have caught this defect long before the first real push attempt, rather than after eight stray commits had already landed on the branch.
- A gate whose whole job is refusing broken changes has one job it must never fail at silently: staying inert on everything that is not the change it is judging. This is the third way Relay's own `pre-push` hook has been found doing more than that. The first two are documented in Related below; each was a different mechanism, and each was invisible to the suite until a live push hit it, because the hook itself is untracked and the suite's fixture repositories carry none of it.

## Related Issues

- `docs/solutions/logic-errors/push-inherited-the-read-sized-git-timeout-so-the-pre-push-gate-outran-it.md` documents a different failure of the same untracked hook: a shared read-sized subprocess timeout applied to `push` killed the runner's own push mid-hook once the suite grew past two minutes. That defect is about how long the hook is allowed to run; this one is about what the hook's own environment does to processes it merely orchestrates. Different root cause, same mechanism, same blind spot: the fixture suite creates repositories with no hooks and no ambient `GIT_DIR`, so neither defect could be found by the suite as it stood, only a live push against the real hook found either one.
- `docs/solutions/logic-errors/stubbed-seams-agree-by-construction-first-live-run-found-five-contract-defects.md` is the wider lesson both of the above descend from: a stub or fixture environment that was written by the same hands as what it stands in for agrees with it by construction, and proves nothing about the live case. That doc is about message contracts between Relay's own processes; this one and the timeout doc are both about an environmental fact, an untracked hook running real work, that no fixture in this repository contains.
- `CONCEPTS.md`'s **External gate** entry already distinguishes a pre-push hook (whose whole runtime is charged to the Runner's own push) from CI (which costs the Runner nothing). This defect is a further cost of the pre-push form specifically: its environment is shared with everything it invokes, in a way CI's separate job environment is not.
