---
title: Task Branch Prefix Manifest Field - Plan
type: feat
date: 2026-08-31
artifact_contract: ce-unified-plan/v1
artifact_readiness: implementation-ready
product_contract_source: ce-plan-bootstrap
execution: code
---

# Task Branch Prefix Manifest Field - Plan

## Goal Capsule

**Objective:** an operator running Relay against a repo whose branch convention is not `relay/<id>` can name that convention in the Manifest, and the Task process and the Runner then look at the same branch.

**Means:** optional `[project] branch_prefix` that overrides `TASK_BRANCH_PREFIX`, defaulting to `"relay/"` when absent, with `task_branch_for` as the one namer both the brief and the git tail call (KTD1, KTD2).

**Authority hierarchy:** this plan's Key Technical Decisions; then `CLAUDE.md` (halt classes stay closed, the Runner never writes the tracker); then the existing Manifest default pattern in `skills/relay/scripts/relay/manifest.py` (`pick()`, KTD11 named defaults).

**Stop conditions:** the unittest suite passes; a Manifest that omits the field still produces `relay/<id>` at every namer call site; a Manifest that sets a different prefix produces that prefix plus the Task id at every namer call site, including the brief.

**Execution profile:** one session, all units together, no rollout.

**Tail ownership:** the calling Relay Task process runs the project gate and lands the branch. This plan does not describe shipping.

---

## Product Contract

### Summary

Make the Task branch prefix a Manifest field under `[project]`. Today the prefix is the constant `"relay/"` in `gitwrite.py`, duplicated as a string in `brief.py`. A repo that names branches `IW-<n>-<slug>` cannot keep the Runner from prefixing `relay/`. The field defaults to `"relay/"` when absent so every existing Manifest and test keeps working.

### Problem Frame

support-workbench's branch convention is `IW-<n>-<slug>`. Relay currently tells every Task process to create `relay/<id>` and then looks for that same name in preflight, the local merge tail, verify, and `--retry-blocked`. An operator who wants a different prefix has no Manifest knob. The two hardcoded sites the issue names are the visible ones. The load bearing split is that `brief.py` builds the name itself while `run.py` and `verify.py` call `gitwrite.task_branch_for`, which only reads the constant. Changing one without the other would tell the process one name and have the Runner look for another.

### Requirements

**Manifest field**

- R1. `[project]` may name `branch_prefix` as a string. When the key is absent, the loaded Manifest carries `"relay/"` and `defaults_applied` records `project.branch_prefix = 'relay/'` the same way other named defaults work.
- R2. When the key is present, including as the empty string, that value is the prefix. Absence and an explicit empty string are different: absence is the default, empty means the branch name is the Task id alone.
- R3. A non-string `branch_prefix` is a validate error. Existing Manifests and fixtures that omit the key remain valid.

**Single namer**

- R4. The branch name for a Task is always prefix concatenated with the Task id. `gitwrite.task_branch_for` is the only function that performs that concatenation.
- R5. `brief.values` uses that namer (or the Manifest prefix through it) for the `$branch` placeholder. It does not concatenate `"relay/"` itself.
- R6. Every Runner site that currently calls `task_branch_for(task.id)` (the run loop, verify, the local merge tail, `--retry-blocked`) passes the Manifest prefix so a custom prefix is what preflight, merge, and retry look for.

**Docs and fixtures**

- R7. `skills/relay/SKILL.md` describes a stranded Task branch without hardcoding the `relay/` prefix, and its Author a manifest steps ask whether this repo uses a prefix other than `relay/`.
- R8. Each file under `docs/examples/` shows `branch_prefix` as a commented example under `[project]`, in the same comment style those files already use for optional fields.
- R9. `tests/test_summary.py` keeps passing against stored `relay/` branch names on records. Those tests render a record's `branch` field. They do not load the namer. Do not rewrite them to invent a prefix field they do not read.
- R10. `tests/fixtures/backends/claude/stdout-complete.jsonl` stays as captured. It is a live run against the default prefix, not a Manifest sample.

### Success Criteria

- SC1. A Manifest with no `branch_prefix` key still names every Task branch `relay/<id>` in the brief and in the Runner.
- SC2. A Manifest with `branch_prefix = "IW-"` names every Task branch `IW-<id>` in the brief and in the Runner.
- SC3. `python3 -m unittest discover -s tests` passes.

### Scope Boundaries

- In scope: the optional field, the namer, the Runner and brief call sites, the skill wording (resume text plus one authoring question for the prefix), the example Manifest comments, the `gitwrite` module docstring, the Task process sentence in `CONCEPTS.md`, and tests that prove default, override, empty prefix, and type refusal.
- Out of scope: a full branch name template (`IW-<n>-<slug>` with a slug). Prefix plus Task id is the whole naming rule.
- Out of scope: rewriting historical transcripts, solutions docs, or the outer loop plan that mention `relay/<id>` as the then current default.
- Out of scope: a live run against support-workbench. The issue says this is forward looking.
- Out of scope: a new halt class.

### Deferred to Follow-Up Work

- A slug or template form that could produce `IW-<n>-<slug>` from the card title. Prefix plus id is enough to stop `relay/` landing on that repo. The slug is a later Manifest field if the operator still wants it.

---

## Planning Contract

### Key Technical Decisions

- KTD1. **`[project] branch_prefix` is the field, defaulted through `pick()`.** `[project]` already holds repo facts (`repo`, `default_branch`, `mirror`). A branch naming convention is a fact about that repo, not a Task process default and not a new table for one key. `pick()` records the default in `defaults_applied` (the existing KTD11 rule: nothing is defaulted silently). Governs R1, R2, R3.
- KTD2. **`task_branch_for` takes the prefix and is the only concatenator.** Keep `TASK_BRANCH_PREFIX = "relay/"` as the default constant. The function takes an optional prefix and uses the constant when prefix is `None`, so existing tests that call `task_branch_for(task_id)` keep working. Distinguish `None` from `""`: `prefix or TASK_BRANCH_PREFIX` would turn an empty override back into `relay/`. `brief.values` calls it with the Manifest prefix instead of `"relay/" + task.id`. `local_merge_tail` currently derives the branch internally from the Task id alone and must take the prefix too, otherwise a custom prefix would pass preflight against one name and merge another. Production callers always pass the Manifest prefix. The omitted argument exists for unit tests and stubs, not for `run.py` or `verify.py`. Governs R4, R5, R6.
- KTD3. **Empty string is a real override, and a trailing slash is not required.** `pick()` already treats a present empty value as the author's value. support-workbench may want `IW-` (no slash) or `""` (Task id alone). Jira Task ids already look like `IW-55`, so an empty prefix is the escape that avoids `IW-IW-55`. Requiring a trailing slash would refuse the trigger case. Do not add `git check-ref-format` at validate. An illegal git ref already fails at checkout as `unclean_exit`. Governs R2.
- KTD4. **Do not rewrite captured transcripts or summary tests that store a branch name.** `test_summary.py` asserts on `record["branch"]`, which is already `"relay/T-2"` in those fixtures. `stdout-complete.jsonl` is a real process that created `relay/T-4` under the default. Changing either would couple historical captures to a field they never had. Governs R9, R10.

### Assumptions

- A1. Prefix plus Task id is what the operator asked for. The trigger names `IW-<n>-<slug>`, but the issue text specifies a prefix field that overrides `TASK_BRANCH_PREFIX`, not a template. An empty or `IW-` prefix is the intended escape from `relay/`.
- A2. Existing tests that hardcode `"relay/" + id` against the default Manifest stay green because the default is `"relay/"`. Only new tests need a Manifest that sets a different prefix.
- A3. `Project` is constructed only in `manifest.load` in production code. Tests that build a Manifest through `load()` pick up the new field. Hand built `SimpleNamespace` project objects used as stubs need a `branch_prefix` attribute only where a namer call will read it. Prefer giving `task_branch_for` a default so a stub that never names a prefix still concatenates `"relay/"`.
- A4. The run loop stub that currently checks out `relay/%s` (`TASK_BRANCH_SH` in `tests/test_run.py`) must take the Manifest prefix. The stub does not parse the brief's `$branch` placeholder, so a green default prefix suite is not proof of SC2.

### Implementation Constraints

- Halt classes stay a closed set. A bad prefix is a checkout failure, not a new class.
- The Runner still never writes the tracker.
- Python 3 standard library only.
- Prose in this repo uses no dashes of any kind.

---

## Implementation Units

### U1. Manifest field and named default

**Goal:** load and validate `project.branch_prefix` with the same `pick()` pattern other optional keys use.

**Requirements:** R1, R2, R3

**Dependencies:** none

**Files:**
- `skills/relay/scripts/relay/manifest.py`
- `tests/test_manifest.py`
- `tests/fixtures/manifests/complete.toml` only if a new assertion needs an explicit key. Prefer leaving the fixture unchanged so the absent key path is what the suite already loads.

**Approach:**
1. Add `branch_prefix` to the `Project` dataclass. Load it with `pick(p, "project", "branch_prefix", gitwrite.TASK_BRANCH_PREFIX)` (or the same literal the constant holds, pinned equal by test).
2. `gitwrite` does not import `manifest`, so `manifest` importing `gitwrite` for the default constant does not cycle. If an import cycle appears, put the default string on `contracts` (add `contracts.py` to this unit's Files) and alias it from `gitwrite`.
3. In `validate`, refuse a value that is not a `str`. Do not refuse the empty string.
4. `Project` field order: add `branch_prefix` with a default of `"relay/"` only if tests construct `Project(...)` without it. Prefer matching existing required fields and going through `load()`.

**Patterns to follow:** `pick()` for `on_halt.continue_past_task_halt` and the timeout keys. `test_manifest.py` cases that assert a named default appears in `defaults_applied` when the key is absent and does not appear when the key is set.

**Execution note:** write the load and validate tests first, then the field.

**Test scenarios:**
- Absent key: loaded prefix is `"relay/"`, and `defaults_applied` contains `project.branch_prefix = 'relay/'`.
- `branch_prefix = "IW-"`: loaded prefix is `"IW-"`, and that key is absent from `defaults_applied`.
- `branch_prefix = ""`: loaded prefix is `""`, and the key is absent from `defaults_applied`.
- `branch_prefix = 1`: `validate` is not ok and the error names `project.branch_prefix`.
- The existing complete fixture still validates.

**Verification:** the new `test_manifest.py` cases pass and `CompleteManifest` still passes.

### U2. Single namer threaded through brief and Runner

**Goal:** every site that names a Task branch uses `task_branch_for` with the Manifest prefix.

**Requirements:** R4, R5, R6. Cites KTD2, KTD3.

**Dependencies:** U1

**Files:**
- `skills/relay/scripts/relay/gitwrite.py`
- `skills/relay/scripts/relay/brief.py`
- `skills/relay/scripts/relay/run.py`
- `skills/relay/scripts/relay/verify.py`
- `tests/test_gitwrite.py`
- `tests/test_brief.py`
- `tests/test_run.py` (edit: `TASK_BRANCH_SH` and the `local_merge_tail` caller at the Tail tests)
- `tests/test_verify.py`
- `tests/test_summary.py` (read only: confirm existing prefix assertions still pass. Add nothing unless a new summary path starts naming the branch from the Manifest instead of the record.)

**Approach:**
1. Change `task_branch_for` to take an optional prefix and concatenate `prefix + task_id`, using `TASK_BRANCH_PREFIX` when prefix is `None`. Empty string is a prefix, not an omit.
2. `brief.values`: `branch = branch or task_branch_for(task.id, manifest.project.branch_prefix)`.
3. `run.py` and `verify.py`: pass `manifest.project.branch_prefix` at every `task_branch_for` call. Sites today: the GitError handler, preflight, and `_clear_blocked_branch` in `run.py`, plus `verify.verify`. `_clear_blocked_branch` currently takes no Manifest. Give it the prefix. The `_Run` dataclass already holds `manifest`.
4. `local_merge_tail` currently calls `task_branch_for(task_id)` with no prefix. Thread the prefix in as an argument. Callers must pass it: `run.py`, `tests/test_gitwrite.py` `TailBase.run_tail`, and `tests/test_run.py`.
5. Point the run loop stub (`TASK_BRANCH_SH`) at the Manifest prefix so a custom prefix run actually creates that branch. The stub does not read `$branch`.
6. Leave `TASK_BRANCH_PREFIX` defined. Tests and the Manifest default still read it.

**Patterns to follow:** `tests/test_brief.py` `LocalMergeTemplate.test_the_brief_orders_the_pipeline_and_keeps_the_runner_owned_steps_out` already asserts the brief contains `relay/T-1` under the default Manifest. Add a sibling that loads a Manifest with `branch_prefix = "IW-"` and asserts `IW-T-1` appears and `relay/T-1` does not.

**Execution note:** write the custom prefix Runner tests first. A green default prefix suite is not SC2. The omitted prefix default on `task_branch_for` will hide a missed production argument unless those tests load `branch_prefix = "IW-"` and `branch_prefix = ""` through preflight, `local_merge_tail`, verify, and `--retry-blocked`.

**Test scenarios:**
- `task_branch_for("55")` still returns `"relay/55"`.
- `task_branch_for("55", "IW-")` returns `"IW-55"`.
- `task_branch_for("55", "")` returns `"55"`.
- Default Manifest brief contains `relay/T-1` in the create branch step.
- Manifest with `branch_prefix = "IW-"` brief contains `IW-T-1` and does not contain `relay/T-1`.
- Manifest with `branch_prefix = "IW-"`: preflight, `local_merge_tail`, `verify.verify`, and `_clear_blocked_branch` look for `IW-<id>` and not `relay/<id>`.
- Manifest with `branch_prefix = ""`: those same four sites look for the Task id alone.
- Existing `test_summary.py` continued past and stranded branch cases still pass.
- A grep of `skills/relay/scripts/relay/` for `"relay/" +` and `TASK_BRANCH_PREFIX +` returns only the constant definition and `task_branch_for`.
- Every production `task_branch_for(` call in `run.py`, `verify.py`, and `local_merge_tail` passes a prefix argument.

**Verification:** `python3 -m unittest tests.test_gitwrite tests.test_brief tests.test_summary tests.test_run tests.test_verify` pass.

### U3. Skill wording and example Manifests

**Goal:** operators see that the prefix is configurable, and the resume wording no longer hardcodes `relay/<task-id>`.

**Requirements:** R7, R8. The `gitwrite` module docstring and the Task process sentence in `CONCEPTS.md` are in this unit so they stay aligned with R4.

**Dependencies:** U1 (the field name must be settled)

**Files:**
- `skills/relay/SKILL.md`
- `docs/examples/manifest-github-projects.toml`
- `docs/examples/manifest-jira-local-merge.toml`
- `docs/examples/manifest-markdown.toml`
- `skills/relay/scripts/relay/gitwrite.py` (module docstring still says the Task process exits on `relay/<task-id>`. Reword that sentence to the prefix plus id form.)
- `CONCEPTS.md` (one sentence on Task process: the branch name is the Manifest prefix plus the Task id, default `relay/`)

**Approach:**
1. In `SKILL.md` resume text, say a stranded Task branch that still carries commits, not `relay/<task-id>`.
2. In `SKILL.md` Author a manifest, after the repo and tracker questions, ask whether this repo names Task branches as a prefix plus the Task id, default `relay/`. Write `branch_prefix` only when the operator names a different prefix. Point at the commented example lines rather than inventing a new table.
3. Under each example `[project]` table, add a commented `branch_prefix` line that names the default and why an operator would set it. The Jira example should show the empty prefix, because Jira Task ids already carry the project key. Match the comment density of neighbouring keys. Do not uncomment it.
4. Reword the `gitwrite.py` module docstring sentence that says the Task process exits on `relay/<task-id>` to the prefix plus id form.
5. `CONCEPTS.md` Task process already carries the prefix plus id sentence from planning. Leave that sentence. Do not add a second one.
6. Do not rewrite `docs/solutions/` or historical plans.

**Patterns to follow:** the commented `on_halt.continue_past_task_halt` block in `docs/examples/manifest-github-projects.toml`.

**Test expectation:** none. These are comments and skill prose. U2's grep scenario is the check that code comments did not leave a second concatenator.

**Verification:** the three example files parse as TOML with the new lines commented. `SKILL.md` no longer contains the stranded `relay/<task-id>` phrase and the authoring steps mention `branch_prefix`. The `gitwrite.py` module docstring does not hardcode `relay/<task-id>` as the only name.

---

## Verification Contract

The project gate is the unittest suite from the repo root:

```text
python3 -m unittest discover -s tests
```

Per unit, run the modules named in that unit's Verification before moving on. The full discover run is the Definition of Done gate, not a substitute for the per unit scenarios.

No live Task process run is required. The issue is forward looking and names no live target. The envelope grammar, the closeout terminal line, the brief template's placeholder names, the halt record, and the classify digest keys are unchanged. The brief's `$branch` value changes with the Manifest. The placeholder name does not. SC2 is proven in the suite by pointing `TASK_BRANCH_SH` at the Manifest prefix (A4) and by the U2 Runner scenarios, not by a throwaway live Task. The first real process that sees a non default prefix is the operator's next run against that repo.

---

## Definition of Done

- U1: `project.branch_prefix` loads, defaults, and validates as specified.
- U2: `task_branch_for` is the only concatenator. Brief and Runner agree for the default prefix, an override, and the empty prefix. Preflight, `local_merge_tail`, verify, and `--retry-blocked` are each proven against a custom prefix, not only the default.
- U3: skill resume wording is prefix agnostic. Authoring asks for the prefix. Example Manifests show the commented field. The `gitwrite` module docstring and the Task process sentence in `CONCEPTS.md` match.
- The full suite passes.
- No new halt class.
- No tracker write from the Runner.
- Historical fixtures that mention `relay/` as captured output are unchanged.
