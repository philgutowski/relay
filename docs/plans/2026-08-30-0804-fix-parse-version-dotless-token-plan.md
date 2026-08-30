---
artifact_contract: ce-unified-plan/v1
artifact_readiness: implementation-ready
product_contract_source: ce-plan-bootstrap
created: 2026-08-30
depth: lightweight
---

# `parse_version` must not treat a bare digit in an update banner as a version

## Problem Frame

`_parse_leading_digit` (`skills/relay/scripts/relay/backends/__init__.py:77`) matches
`_VERSION_TOKEN_RE` (`^(\d[\w.\-]*)`) against stripped text and returns whatever it
matches. Claude's `parse_version` is this function directly (`claude.py:10`). Codex's and
Grok's `parse_version` is `_parse_after_name_token` (`__init__.py:88`), which splits off the
first whitespace token (the CLI name echoed at the start of `--version` output, e.g. `grok`
or `codex-cli`) and calls `_parse_leading_digit` on the remainder.

Both paths accept any leading digit run as a version, with no requirement that it look like
a dotted version number. `launch.cli_version` (`launch.py:95`) calls this once per run to
record the observed CLI version, comparing it against `contracts.BACKEND_PINS[...]
["version_tested"]` to surface drift. A Codex or Grok update banner ahead of, or instead of,
the real version line — e.g. `grok 3 updates available` — parses to `"3"` after the name
token is skipped, since `"3 updates available"` leads with a digit. `cli_version` then
records `"3"` as the observed version instead of failing closed to `None`. Because `"3"`
does not equal any `version_tested` pin, this happens to still surface as a drift signal
today, but it is drift against a fabricated value, not the real one — a coincidental safety
net, not a correct read. A banner shaped so its leading digits do coincide with a pin (or a
downstream comparison keyed on truthiness rather than equality) would hide real drift, which
is the failure mode Origin U5's observed-version field exists to catch.

Evidence: `skills/relay/scripts/relay/backends/__init__.py:96`
(`return _parse_leading_digit(parts[1])`).

## Scope

**In scope:**
- `_parse_leading_digit` (`__init__.py:77`): after the existing leading-digit match, require
  the matched token to contain a `.` before returning it; return `None` otherwise. This is
  the single shared match point both `claude.py`'s direct `parse_version` and
  `_parse_after_name_token`'s (codex, grok) delegated call run through, so one change covers
  all three backends.
- `tests/test_backends.py`: unit tests on `_parse_after_name_token` / the grok and codex
  `parse_version` callables for the dotless-token banner case, including the literal
  `grok 3 updates available` case named by the task.
- `tests/test_launch.py`: a `cli_version`-level test with a multi-line update banner for a
  name-prefixed backend (grok or codex), mirroring the existing claude-only
  `test_a_leading_non_version_token_returns_none_rather_than_the_wrong_word`, which today has
  no codex/grok counterpart.

**Out of scope:**
- `_VERSION_TOKEN_RE` itself. The regex correctly finds the leading digit run; the gap is
  that nothing after the match checks it looks like a version. Changing the regex to try to
  express "must contain a dot" inline is not necessary when a plain post-match check reads
  clearly and matches the task's suggested fix.
- `contracts.BACKEND_PINS` / `version_tested` values. All three (`2.1.250`, `0.149.0`,
  `1.0.5`) already contain a dot, so the dot requirement introduces no regression against any
  pinned value (verified below).
- `launch.cli_version`'s drift-comparison behavior once it has a parsed value (equality
  check against `version_tested`). Not implicated; the defect is upstream of it, in what
  counts as "parsed" at all.
- Any change to how Codex or Grok's real `--version` output is shaped. This fixes parsing
  only.

## Current State (research)

- `__init__.py:26-27` — the module comment already documents the shared-token intent:
  "Same leading digit token Claude's parse_version uses. Codex and Grok skip the first name
  token and apply it to the rest."
- `__init__.py:77-85` (`_parse_leading_digit`) — `None`/empty guards, then
  `_VERSION_TOKEN_RE.match(stripped)`, returns `match.group(1)` or `None`. No dot check
  today.
- `__init__.py:88-98` (`_parse_after_name_token`) — `None`/empty guards, `stripped.split(None,
  1)`, requires at least two whitespace-separated parts, then delegates to
  `_parse_leading_digit(parts[1])`. This is where the bug reaches Codex and Grok: `parts[1]`
  for `"grok 3 updates available"` is `"3 updates available"`, which leads with a bare digit.
- `claude.py:10` — `parse_version = _parse_leading_digit` (direct, no name-token skip).
- `codex.py:11`, `grok.py:12` — `parse_version = _parse_after_name_token`.
- `contracts.py:124,152,189` — `version_tested` pins: claude `2.1.250`, codex `0.149.0`,
  grok `1.0.5`. All three contain a `.`, confirming the suggested fix's premise.
- `launch.py:95-112` (`cli_version`) — runs `<binary> --version`, fails closed to `None` on
  any exception, otherwise calls `module.parse_version(proc.stdout)` for the manifest's
  configured `backend`.
- `tests/test_backends.py:135-145` (`SharedSurface`) — `test_parse_version_reads_each_observed_sample`
  (asserts each backend's `version_output_sample` still parses to its `version_tested`, the
  regression guard for this change) and `test_parse_version_returns_none_rather_than_raising`
  (empty string and `"update available"` — the latter already returns `None` today for every
  backend: for codex/grok, `_parse_after_name_token` splits it into two parts fine (`"update"`,
  `"available"`), but `_parse_leading_digit("available")` never matches a leading digit, the
  same failure mode as the claude direct-path case; neither existing case exercises the actual
  bug, which needs a digit-leading remainder after a real name token is skipped).
- `tests/test_launch.py:335-338` (`test_a_leading_non_version_token_returns_none_rather_than_the_wrong_word`)
  — the existing claude-only multiline-banner regression test, at the `cli_version` level.
  No codex/grok equivalent exists yet.

## Design

Add a dot check to `_parse_leading_digit`, immediately after the regex match, before
returning:

```python
def _parse_leading_digit(text):
    """A version only when stripped stdout leads with a digit and the matched token
    looks like a dotted version (never a bare update-banner digit). Never raises."""
    if text is None:
        return None
    stripped = str(text).strip()
    if not stripped:
        return None
    match = _VERSION_TOKEN_RE.match(stripped)
    if not match:
        return None
    token = match.group(1)
    return token if "." in token else None
```

This is the single point both call paths run through (`claude.py`'s direct assignment and
`_parse_after_name_token`'s delegation for codex/grok), so the fix is one change, not three.

Trace against the task's two named cases:
- `"grok 3 updates available"` → `_parse_after_name_token` splits to `parts[1] = "3 updates
  available"` → `_parse_leading_digit` matches token `"3"` → no dot → `None`. Matches the
  task's expected outcome.
- The launch.py multiline update banner (codex/grok shape: name-prefixed line, banner text,
  then the real version line) → same path, still `None` for the banner reading; the plan does
  not change `cli_version`'s single-call, first-`--version`-invocation behavior, only what a
  banner-shaped string parses to.

Trace against the three pinned samples to confirm no regression:
- `"2.1.250 (Claude Code)"` (claude, direct) → token `"2.1.250"` → contains `.` → returns
  `"2.1.250"`. Unchanged.
- `"codex-cli 0.149.0"` (codex, after name token) → `parts[1] = "0.149.0"` → token
  `"0.149.0"` → contains `.` → returns `"0.149.0"`. Unchanged.
- `"grok 1.0.5 (5115b46bc909) [stable]"` (grok, after name token) → `parts[1] = "1.0.5
  (5115b46bc909) [stable]"` → token `"1.0.5"` → contains `.` → returns `"1.0.5"`. Unchanged.

## Test Scenarios

`tests/test_backends.py` (`SharedSurface` or a new small case class alongside it):

1. **`grok 3 updates available` returns `None`** — the literal case named by the task:
   `backends.build("grok").parse_version("grok 3 updates available")` is `None`.
2. **A codex-shaped dotless update banner returns `None`** — e.g.
   `backends.build("codex").parse_version("codex-cli 5 new updates")` is `None`, confirming
   the fix is not grok-specific (both go through `_parse_after_name_token`).
3. **A dotless leading digit with no name token still returns `None` on the direct path** —
   `backends.build("claude").parse_version("3 updates available")` is `None`, covering
   `_parse_leading_digit` directly (claude never skips a name token, so this is the direct
   call path's own dotless case).
4. Extend the existing `test_parse_version_reads_each_observed_sample` regression loop
   (`test_backends.py:135`) needs no change — it already iterates all three backends' real
   `version_output_sample` values, which still parse correctly per the Design trace above;
   running it after the fix is the regression guard, not a new test.

`tests/test_launch.py` (`CliVersion`):

5. **The launch.py multiline update banner, codex or grok** — mirror
   `test_a_leading_non_version_token_returns_none_rather_than_the_wrong_word`
   (`test_launch.py:335`) for a name-prefixed backend, e.g.:
   ```python
   fake = lambda *a, **k: _FakeCompletedProcess(
       "grok 3 updates available\n1.0.5 (5115b46bc909) [stable]\n", 0)
   self.assertIsNone(launch.cli_version({}, run=fake, backend="grok"))
   ```
   This is the multiline case: the real version line is present on a second line, but
   `cli_version` reads the whole captured stdout as one string and `parse_version` must not
   pick up the banner's bare digit from the first line before ever reaching the real version
   line.

## Files

- `skills/relay/scripts/relay/backends/__init__.py` — add the dot check to
  `_parse_leading_digit` (around line 77-85).
- `tests/test_backends.py` — new dotless-banner test cases per Test Scenarios 1-3.
- `tests/test_launch.py` — new multiline-banner `CliVersion` case per Test Scenario 5.

## Risks

- Low. The change only narrows what counts as a parsed version (a strict subset of what
  parses today), and the Design section traces all three pinned `version_output_sample`
  values through the new check to confirm none of them regress.
- A future CLI whose real version output is genuinely dotless (a bare integer version) would
  now fail closed to `None` for that backend. Given all three current pins are dotted and the
  task's own rationale is "all three `version_tested` pins are dotted," this is an accepted
  tradeoff, not a new risk introduced silently — `cli_version` already fails closed to `None`
  on several other conditions (missing binary, timeout, non-zero exit, decode error), and a
  dotless real version would join that list until a backend is added that needs otherwise.
- The dot check narrows the false-positive surface but does not close it: an update banner
  whose leading digits happen to be decimal-shaped (e.g. `grok 1.2 major updates available`)
  still contains a dot and would still be returned as the observed version, reproducing the
  same coincidental-drift failure this plan closes for the bare-digit case. No such banner
  shape has been observed from Codex or Grok; tightening the check to require a full
  `\d+\.\d+` version shape is deferred rather than built speculatively against an unobserved
  banner format.

## Verification

Run the full suite from the repo root: `python3 -m unittest discover -s tests`. Confirm the
new `test_backends.py` and `test_launch.py` cases pass and every existing `SharedSurface` and
`CliVersion` test still passes unchanged (the added check is a strict narrowing that the
Design trace confirms does not affect any currently-passing sample).
