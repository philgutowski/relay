---
artifact_contract: ce-unified-plan/v1
artifact_readiness: implementation-ready
product_contract_source: ce-plan-bootstrap
created: 2026-08-27
depth: lightweight
---

# Classify digest keys contract

## Problem Frame

`classify.classify()` builds a dict (the "digest") that `run.py` and `closeout.py` each read
with separate string-keyed lookups (`digest.get("...")`). Nothing asserts that the keys either
reader module reads are keys `classify.py` actually sets. A future rename or removal in
`classify.py` would silently break a reader with no test failure pointing at the cause.

## Scope

**In scope:**
- Add `DIGEST_KEYS`, the set of top-level digest keys `classify.classify()` guarantees, to
  `skills/relay/scripts/relay/contracts.py`, near `HALT_LINES`.
- Add a test that fails if:
  1. `run.py` or `closeout.py` reads (via `.get(...)` on a digest/`ctx.digest` object) a
     top-level key not in `DIGEST_KEYS`.
  2. `classify.classify()` stops setting a key that `run.py` or `closeout.py` reads.
- Guard only. No runtime behavior changes anywhere.

**Out of scope:**
- Nested keys inside `envelope` (`blockers`, `plan_path`, etc.) — those come from
  `classify.parse_envelope()`, a different, already-nested contract. Only the top-level digest
  dict returned by `classify.classify()` is in scope.
- `verify.py`, `summary.py`, or any other digest reader — the task names only `run.py` and
  `closeout.py`.
- Changing `classify.classify()`'s actual keys.

## Assumptions

- "Reads a key" means a `.get("<key>")` (or `["<key>"]`) call on the object that is unambiguously
  the digest / `ctx.digest` in `run.py` and `closeout.py`. Static source inspection (regex over
  the two files) is sufficient; no need to execute the modules.
- The test lives in `tests/test_contracts.py`, alongside the existing pin/contract checks
  (`PLUGIN_PINS`, `HALT_LINES` completeness, etc.), rather than in `test_run.py` or
  `test_closeout.py`, since it is a contract test about all three modules, not a behavior test
  of one.

## Current State (research)

`classify.classify()` (`skills/relay/scripts/relay/classify.py:170-276`) returns a dict with
these top-level keys, set unconditionally in the function body:

```
transcript_path, transcript_present, exit_code, timed_out, line_count, malformed_lines,
tool_calls, findings, envelope, last_message, last_message_tail, halt_class, routable
```

`run.py` reads these top-level digest keys via `digest.get(...)`:
```
routable, halt_class, findings, envelope, last_message
```
(`skills/relay/scripts/relay/run.py:94,99,286,305,313,320,471,477,497`)

`closeout.py` reads these top-level digest keys via `(digest or {}).get(...)` /
`closeout_digest.get(...)`:
```
findings, envelope, last_message_tail, last_message
```
(`skills/relay/scripts/relay/closeout.py:72,73,104,114,153,170,173,254,255,256,261`)

So the reader-required subset is `{routable, halt_class, findings, envelope, last_message,
last_message_tail}`, all already produced by `classify.classify()`. `contracts.py` already
holds `HALT_LINES`, a dict-shaped contract with its own completeness test pattern in
`tests/test_contracts.py` to follow.

## Design

### 1. `contracts.py` — add `DIGEST_KEYS`

Add near `HALT_LINES` (after the halt-class block, same section):

```python
# The digest classify.classify() (U7) guarantees, read by run.py and closeout.py via
# digest.get(...). tests/test_contracts.py asserts both readers stay inside this set and that
# classify keeps setting every key either reader uses.
DIGEST_KEYS = frozenset((
    "transcript_path",
    "transcript_present",
    "exit_code",
    "timed_out",
    "line_count",
    "malformed_lines",
    "tool_calls",
    "findings",
    "envelope",
    "last_message",
    "last_message_tail",
    "halt_class",
    "routable",
))
```

No changes to `classify.py`, `run.py`, or `closeout.py` — this is additive only.

### 2. `tests/test_contracts.py` — add the guard test

Add a test function (mirroring the file's existing static-inspection style for `PLUGIN_PINS`)
that:

1. Reads `skills/relay/scripts/relay/run.py` and `skills/relay/scripts/relay/closeout.py` as
   source text.
2. Extracts every top-level digest key read, by regexing for `digest.get("KEY"` /
   `digest.get('KEY'` / `ctx.digest.get("KEY"` and similar patterns bound to a `digest`-named
   variable (`digest`, `ctx.digest`, `closeout_digest`) — not `envelope.get(...)`, which is a
   nested lookup out of scope.
3. Asserts every extracted key is a member of `contracts.DIGEST_KEYS` (guards "reads a key not
   in the set").
4. Asserts every key in `contracts.DIGEST_KEYS` that is read by at least one of the two modules
   is present in `classify.classify()`'s actual returned dict — call `classify.classify()` with
   a minimal/empty transcript and a stub launch result, and assert the reader-required keys are
   all present in `result` (guards "classify stops setting a key either module reads").

Directional sketch (illustrative, not literal code):

```python
import re
from skills.relay.scripts.relay import contracts, classify

READER_DIGEST_GET_RE = re.compile(
    r"\b(?:digest|ctx\.digest|closeout_digest)\s*\.get\(\s*[\"']([A-Za-z_]+)[\"']"
)

def _digest_keys_read(source_path):
    with open(source_path, encoding="utf-8") as handle:
        source = handle.read()
    return set(READER_DIGEST_GET_RE.findall(source))

class DigestKeysContractTests(unittest.TestCase):
    def test_readers_stay_inside_digest_keys(self):
        run_keys = _digest_keys_read(RUN_PY_PATH)
        closeout_keys = _digest_keys_read(CLOSEOUT_PY_PATH)
        unknown = (run_keys | closeout_keys) - contracts.DIGEST_KEYS
        self.assertFalse(unknown, f"reader(s) use digest key(s) not in DIGEST_KEYS: {unknown}")

    def test_classify_sets_every_key_readers_use(self):
        run_keys = _digest_keys_read(RUN_PY_PATH)
        closeout_keys = _digest_keys_read(CLOSEOUT_PY_PATH)
        required = (run_keys | closeout_keys) & contracts.DIGEST_KEYS
        result = classify.classify("/nonexistent/path", _StubLaunchResult())
        missing = required - set(result)
        self.assertFalse(missing, f"classify() no longer sets key(s) readers depend on: {missing}")
```

Use a small stub/namedtuple for `launch_result` with `timed_out=False` and `exit_code=None`,
matching the pattern `classify.classify()` already expects (see `classify.py:173-174`). Reuse
an existing stub from `tests/test_classify.py` if one already fits, rather than inventing a
second one — check that file first during implementation.

**Path resolution:** resolve `RUN_PY_PATH` / `CLOSEOUT_PY_PATH` the same way the rest of
`tests/test_contracts.py` resolves the plugin/repo root (check existing constants at the top of
that file, e.g. a `REPO_ROOT` or similar, before adding a new one).

## Test Scenarios

1. **`test_readers_stay_inside_digest_keys`** — passes today because every current `.get(...)`
   call in `run.py`/`closeout.py` on a digest-named variable is already inside `DIGEST_KEYS`.
2. **`test_classify_sets_every_key_readers_use`** — passes today because `classify.classify()`
   already sets `routable`, `halt_class`, `findings`, `envelope`, `last_message`,
   `last_message_tail` unconditionally.
3. **Regression coverage (manual verification during implementation, not new test code):**
   temporarily rename a key `run.py` reads (e.g. `halt_class` → `halt_klass`) in a scratch edit
   and confirm test 1 fails; revert. Temporarily remove a key from `classify.classify()`'s
   returned dict that a reader uses and confirm test 2 fails; revert. This is a design
   self-check, not a permanent test — do not commit the temporary breakage.
4. **No existing test in `test_run.py`, `test_closeout.py`, or `test_classify.py` changes.**

## Files

- `skills/relay/scripts/relay/contracts.py` — add `DIGEST_KEYS` near `HALT_LINES`.
- `tests/test_contracts.py` — add the two guard tests above (or one combined test class).

## Risks

- **False positives from the regex:** if `run.py`/`closeout.py` later reads a digest key via a
  different variable name or indirection (`digest["key"]`, a helper function taking `digest` as
  a parameter under a different local name), the regex misses it and the guard silently doesn't
  cover it. Mitigation: keep the regex scoped to the actual patterns in use today (verified
  above); note in a test docstring/comment that the regex is pattern-based, not a full AST
  walk, so a new access style needs a matching regex update.
- **Scope creep:** it would be tempting to also cover `envelope`'s nested keys or extend the
  guard to `verify.py`/`summary.py`. Explicitly out of scope per the task; do not add.

## Verification

Run the full suite from repo root: `python3 -m unittest discover -s tests`. Confirm: same test
count as before plus the new test(s), and every existing test in `test_run.py`,
`test_closeout.py`, `test_classify.py` still passes unchanged.
