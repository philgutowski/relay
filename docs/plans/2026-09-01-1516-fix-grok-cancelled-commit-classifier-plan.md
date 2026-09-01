---
title: Grok Cancelled Commit Classifier, Brief, and Pin - Plan
type: fix
date: 2026-09-01
artifact_contract: ce-unified-plan/v1
artifact_readiness: implementation-ready
product_contract_source: ce-plan-bootstrap
execution: code
---

# Grok Cancelled Commit Classifier, Brief, and Pin - Plan

## Goal Capsule

- **Objective:** a halted Grok Task's Cause line names the real reason (grok's own permission
  layer cancelled a tool call mid-commit) instead of a generic "exited without a return envelope"
  or "left the tree dirty," so an operator reading the tracker card or the run summary does not
  have to open the raw log to learn what actually happened.
- **Means:** classify.py reads a second marker in grok's existing `updates.jsonl` denial scan and
  attaches a finding naming the cancelled command (KTD1); the grok brief tells the task to avoid
  the command shape that triggers the cancellation (KTD4); `BACKEND_PINS["grok"]` records the
  caveat for the next reader (KTD5).
- **Authority hierarchy:** this plan's Requirements and KTDs bind; `docs/solutions/` learnings
  cited below are precedent, not owners, where this plan departs from them.
- **Stop conditions:** none that halt this plan. The one condition that could have forced a stop —
  whether `updates.jsonl` already carries the cancellation on its own, without a second file read —
  resolved during U1 (see Open Questions): it does.
- **Execution profile:** solo implementer, one sitting; touches four modules plus two templates.
- **Tail ownership:** the implementer runs the live verification in U4 and the project gate; no
  separate shipping tail.

---

## Product Contract

### Summary

Grok's headless CLI, under `--permission-mode auto`, cancels a `run_terminal_command` tool call
whose argument uses command substitution or a heredoc — exactly the `git commit -m "$(cat <<'EOF'
... EOF)"` form the compound-engineering pipeline teaches — instead of refusing it in a way the
task can react to. `updates.jsonl` already records this the same way it records a permission
denial, a `tool_call_update` with `status: "failed"`, but the classifier's denial scan only
recognizes one marker string and drops every other failed update silently. So two rounds of the
same failure produced two different, both-wrong halt readings (`closeout_out_of_scope`,
`unclean_exit`). This plan makes the classifier name the cause, tells future Grok tasks to avoid
the trigger, and records the caveat on the backend's capability pin.

### Problem Frame

Round eight (2026-09-01) ran tasks 45 and 56 on grok. Both worked normally for 19-24 minutes,
reached their commit step, and died the moment they issued a heredoc-inside-command-substitution
`git commit`. Both tasks' own `updates.jsonl` session files (confirmed by reading them directly
during U4, still on disk from that round) carry a `tool_call_update` with `status: "failed"` whose
body reads `User cancelled the execution for tool \`run_terminal_command\``, immediately ending
that turn — with no user present, since the run is headless. `contracts.BACKEND_PINS["grok"]`
already documents one prior finding in this family (`dontAsk` silently cancelling every tool call,
`docs/solutions/workflow-issues/grok-accepts-dontask-then-cancels-every-tool-call.md`); this is a
second, narrower trigger under the `auto` mode that finding chose specifically because it *does*
run real work.

The evidence for this specific trigger is not new data Relay lacks — `updates.jsonl` already
carries it, in the exact same `status: "failed"` shape the denial scan already reads — but
`backends/grok.py`'s `tool_call_update` branch only synthesizes a finding-bearing block when the
body contains `_DENIAL_MARKER` (`"Denied by permission policy"`), so a cancellation's body, which
never contains that phrase, is silently dropped before the classifier ever sees it.
`docs/solutions/workflow-issues/quota-exhaustion-reads-as-no-envelope-and-the-rate-limit-
telemetry-is-already-discarded.md` documents the identical shape at a different seam (rate-limit
telemetry discarded by `tail.decode`) and is a precedent for the fix pattern, not a landed change
to mirror line-for-line: grep confirms no code changed there yet.

### Requirements

**Classifier**

- R1. When a grok task's `updates.jsonl` carries a `tool_call_update` with `status: "failed"`
  whose body reads `User cancelled the execution for tool` (optionally backtick-wrapped) for a
  `run_terminal_command` call, `classify.classify` records a finding naming the cancelled tool and
  the command it was given, distinct from a permission denial.
- R2. The finding does not change which of the sixteen `HALT_CLASSES` (KTD6, closed set) the
  record gets. `halt_class` still falls through the existing precedence (no envelope present ->
  `no_envelope`; dirty tree found later by `run.py` -> `unclean_exit`); the finding rides
  alongside it, matching `RUNNER_SELF_KILL` and `WAITING_LAST_MESSAGE`'s precedent.
- R3. The new detection must not fire on the already-covered `_DENIAL_MARKER` case
  (`"Denied by permission policy"`) or on Grok's own auto-mode refusal phrasing (`"Auto mode
  blocked this action..."`), both already covered by `tests/test_classify.py`'s `GrokEvidence`
  class.

**Brief**

- R4. A grok Task's rendered brief instructs plain `git commit` forms only, with no command
  substitution and no heredoc, stated as a fact about this backend rather than a general style
  preference. A subject-plus-body message stays reachable through repeated `-m` flags (`git
  commit -m "Subject" -m "Body paragraph."`), so this backend does not lose the multi-paragraph
  commit convention the pipeline produces on the other two backends. Claude and codex briefs are
  unchanged.
- R5. The instruction is defanged like every other runner-authored brief sentence (R56, `brief.
  defang`), so a hostile task description cannot forge or neutralize it.

**Pin**

- R6. `contracts.BACKEND_PINS["grok"]` records, in the same comment convention as the existing
  `dontAsk` and denial-refusal findings, that a cancelled tool call is session-fatal on this
  backend: no retry, no envelope, whatever the task had in flight is stranded.

### Scope Boundaries

- Out of scope: broadening this to codex or claude. Neither backend has demonstrated this failure
  shape, and `docs/solutions/workflow-issues/grok-accepts-dontask-then-cancels-every-tool-call.md`
  already establishes that a permission construct's behavior does not generalize across CLIs.
- Out of scope: retrying a cancelled task automatically. The record still halts; only the Cause
  line changes. An automatic-retry policy is a separate design question belonging with issue #58
  (the retry-reassignment gap this round's manifest names as the next card).
- Folded into this plan, not deferred: bumping `grok`'s `version_tested` pin (currently `1.0.5`)
  to the version actually installed (`1.0.13`, confirmed in this session) happens in U4 as a
  byproduct of the required live verification on that installed version, not as a separate
  refactor — see U4's Approach.

### Open Questions

- Resolved during U1, confirmed by reading task 45's and task 56's real `updates.jsonl` files
  directly (both still on disk under `~/.grok/sessions/`, from the same round eight run the task
  text cites): the ACP session file already carries the cancellation as a `tool_call_update` with
  `status: "failed"`, the identical status a permission denial uses, distinguished only by the
  body text. The task's own evidence citations (`logs/45.stdout.log`, `logs/56.stdout.log`) are
  the stdout log, but that log turned out not to be the only place, or even the necessary place,
  to read this from — the plan's original premise (the signal is stdout-log-only, per
  `tests/test_classify.py:828-830`'s "tail's evidence, not classify's" boundary) does not hold for
  this specific marker, only for `stopReason`, which this fix does not need. No stdout log read
  was added; see KTD1.

---

## Planning Contract

### Key Technical Decisions

- KTD1. **Detect the cancellation entirely inside `backends/grok.py`'s existing `updates.jsonl`
  parse, widening the `tool_call_update` branch's body check rather than reading a second file.**
  U4's live verification, against task 45's and task 56's own real `updates.jsonl` files, found
  the cancellation is a `tool_call_update` with `status: "failed"` — the identical status the
  existing denial branch already reads — with the marker text in the body instead of a permission
  denial. This disproved the plan's original premise (that `stopReason` on the stdout log was the
  only signal, requiring a second-file read mirroring `backends/codex.py:24-66`'s two-file
  pattern) before any of that code was kept: `backends/grok.py:92-107`'s existing branch already
  synthesizes a `tool_result` block for a `"failed"` status whose body matches `_DENIAL_MARKER`;
  this KTD widens that same check to also match the cancellation marker, reusing the branch rather
  than adding a second one. No `Evidence` schema change was needed at all — a normalizer
  "translates, it does not invent a new schema" (KTD1 of `docs/plans/2026-08-30-1436-feat-
  evidence-line-shape-normalizers-plan.md`), and the existing schema already carried this signal.
- KTD2. **classify.py's existing denial-scanning loop (`classify.py:396-420`) gains a second
  marker check, sibling to `contracts.DENIAL_REGEX`, rather than a separate loop.** The loop
  already looks up the originating `tool_use` by id and computes `_target_of(use)`; a cancellation
  finding needs exactly the same lookup with a different regex and a different resulting class,
  not the promotion logic denial has (`HALT_PATH_GATE`, `HALT_TRACKER_WRITE_DENIED`), since a
  cancelled call was never permitted to run and touches no path. New finding fields mirror the
  denial finding's own vocabulary (`tool`, `target`, `line`, `tool_use_line`) rather than
  inventing an `argument` field, so both branches share one shape.
- KTD3. **The new finding class (`CANCELLED_TOOL_CALL`) is finding-only, added to
  `FINDING_CLASSES` and `LINE_CLASSES` but never to `HALT_CLASSES`.** Matches R2 and the precedent
  set by `RUNNER_SELF_KILL` and `WAITING_LAST_MESSAGE` (`contracts.py:397-406`): KTD6's sixteen
  halt classes are a closed set, amended only when the plan says so, and this plan does not.
- KTD4. **The commit-message constraint is threaded through a new `Capability` field
  (`commit_message_constraint: str | None`, `None` for claude and codex, prose for grok), not a
  `task.backend == "grok"` string comparison in `brief.py`.** `brief.py` already reads capability
  fields exclusively (`_unenforced_block` conditions on `capability.enforces_at_launch`); a
  backend-name comparison would be the only one of its kind in that module. `Capability` is a
  frozen dataclass with no defaults (`backends/__init__.py:141-166`), so the field must be added
  to all three `BACKEND_PINS` dicts even though only grok's is non-empty.
- KTD5. **The `BACKEND_PINS["grok"]` caveat is a comment plus the new `commit_message_constraint`
  string, not a standalone boolean flag.** Matches the two existing precedent comments in the same
  dict (`dontAsk`, lines 254-259; the R25 denial demonstration, lines 265-269): state what was
  observed, how many times, and what follows from it, so the pin explains itself to a reader who
  has not seen this plan.

### Assumptions

None remain open. Both assumptions this plan carried at write time — the shape of the
cancellation signal, and whether grok's current installed version still exhibits it — were
resolved by U4 against real evidence rather than left standing: see Open Questions above for the
shape, and U4's Approach for the version.

---

## Implementation Units

### U1. Classifier detects a cancelled tool call and attaches a finding

**Goal:** a grok stream ending in a cancelled `run_terminal_command` produces a
`CANCELLED_TOOL_CALL` finding naming the tool and the command, without changing the record's
`halt_class`.

**Requirements:** R1, R2, R3

**Dependencies:** none

**Files:**
- `skills/relay/scripts/relay/contracts.py` (new `CANCELLED_TOOL_CALL` constant, new marker
  regex, `FINDING_CLASSES`, `LINE_CLASSES`, `HALT_LINES` entries)
- `skills/relay/scripts/relay/backends/grok.py` (`normalize_transcript`'s existing
  `tool_call_update` branch widens to a second marker; `log_path` stays unread)
- `skills/relay/scripts/relay/classify.py` (loop at `classify.py:396-420` gains the sibling
  marker check)
- `tests/test_classify.py` (new cases in `GrokEvidence`, a `run_grok_updates` helper)
- `tests/test_summary.py` (`FINDING_ROWS` gains a row, or `test_the_table_covers_every_class_in_
  halt_lines` fails immediately)

**Approach:**
1. In `contracts.py`, add `CANCELLED_TOOL_CALL = "cancelled_tool_call"` near `WAITING_LAST_MESSAGE`
   (`contracts.py:406`), with the same "Finding only" comment convention citing this issue and R2.
   Add a marker regex sibling to `DENIAL_REGEX` (`contracts.py:93`), tolerant of the backtick-
   wrapped tool name already quoted in the grok pin comment. Add the constant to `FINDING_CLASSES`
   (`contracts.py:457-468`) and `LINE_CLASSES` (`contracts.py:472-475`), and a `HALT_LINES` entry
   reading `"the CLI cancelled its own tool call, no user present: {tool} on {target}"`.
2. In `backends/grok.py:normalize_transcript`'s existing `tool_call_update` branch
   (`grok.py:92-107`), widen the `if _DENIAL_MARKER not in body: continue` gate to a second
   check: when the new marker regex matches instead, synthesize the same `tool_result` shape,
   passing `body` straight through rather than reconstructing it (unlike the denial branch, the
   real marker text already matches the regex verbatim, confirmed against task 45's and task 56's
   own captures). No log_path read, no new file.
3. In `classify.py`'s loop (`classify.py:396-420`), add the sibling branch: when
   `contracts.DENIAL_REGEX` does not match but the new marker regex does, append a finding
   `{"class": contracts.CANCELLED_TOOL_CALL, "tool": tool, "target": _target_of(use), "line":
   number, "tool_use_line": use.get("_line")}` — no promotion to `HALT_PATH_GATE` or
   `HALT_TRACKER_WRITE_DENIED`, since a cancelled call was never permitted to touch anything.
4. Add a `FINDING_ROWS` row for `CANCELLED_TOOL_CALL` in `tests/test_summary.py`, citing
   `classify.classify` as the raiser — the completeness test there fails immediately without it.

**Technical design:**

```text
updates.jsonl (ACP session file), one `tool_call_update`:
  sessionUpdate=tool_call_update, toolCallId=X, status="failed",
  content=[{text: "User cancelled the execution for tool `run_terminal_command`"}]
        |
        v  (normalize_transcript's existing branch, marker check widened)
  lines += [(n, {user, tool_result tool_use_id=X is_error=True content=<body, unchanged>})]
        |
        v  (classify.py's existing loop, new sibling branch: DENIAL_REGEX misses, marker matches)
  finding = {class: CANCELLED_TOOL_CALL, tool: "Bash", target: C[:120], line: n, tool_use_line: n-1}
```

**Patterns to follow:** `backends/grok.py:92-107` (the branch this widens), `contracts.py:397-406`
(finding-only constant comment style), `classify.py:396-420` (the branch this extends).

**Test scenarios:**
- Happy path: a synthetic `updates.jsonl` (built as a tempfile per `SelfKillScan._log()`'s
  precedent, `tests/test_classify.py:882-888` — not committed under `tests/fixtures/backends/
  grok/`, whose README requires every file there be a real, unedited capture) carrying the real
  marker text produces exactly one `CANCELLED_TOOL_CALL` finding naming `Bash` and the command,
  and `halt_class` still resolves to `no_envelope` as before this change.
- Error/regression path: `session-transcript-complete.jsonl`'s existing embedded denial
  (`GrokEvidence.test_session_transcript_complete_also_carries_an_embedded_denial`) still produces
  exactly one `HALT_DENIED_TOOL` finding and zero `CANCELLED_TOOL_CALL` findings (R3).
- Error/regression path: `session-transcript-blocked.jsonl`'s auto-mode-blocked phrasing
  (`GrokEvidence.test_auto_mode_blocked_this_action_is_not_a_denial`) continues to produce neither
  a `HALT_DENIED_TOOL` nor a `CANCELLED_TOOL_CALL` finding (R3).
- Integration: `run_grok_updates` (new helper, sibling to `run_grok` at
  `tests/test_classify.py:803-806`) confirms the finding survives into the same
  `result["findings"]` list `finding_line`/`summary.cause_line` already render from, with no new
  renderer code needed (both are generic over `finding["class"]`).

**Execution note:** confirm the marker's exact shape against a real captured transcript in U4
before treating this unit's synthetic fixture as final proof; U4 found the real shape needs no
second file at all, so the detection here reads `updates.jsonl` alone.

**Verification:** `python3 -m unittest test_classify test_summary` passes with the new cases; no
existing `GrokEvidence` or `DeniedTool`-family test's assertions change. Confirmed directly
against the real `updates.jsonl` files for task 45 and task 56, both still on disk under
`~/.grok/sessions/` from round eight: both produce exactly one `CANCELLED_TOOL_CALL` finding.

---

### U2. Brief tells a grok task to avoid the trigger

**Goal:** a rendered grok brief instructs plain `git commit` forms only (single `-m`, or repeated
`-m` for a subject plus body); claude and codex briefs are byte-identical to before this change.

**Requirements:** R4, R5

**Dependencies:** none (independent of U1)

**Files:**
- `skills/relay/scripts/relay/contracts.py` (`Capability` dataclass gains
  `commit_message_constraint: str | None`; all three `BACKEND_PINS` dicts set it)
- `skills/relay/scripts/relay/brief.py` (new constant sentence, a block-builder function mirroring
  `_unenforced_block`, a new `values()` key, `defang` tuple)
- `skills/relay/templates/brief-local-merge.md` (new placeholder)
- `skills/relay/templates/brief-pr-terminal.md` (same placeholder, for template symmetry — this
  mode is unreachable via `validate` today but is still rendered by its own tests)
- `tests/test_brief.py` (new render-time assertions)

**Approach:**
1. Add `commit_message_constraint: str | None` to the `Capability` dataclass (`backends/__init__.py:
   141-166`). Set it to `None` for claude and codex (matching the `allow_flag`/`deny_flag`
   precedent for "this backend has none" rather than an empty string — `test_backends.py`'s
   `CapabilityRecord` test forbids an empty-string pin value), and for grok to a sentence stating the
   constraint as a backend fact (e.g., a git commit message must not use command substitution or
   a heredoc on this backend, because that shape gets the tool call cancelled outright with no
   envelope; for a subject plus body, repeat `-m`) — worded as fact, not general style advice,
   per R4.
2. In `brief.py`, add a builder function mirroring `_unenforced_block` (`brief.py:169-181`): return
   `""` when `capability.commit_message_constraint` is empty, else `"\n" + sentence + "\n"`, so the
   empty case renders exactly as today (same whitespace contract `_unenforced_block` already
   documents). Add the resulting string to `values()`'s returned dict under a new key.
3. In both templates, place the new placeholder where a blank line already separates the "Work on
   the branch..." paragraph from the next paragraph (`brief-local-merge.md:32-34`; the equivalent
   spot in `brief-pr-terminal.md`), on its own line with no blank line directly above or below it —
   matching `_unenforced_block`'s placement convention exactly, so claude and codex briefs keep
   today's single blank line there.
4. Add the new grok sentence to `brief.defang`'s tuple (`brief.py:151`), alongside
   `UNENFORCED_LEAD`, `UNENFORCED_OVERRIDE_REFUSAL`, `UNENFORCED_AUDIT`, so a hostile task
   description cannot forge or neutralize it (R5).

**Patterns to follow:** `brief.py:169-181` (`_unenforced_block`'s empty-case whitespace contract),
`brief.py:107-129` (`UNENFORCED_LEAD` et al. as module-level sentence constants), `brief.py:141-153`
(`defang`).

**Test scenarios:**
- Happy path: rendering a grok brief includes the exact commit-message sentence.
- Happy path: rendering a claude brief and a codex brief are both byte-identical to their
  pre-change output (the empty-string case collapses to today's single blank line, matching
  `_unenforced_block`'s own tested contract).
- Edge case: a task description containing a verbatim copy of the new sentence renders with
  `INSTRUCTION_REMOVED` in its place inside the data block, same as the existing three defanged
  sentences (mirror `tests/test_brief.py`'s existing defang assertions).
- Integration: `brief-pr-terminal.md` renders without a `BriefError` for a grok task, confirming
  the placeholder is defined in both templates' shared `values()` mapping.

**Verification:** `python3 -m unittest test_brief` passes; a manual render of both templates for
all three backends shows no unintended whitespace drift outside the new placeholder's own lines.

---

### U3. BACKEND_PINS records the caveat

**Goal:** a reader of `contracts.BACKEND_PINS["grok"]` learns, without reading this plan, that a
cancelled tool call is session-fatal on this backend.

**Requirements:** R6

**Dependencies:** U1 (cites the finding class it documents), U2 (cites the brief constraint it
documents)

**Files:**
- `skills/relay/scripts/relay/contracts.py` (comment block above the grok `permission_mode` trio,
  `contracts.py:254-261`)

**Approach:**
1. Add a comment above (or extending) the existing `dontAsk` comment block, in the same style
   (what was observed, how many times, what follows): a `run_terminal_command` whose argument uses
   command substitution or a heredoc is cancelled outright under `auto` mode rather than executed
   or refused; `updates.jsonl` carries it as a `tool_call_update` with `status: "failed"`, the
   same status a permission denial uses, distinguished only by the body text; no retry, no
   envelope, whatever the task had in flight is stranded. `classify.py` now surfaces this as a
   `CANCELLED_TOOL_CALL` finding (U1) and the brief tells the task to avoid the construct (U2)
   since instruction is the only enforcement layer this backend has for it.
2. Cite the confirming date once U4's real-data check runs, the same way the `dontAsk` comment
   cites "reproduced five times" — here the confirmation is two real captures read directly,
   not a fresh reproduction count.

**Patterns to follow:** `contracts.py:254-259` and `:265-269` (the two existing grok pin
comments); `contracts.py:210-223` (codex's issue-numbered pin comments, for the citation style).

**Test scenarios:**
- Test expectation: none — comment-only change with no executable behavior; `tests/
  test_contracts.py`'s existing structural checks (dict key completeness, no test targets comment
  text) are unaffected.

**Verification:** `python3 -m unittest test_contracts` still passes; the comment reads correctly
standalone (no forward reference to "this plan").

---

### U4. Live verification against a real grok process

**Goal:** confirm, against the real installed `grok` CLI, that (a) the assumed cancelled-stream
shape (Planning Contract Assumptions) matches reality, (b) U1's detection fires on that real
capture, and (c) U2's brief instruction avoids the cancellation entirely — satisfying this repo's
own rule that a contract change between processes needs a live probe before it is called done
(`docs/solutions/logic-errors/stubbed-seams-agree-by-construction-first-live-run-found-five-
contract-defects.md`).

**Requirements:** R1 (shape confirmation), R4 (remedy confirmation)

**Dependencies:** U1, U2

**Files:** none in this repo. Real evidence came from reading task 45's and task 56's own
`updates.jsonl` files already on disk under `~/.grok/sessions/`, not from committing a new
fixture; U1's synthetic tempfile remains the fast unit-test proof.

**Approach:**
1. Attempt to reproduce the failure directly, without the full Relay pipeline: launch `grok`
   1.0.13 headless in a scratch git repository with `--permission-mode auto` and a prompt that
   stages a file and commits it with the heredoc-inside-command-substitution form the task text
   quotes, capturing raw stdout. **Result: it did not reproduce** — two single-command probes
   (one with a narrow `--allow 'Bash(git commit*)'`, one with the bare `--allow Bash` the
   production manifest actually passes) both committed cleanly under grok 1.0.13. A single
   trivial `-p` prompt does not reliably reproduce this bug; whatever conditions make it fire
   during a real 19-24 minute multi-turn task did not hold here.
2. Read task 45's and task 56's own `updates.jsonl` files directly instead — both still on disk
   under `~/.grok/sessions/<encoded-cwd>/<session-id>/updates.jsonl` from round eight, since nothing
   has cleaned that directory. This is real, unedited evidence from the exact failures the task
   text describes, stronger than a fresh reproduction attempt: it disproved the plan's stdout-log
   premise (`status: "failed"`, not `"cancelled"`; no second file needed) and drove KTD1's rewrite.
3. Run `python3 -m unittest test_classify` after pointing `classify.classify` at both real
   `updates.jsonl` paths directly (outside the suite, as a one-off check): both produce exactly
   one `CANCELLED_TOOL_CALL` finding, confirming U1 against the original bug's own data rather
   than only a hand-built fixture — the strongest available answer to "stubbed seams agree by
   construction" (`docs/solutions/logic-errors/stubbed-seams-agree-by-construction-first-live-run-
   found-five-contract-defects.md`) for this specific change.
4. Confirm U2's remedy independently: a probe using the exact multi-`-m` form R4 recommends
   (`git commit -m "Test commit" -m "Body paragraph explaining the change."`) landed a normal
   two-line commit under grok 1.0.13, confirming the subject-plus-body escape hatch works.
5. Record the installed grok version (`1.0.13`, confirmed via `grok --version` in this session)
   and update `version_tested`/`version_output_sample` in the same commit as U3's caveat, since
   this live session is exactly the re-verification a version bump requires.

**Execution note:** this is a live-process and real-data investigation, not a unit test. Step 1's
disposable scratch directory is cleaned up after use; steps 2-3 read existing files and mutate
nothing.

**Test scenarios:**
- Happy path: the plain-`-m` and multi-`-m` probes both land a real commit and the stream ends in
  `end_turn`.
- Real-data path: `classify.classify` against task 45's and task 56's actual `updates.jsonl`
  produces the `CANCELLED_TOOL_CALL` finding on both, with `halt_class` still `no_envelope`.

**Verification:** both probes' raw output and both real-data classify runs inspected by hand; the
Definition of Done's "no dead-end code" item is satisfied by removing the abandoned log_path
design from U1 in the same session rather than leaving it committed.

---

## Verification Contract

| Command | Applies to | What it proves |
|---|---|---|
| `python3 -m unittest test_classify` | U1 | New finding fires correctly and does not regress existing `GrokEvidence` cases |
| `python3 -m unittest test_summary` | U1 | `FINDING_ROWS` covers the new class; no placeholder survives its cause line |
| `python3 -m unittest test_brief` | U2 | Grok brief carries the constraint; claude/codex briefs unchanged; defang covers the new sentence |
| `python3 -m unittest test_contracts test_backends` | U1, U2, U3 | `BACKEND_PINS`/`Capability` dict shape stays complete across all three backends; no broken pin cross-reference |
| `python3 -m unittest discover -s tests` | all | The full project gate (this repo's `[gate]` command); a local untracked pre-push hook re-runs it on every push |
| `classify.classify` against task 45's and task 56's real `updates.jsonl` (U4) | U1 | The finding fires on the original bug's own data, not only a hand-built fixture |
| Live `grok` 1.0.13 probes (U4) | U4 | The brief's remedy (plain and multi-`-m` commit forms) lands cleanly; a single trivial `-p` probe does not reproduce the failure itself |

## Definition of Done

- All four units' test scenarios pass under `python3 -m unittest discover -s tests`.
- U4's real-data check and remedy probes have both run; the shape mismatch they found (stdout log
  and `stopReason` were never necessary; `updates.jsonl`'s existing `status: "failed"` shape
  already carries it) is folded back into U1, not left as a second, unused code path.
- No leftover synthetic-only fixture stands in for a real capture: U1's tempfile fixture is the
  unit-test proof, and U4's real-data check against task 45/56 stands as the live proof; no new
  fixture file was added or was needed.
- `contracts.BACKEND_PINS["grok"]`'s new comment and `version_tested` (bumped to `1.0.13`) read
  correctly to someone who has not read this plan.
- No dead-end code from the abandoned log_path/stopReason design remains — the log_path read, the
  `_terminal_cancellation` helper, and the `stopReason`-dependent test cases were removed in the
  same session U4 found they were unnecessary, not left committed alongside the working fix.

---

## Sources / Research

- `docs/solutions/workflow-issues/grok-accepts-dontask-then-cancels-every-tool-call.md` — direct
  precedent for the `BACKEND_PINS["grok"]` comment convention (U3) and the "prove a posture by
  observing both a refusal and a success" standard this plan's U4 follows for the cancellation
  case.
- `docs/solutions/workflow-issues/quota-exhaustion-reads-as-no-envelope-and-the-rate-limit-
  telemetry-is-already-discarded.md` — the finding-not-a-class pattern (KTD3) this plan mirrors;
  confirmed prescriptive only, not yet landed in code, so this plan is the first implementation of
  that pattern rather than a copy of an existing diff.
- `docs/solutions/logic-errors/stubbed-seams-agree-by-construction-first-live-run-found-five-
  contract-defects.md` — governs U4's requirement and U1's synthetic-fixture caveat (a hand-built
  fixture proves the parser agrees with itself, not that it matches reality).
- `docs/plans/2026-08-30-1436-feat-evidence-line-shape-normalizers-plan.md` (KTD1) — "a normalizer
  translates, it does not invent a new schema," why U1 widens the existing `updates.jsonl` branch
  rather than reading a second file or touching `Evidence`'s shape.
- `tests/fixtures/backends/README.md` (the shared backend-fixtures README, not a grok-specific
  one) — the real-capture-only invariant U1's Execution note and U4 both honor.
- `~/.grok/sessions/%2FUsers%2Fpgutowski%2FDocuments%2FPhilAI%2Frelay/962d021f-8445-4e1a-a7e7-
  e485536cf0a1/updates.jsonl` and `.../40ad596b-a77e-4417-b051-1067686bede4/updates.jsonl` — task
  45's and task 56's own session files, read directly during U4; the source of the corrected
  `status: "failed"` shape and the reason KTD1 does not read a second file.
- `contracts.py:254-283` — the grok `BACKEND_PINS` entry U1, U3, and part of U2 all touch; read in
  full before editing so the three units' edits do not collide on the same lines.
