---
artifact_contract: ce-unified-plan/v1
artifact_readiness: implementation-ready
product_contract_source: ce-plan-bootstrap
created: 2026-08-27
depth: lightweight
---

# Widen `CLAUDE_DIR_SCAN_REGEX` to catch markdown link, bold, italic, and list-marker forms

## Problem Frame

`CLAUDE_DIR_SCAN_REGEX` (`skills/relay/scripts/relay/contracts.py:85`) is the pre-flight
detector for a path under the agent config directory (leading dot, the word `claude`, a
slash) appearing in a task's title, description, or brief. Its one production consumer,
`brief.py:147` (`_paths_in`, called from `scan`), routes a hit to excluding the task from
the run entirely: under Relay's `dontAsk` permission mode, an Edit or Write under that
directory is denied regardless of the allowlist, so a task whose card names such a path
cannot finish unattended (`docs/solutions/workflow-issues/headless-dontask-blocks-claude-dir-edits.md`).

The regex's leading character class, `[\s"'`(/]`, admits the prefix only when preceded by
start of line, whitespace, a double quote, a single quote, a backtick, an open paren, or a
slash. It omits `[` and `*`. Tracker cards commonly write such a path inside a markdown
link (`[...](.claude/skills/x/SKILL.md)` or `[.claude/skills/x/SKILL.md](...)`), in bold
(`**.claude/skills/x/SKILL.md**`), or in italic (`*.claude/skills/x/SKILL.md*`) — none of
those forms is caught today when the character immediately preceding the prefix is `[` or
`*` rather than one of the six already covered. A task written that way launches anyway
and is caught only later by the post-run branch-diff backstop (`gitwrite.py:251`), after a
whole task process has already been spent — the exact "found out in the last second
instead of the first" failure the solutions doc names as the cost of a missed pre-flight
hit.

## Scope

**In scope:**
- `CLAUDE_DIR_SCAN_REGEX` (`contracts.py:85`): widen the leading character class to also
  match `[` and `*`, so a markdown link, bold, or italic wrapper immediately preceding the
  prefix is caught. (List-marker forms with a space before the path, e.g. `- .claude/x`,
  already match today via `\s`; a marker glued directly to the path with no space, e.g.
  `*.claude/x`, is the same character as the italic case and is covered by the same `*`
  addition.)
- `tests/test_contracts.py`: new tests pinning the regex directly — the newly matching
  forms (link, bold, italic), a representative set of forms that already matched (to guard
  against a future edit narrowing the class back), and the non-matching suffix case (a bare
  word immediately before the prefix, e.g. `myclaude/x` — no, precisely: a word character
  directly touching the leading dot, e.g. `x.claude/` where `x` is part of a longer token —
  must keep not matching).
- `docs/solutions/workflow-issues/headless-dontask-blocks-claude-dir-edits.md`: amend the
  existing entry to record the widened forms. No new solutions doc.

**Out of scope:**
- `brief.py` beyond whatever behavior the wider regex produces on its own. `_paths_in` and
  `scan` (`brief.py:144-165`) already iterate every regex hit and extend each to
  `PATH_TAIL_STOP`; a wider regex simply produces more starting matches through the same
  unchanged extraction path. No code change there.
- `PATH_TAIL_STOP` (`brief.py:69`), the trailing-character stop set. The task is about the
  leading edge only.
- The post-run branch-diff backstop (`gitwrite.py:251`). It filters on changed file paths,
  not on file contents, so it is unaffected by and independent of this regex; it stays the
  catch-all for whatever the pre-flight scan still misses.
- The literal `.claude/` prefix must not appear anywhere in the new test file's source as a
  written-out literal wrapped exactly the way this task's own card describes it — this is
  already how the existing `Scan` test class in `tests/test_brief.py` builds its fixtures
  (as plain Python string literals assembled at test time), and this plan's new
  `test_contracts.py` tests follow the identical pattern. This is a repo-hygiene note, not a
  functional requirement; nothing enforces it beyond consistency with the pattern already in
  use in `test_brief.py`.

## Current State (research)

- `contracts.py:85` — `CLAUDE_DIR_SCAN_REGEX = re.compile(r"(^|[\s\"'\`(/])\.claude/",
  re.MULTILINE)`. The comment above it (`contracts.py:84`) already frames this as "the
  pre-flight scan form from the solutions doc: catches the path inside prose and quotes."
- `contracts.py:83` — `CLAUDE_DIR_PATH_REGEX = re.compile(r"(^|/)\.claude/")`, a second,
  narrower regex used elsewhere (not the scan consumer at `brief.py:147`); out of scope,
  not named by the task.
- `brief.py:144-153` (`_paths_in`) — for each `CLAUDE_DIR_SCAN_REGEX` match, computes
  `start = match.end() - len(".claude/")` (i.e., re-anchors to the start of the literal
  `.claude/` regardless of which leading character matched) and walks forward to the first
  `PATH_TAIL_STOP` character, so the extracted path text is identical no matter which
  character in the leading class triggered the match. Widening the leading class changes
  only which occurrences are found, not how a found occurrence is turned into a path
  string.
- `brief.py:156-165` (`scan`) — calls `_paths_in` over title, description, and brief text;
  unaffected.
- `docs/solutions/workflow-issues/headless-dontask-blocks-claude-dir-edits.md:118` — the
  `preflight_claude_dir.sh` shell example's grep pattern,
  `'(^|[[:space:]"'"'"'`(/])\.claude/'`, mirrors the same leading class in POSIX bracket
  form and should be widened in the same edit for consistency, since the doc presents it as
  the reference form of the same check.
- `tests/test_brief.py:195-207` (`test_every_path_form_from_the_solutions_doc_is_caught`) —
  the existing form-coverage test, all six already-matching forms (bare, double-quoted,
  backtick, paren, mid-path slash, start-of-line). This plan does not touch this test; the
  new coverage for the widened forms and the non-matching suffix case belongs in
  `tests/test_contracts.py` because the task asks for tests pinning the regex, not the
  `brief.scan` integration, and `test_contracts.py` currently has no `CLAUDE_DIR_SCAN_REGEX`
  coverage at all (confirmed: no match for `CLAUDE_DIR` in that file).
- `gitwrite.py:251` — the branch-diff backstop the task cites as filtering on changed paths,
  not file contents; confirms the out-of-scope call above is correct (this fix cannot affect
  it either way).

## Design

Widen the leading character class from `[\s"'`(/]` to `[\s"'`(/\[*]`, adding an escaped
open square bracket and an asterisk:

```python
CLAUDE_DIR_SCAN_REGEX = re.compile(r"(^|[\s\"'`(/\[*])\.claude/", re.MULTILINE)
```

Why these two characters cover the four named forms with nothing narrower or broader:
- **Markdown link**, either the display text (`[.claude/x](url)`, char before the prefix is
  `[`) or the target when it's the visible form (a target inside parens already matches via
  the existing `(`) — the new `[` closes the display-text case.
- **Bold** (`**.claude/x**`) and **italic** (`*.claude/x*`) both have the prefix immediately
  preceded by `*` (the second `*` of a bold delimiter is still just a `*` from the regex's
  point of view, since the class matches one character and `_paths_in` re-anchors to the
  literal `.claude/` regardless).
- **List marker**: a marker followed by a space (`- .claude/x`, `* .claude/x`) already
  matches today via `\s`; a marker with no space is indistinguishable from the italic case
  and is caught by the same new `*`.
- **Suffix-of-a-longer-token stays excluded**: the class still contains no word character
  (`\w`), so `x.claude/y` (a bare word glued directly to the prefix) still does not match —
  neither `[` nor `*` changes that.

No change to `brief.py`, `PATH_TAIL_STOP`, or `gitwrite.py`.

## Test Scenarios

All in `tests/test_contracts.py`, exercising `contracts.CLAUDE_DIR_SCAN_REGEX` directly
(`.search(text)` / `.finditer(text)`), building each literal path string at test time by
concatenation or an f-string rather than writing the bare `.claude/` token as a top-level
module string, matching the existing pattern in `tests/test_brief.py`'s `Scan` class:

1. **Newly matching — markdown link display text**: a string built as
   `"[" + ".claude/skills/x/SKILL.md" + "](docs/x.md)"` matches.
2. **Newly matching — bold**: `"**" + ".claude/settings.json" + "**"` matches.
3. **Newly matching — italic**: `"*" + ".claude/settings.json" + "*"` matches.
4. **Newly matching — list marker glued to the path, no space**: `"*" +
   ".claude/hooks/pre.sh"` (i.e., the same character as case 3, confirming the marker case
   needs no separate mechanism) matches.
5. **Already matching, still matching (regression guard)** — one assertion per existing
   form from `contracts.py:84`'s comment and `test_brief.py`'s
   `test_every_path_form_from_the_solutions_doc_is_caught`: start of line, preceded by a
   space, a double quote, a single quote, a backtick, an open paren, and a mid-path slash.
6. **Non-matching — suffix of a longer token**: a string built as `"x" +
   ".claude/skills/y/SKILL.md"` (a bare word character directly touching the leading dot)
   does not match.
7. **Non-matching — the bare word `claude` with no path** (existing behavior, already
   covered at the `brief.scan` level by `test_brief.py`'s
   `test_the_word_claude_without_a_path_is_not_a_hit`; add the same assertion directly
   against the regex here for symmetry with the rest of this test class, since this class is
   the first one to test the regex in isolation): `"claude and check the output"` does not
   match.

## Files

- `skills/relay/scripts/relay/contracts.py` — widen `CLAUDE_DIR_SCAN_REGEX`'s leading
  character class (line 85).
- `tests/test_contracts.py` — new test class covering the regex directly per Test Scenarios
  above.
- `docs/solutions/workflow-issues/headless-dontask-blocks-claude-dir-edits.md` — amend to
  record the widened forms: update the `preflight_claude_dir.sh` example's grep pattern
  (line 118) to the widened POSIX-bracket equivalent, and add the markdown
  link/bold/italic/list-marker forms to the guidance the doc already gives about which forms
  the scan catches. Amend in place; do not open a new solutions doc.

## Risks

- Purely additive to the character class: every string the old regex matched, the new one
  still matches (superset), so no existing `test_brief.py` or `test_contracts.py` coverage
  can regress from this change alone.
- False-positive surface grows slightly (e.g., a stray `*` before an unrelated `.claude/`-looking
  substring now triggers a hit), which the solutions doc already frames as the accepted,
  cheaper failure mode: "a false positive costs one attended run, a false negative costs a
  full run that halts unmergeable at the end."
- The `preflight_claude_dir.sh` shell example in the solutions doc is illustrative
  documentation, not executable production code in this repo (no test runs it); widening it
  keeps the doc consistent with the real regex but carries no test-suite risk if the POSIX
  bracket-class translation has a small syntax slip. Double-check the shell escaping by eye
  against the Python class it mirrors before committing the doc edit.

## Verification

Run the full suite from the repo root: `python3 -m unittest discover -s tests`. Confirm the
new `test_contracts.py` class passes and every existing `Scan` test in `tests/test_brief.py`
still passes unchanged (the widened regex is a superset, so no existing assertion should
need to change).
