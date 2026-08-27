---
title: A backslash before `[` inside a POSIX ERE bracket expression is not an escape, it is a second literal member
date: 2026-08-27
category: logic-errors
module: runner
problem_type: logic_error
component: runner
severity: low
symptoms:
  - "the pre-flight grep in docs/solutions/workflow-issues/headless-dontask-blocks-claude-dir-edits.md used the bracket expression `[[:space:]\"'\\`(/\\[*]`, written on the assumption that `\\[` escapes the literal `[` the pattern needed to add"
  - "ce-code-review flagged the pattern before merge: inside a POSIX ERE bracket expression `\\[` is parsed as two literal class members, a backslash and a `[`, not one escaped `[`, so the pattern also matched a stray backslash character the widened CLAUDE_DIR_SCAN_REGEX (a Python `re` pattern, where `\\[` is a real escape) never matches"
root_cause: missing_validation
resolution_type: documentation_update
tags: [posix-ere, bracket-expression, grep, regex-escaping, shell]
---

# A backslash before `[` inside a POSIX ERE bracket expression is not an escape, it is a second literal member

## Problem

While widening the pre-flight `grep -E` example in
`docs/solutions/workflow-issues/headless-dontask-blocks-claude-dir-edits.md` to also catch a markdown
link's leading `[`, the character was added as `\[` inside the bracket expression, carrying over the
escaping habit from Python's `re` module (the actual `CLAUDE_DIR_SCAN_REGEX` in
`skills/relay/scripts/relay/contracts.py`, where `\[` is a real, necessary escape). In a POSIX ERE
bracket expression, that same two characters are not an escape at all.

## Symptoms

- The shell example's bracket expression read `[[:space:]"'` (/\[*]`, adding a backslash before the
  new `[` member.
- `ce-code-review` caught it before merge: a POSIX bracket expression has no escape mechanism, so
  `\[` inside `[...]` is two literal class members, a literal backslash and a literal `[`, not an
  escaped `[`. The shell pattern therefore also matched a bare backslash character that the Python
  regex it was meant to mirror does not match.

## What Didn't Work

- Writing `\[` inside the bracket expression on the assumption that backslash-escaping works the same
  way inside `[...]` as it does outside it, or the same way it does in Python `re` syntax.

## Solution

Drop the backslash. Inside a bracket expression, `[` needs no escaping to be treated literally (a
literal `]` is the one member with a placement rule: first in the list, immediately after the
opening `[` or `[^`). The corrected member list is `[[:space:]"'` (/[*]`, fixed in commit `b24be21`.

## Why This Works

POSIX bracket expressions (`[...]`) are a closed character class with their own grammar, separate
from the ERE metacharacter grammar the rest of the pattern uses. Metacharacters lose their special
meaning inside `[...]` and so need no escaping there, backslash included: a backslash inside a
bracket expression is just the literal backslash character, not an escape introducer. This differs
from Python's `re`, where `\[` inside a character class is still parsed as an escaped literal `[`
(harmless there, but not equivalent syntax). Carrying a `re`-flavored escaping habit into a shell
`grep -E` bracket expression silently changes what the class matches.

## Prevention

- When writing or reviewing a `grep -E` (or any POSIX ERE) bracket expression, treat every character
  between `[` and `]` as literal by default; add a character with no leading backslash unless it is
  `]`, `^`, or `-` in a position where the grammar gives them special meaning.
- When a shell regex is deliberately written to mirror a source-language regex (as this pre-flight
  check mirrors `CLAUDE_DIR_SCAN_REGEX`), do not port escaping syntax across languages by habit; verify
  the two engines' bracket-expression rules independently.
- Run the pattern against a positive and a negative fixture before landing it, the way
  `tests/test_contracts.py`'s `ClaudeDirScanRegex` class pins the Python regex; a shell example with no
  test coverage of its own depends on review to catch this class of mistake.

## Related Issues

- `docs/solutions/workflow-issues/headless-dontask-blocks-claude-dir-edits.md`: the pre-flight script
  this bracket expression belongs to, widened in the same change.
