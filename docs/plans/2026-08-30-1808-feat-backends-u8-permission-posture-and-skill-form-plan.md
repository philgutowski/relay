---
title: Backends U8, Permission Posture and Skill Form - Plan
type: feat
date: 2026-08-30
origin: docs/plans/2026-08-28-1101-feat-pluggable-cli-backends-plan.md section "U8. Permission posture and skill form"; tracker task relay task 23, part of #16
artifact_contract: ce-unified-plan/v1
artifact_readiness: implementation-ready
product_contract_source: ce-plan-bootstrap
execution: code
---

# Backends U8, Permission Posture and Skill Form - Plan

## Goal Capsule

- **Objective:** A Task or a Closeout running on Codex or Grok is told to call
  the plugin's skills in a form that CLI can actually resolve, and a backend
  that cannot refuse a tool call at launch carries the run's restrictions in
  its Brief instead.
- **Means:** Route all four skill-invocation sites through
  `backends.build(<backend>).qualify_skill()`, replace the Claude-specific
  prefix sentence in the two Task templates with a rendered value, and add a
  per-backend unenforced-restriction insert. Do not fork the templates (KTD3
  of the parent plan).
- **Product authority:** `docs/plans/2026-08-28-1101-feat-pluggable-cli-backends-plan.md`,
  section `### U8. Permission posture and skill form`, requirements R4, R5
  (its Brief half only) and R10, key decisions KTD3, KTD6, KTD15.
- **Execution profile:** Small and surgical. Four modules, two templates, three
  test modules. No new halt class, no new manifest field, no change to what
  the launcher runs.
- **Stop conditions:** Stop if `brief-closeout.md` turns out to need a new
  placeholder, because a new placeholder there breaks the resident Runner's
  Closeout for this very task (KTD4). Stop if `qualify_skill` turns out not to
  be reachable at a site that *builds* an invocation string, rather than
  reading `CAPABILITY.skill_form` directly at that site. The one authorized
  direct read is KTD3's already-qualified test in `classify`, which asks a
  question no interface callable answers.
- **Tail ownership:** The calling process owns commit and the project gate.

---

## Product Contract

### Summary

The parent plan's U8 has four approach steps. Two of them already landed and
are verified present on `main` at `d01e3a7`:

**Parent approach step 1. Per-backend permission mode and a forbidden-mode
   tuple** landed with the parent plan's U4 and U5.
   `contracts.BACKEND_PINS` carries `permission_mode` and
   `forbidden_permission_modes` per backend (`contracts.py:147`, `:176`,
   `:219`), and `launch._reject_forbidden` (`launch.py:117`) walks the whole
   tuple for both the argv and the allow and deny lists. `tests/test_launch.py`
   already proves the refusal for every spelling.
**Parent approach step 4. Declaring skill substitution undetectable** landed
   with the parent plan's U6.
   `backends/codex.py:20` and `backends/grok.py:21` both put
   `contracts.HALT_SKILL_SUBSTITUTION` in their `_UNDETECTABLE` set, so
   `classify` records "not checked" rather than "checked, none found" on those
   backends.

Parent approach steps 2 and 3 have not landed, and they are what this plan
builds. It also adds two things the parent's approach steps do not name: R6,
removing the constant that goes dead when step 2 lands, and KTD2's single
backend value on the Closeout seam.

**The four skill-invocation sites are all still Claude-only.**
`brief._qualified()` (`brief.py:97`), `closeout.compound_command()`
(`closeout.py:140`), the `compound_skill` value at `closeout.py:185`, and
`classify.required_skill_for()` (`classify.py:86`) each build
`contracts.SKILL_PREFIX + name`. `backends.qualify_skill()` exists and is on
`backends.INTERFACE`, returning `compound-engineering:ce-plan` on claude,
`$ce-plan` on codex, and `/ce-plan` on grok
(`backends/claude.py:81`, `codex.py:145`, `grok.py:175`), but nothing outside
`tests/test_backends.py` calls it. A Codex Closeout today would be handed
`compound-engineering:ce-compound`, which that CLI does not resolve, and the
compound judgment would fail on every non-Claude Task.

**The templates carry Claude vocabulary as static prose.**
`brief-local-merge.md` and `brief-pr-terminal.md` both say "Never invoke a
Skill whose name lacks the `compound-engineering:` prefix." That sentence is
false on codex and grok, which have no prefix at all. There is no
unenforced-restriction insert in any template.

**`contracts.SKILL_PREFIX` goes dead when the four sites move.** After this
plan the only readers are tests. `contracts.PLUGIN_NAME` already carries
`compound-engineering`, and `BACKEND_PINS` carries the claude form as
`"compound-engineering:%s"` under a comment that reads "Do not restate these
values elsewhere" (`contracts.py:121`). Commit `1e115e4` (relay task 18)
removed `contracts.PERMISSION_MODE` for exactly this reason one unit earlier,
so the precedent is set.

### Problem Frame

Relay resolves the CLI per Task at the launch seam, but three of the four
places that spell a skill invocation, and both Task Brief templates, still
assume Claude. A Task or Closeout on codex or grok is therefore told to run a
command its CLI cannot parse. Separately, codex cannot refuse a tool call at
launch (`enforces_at_launch` is `False`, `contracts.py:186`), and nothing yet
tells a codex Task what the run's disallow list says, so the restriction
reaches that process in no form at all.

### Requirements

- R1. Every Task Brief names plugin skills in the invocation form of the
  backend that Task will run on, in the `local_merge` template and the
  `pr_terminal` template alike. (parent R4, KTD15)
- R2. The Closeout Brief's pinned compound invocation, and the `compound_skill`
  name in the same Brief, are in the invocation form of the CLI that will read
  that Brief. (parent R5, KTD15)
- R3. `classify.required_skill_for` decides "already qualified" and builds the
  name it should have been from the backend's own form, so a substitution
  finding names an invocation the operator could actually run. A name counts as
  already qualified only when it carries that backend's form *and* the
  remainder is one of the plugin skills the Brief pins, so a bare sigil shared
  by every skill the CLI can run is not mistaken for plugin ownership.
  (parent R4, KTD15)
- R4. A Task Brief for a backend that cannot enforce restrictions at launch
  carries both halves of the run's launch posture as instructions: the tools
  the manifest allows, and the resolved disallow list. A Brief for a backend
  that does enforce carries no such instruction. (parent R10)
- R5. Every rendered Brief still carries the envelope fence tag, the three
  status values, and the ordered pipeline steps, on every backend. The
  templates are not forked. (parent KTD3)
- R6. `contracts.SKILL_PREFIX` is removed once nothing outside tests reads it.
- R7. No behavior change for a `claude` Task, with one named exception. A
  Brief, a Closeout Brief, and a substitution finding rendered for claude read
  exactly as they do today apart from the reworded skill-form sentence. The
  exception is R3's tightened test: on claude, a call to
  `compound-engineering:<bare name>` for a plugin skill the prefix does not
  actually name, such as `compound-engineering:code-review`, becomes a
  substitution finding where today it is silently accepted. That is the
  intended correction, not a regression, since the plugin ships no such skill.

### Scope Boundaries

- Out of scope: recording which restrictions went unenforced on the Task
  record. The parent plan gives that to U10 step 2 ("Record which restrictions
  went unenforced on the Task record, as a plain scalar so a Cause line can
  render it"), together with the landing bound and the evidence audit. This
  unit owns only R10's Brief half.
- Out of scope: making the Closeout actually run on the Task's backend. That
  is the parent plan's U9. This plan adds the parameter and defaults it to
  today's value, so U9 is a one-line change at the caller.
- Out of scope: validation changes for an unenforced backend, such as the
  acceptance sentence and the required Task path bound. Parent U10, R19.
- Out of scope: `contracts.BACKEND_PINS` values, `launch.build_args`, and
  `_reject_forbidden`. Approach step 1 of the parent U8 is already satisfied
  there and this plan does not touch it.
- Out of scope: the `_UNDETECTABLE` sets in `backends/codex.py` and
  `backends/grok.py`. Approach step 4 is already satisfied.
- Out of scope: a live run against a throwaway target. `CLAUDE.md` requires one
  after a change to a brief template, and no process inside a Relay run can
  discharge it. It is named in the Verification Contract as an operator
  obligation.

---

## Planning Contract

### Key Technical Decisions

- KTD1. **Resolve every form through the interface callable, not through the
  pin.** All four sites call `backends.build(<backend>).qualify_skill(<name>)`
  rather than reading `CAPABILITY.skill_form` and interpolating. `qualify_skill`
  is on `backends.INTERFACE` and is pinned by
  `tests/test_backends.py:202`, so one function is the single producer of the
  string and a backend that later needs a non-interpolation form has one place
  to change. Governs R1, R2, R3.
- KTD2. **The Closeout's three backend consumers come from one parameter, and
  only `run()` may default it.** `closeout.run()` takes a single `backend`
  argument and passes it to `render()`, to `_closeout_task()`, and to the
  `classify.classify()` call it makes over its own transcript, so the
  invocation the Brief pins, the CLI that reads it, and the normalizer that
  reads what it wrote cannot disagree. That is the failure KTD15 of the parent
  plan exists to prevent, and a second independent default is how it would come
  back, so `backend` is a **required** argument on `compound_command()`,
  `render()`, and `_closeout_task()`. Only `run()` carries a default, and only
  because that is what leaves `run.py` unchanged until U9. Governs R2, R7.
- KTD3. **`required_skill_for` derives the "already qualified" test from the
  backend's own `skill_form` and from `REQUIRED_SKILLS` together.** Partition
  the form on `%s`, test the resulting prefix and suffix, **and** require the
  remainder to be one of the plugin skills the Brief pins. The prefix test
  alone is not enough: codex's form is `$%s` and grok's is `/%s`, bare sigils
  every skill on those CLIs shares, so a prefix-only test would classify
  `$code-review` and `/code-review`, the harness skills this classifier exists
  to catch, as already qualified. It also handles a hypothetical suffixed form,
  which the prefix-only test would misread in the other direction. This one
  site reads `CAPABILITY.skill_form` directly because there is no interface
  callable that answers "is this string already in your form"; every site that
  *builds* a form still goes through KTD1. Governs R3, and R7's named
  exception.
- KTD4. **No new placeholder goes into `brief-closeout.md` in this commit.**
  The general rule it instances: a template placeholder and the module value
  feeding it must not land in a commit a resident Runner will read across.
  `docs/solutions/workflow-issues/change-spanning-a-live-template-and-a-frozen-module-breaks-the-landing-run.md`
  establishes that a Runner holds `skills/relay/scripts/relay/` frozen at
  import while reading `skills/relay/templates/` live, so a template that gains
  a placeholder before the resident module supplies it raises `KeyError` at the
  next render. The resident Runner for this task was observed directly: PID
  14768, `relay_cli.py run ~/.relay/manifests/relay-round6-a.toml`, launched
  from this repository rather than from a plugin cache copy, so its
  `TEMPLATE_DIR` follows the merge. That manifest lists task 23 last, so no
  later Task Brief will be rendered by it, but it *will* render
  `brief-closeout.md` for this task after the merge. A new placeholder there
  would cost this task its Closeout; new placeholders in the two Task templates
  cost nothing in this run. **This constraint expires when that process
  exits.** A later unit that legitimately needs a Closeout placeholder, U9 or
  U10 most likely, lands it in a commit no resident Runner reads across, not
  never. One hazard the same reasoning surfaces:
  `~/.relay/manifests/relay-round6-b.toml` exists with later tasks and no live
  process, and a Runner launched from it **before** this merge would freeze the
  pre-merge `brief.py` against post-merge templates and raise on every Task
  Brief it rendered. Launch it after the merge, not across it. Governs the file
  list and U4's execution note.
- KTD5. **Remove `contracts.SKILL_PREFIX` rather than leave it as a legacy
  default.** After U1 to U3 nothing outside tests reads it, its value is a
  second copy of `BACKEND_PINS["claude"]["skill_form"]`, and the pins block
  says not to restate its values elsewhere. Leaving a constant that looks
  authoritative next to a table that actually is authoritative is how a fifth
  call site gets written. `contracts.FORBIDDEN_PERMISSION_MODE` is a different
  name and stays, since `manifest.py:406` still reads it. Governs R6.

### Assumptions

- The insert carries both halves of the launch posture, because codex's pins
  set `allow_flag` and `deny_flag` both to `None` and `backends/codex.py`'s
  `build_args` accepts `allowed` and never puts it on the argv. So the allow
  list is as unenforced as the deny list, and parent R10's "a restriction the
  manifest names" covers it. The two sources are
  `manifest.permissions.allowed` and
  `manifest_module.resolved_disallowed(manifest)`, the latter being the
  manifest's own disallow list plus every R10 variant `validate` filled in.
- Only codex renders the insert today. `enforces_at_launch` is `True` for
  claude and grok and `False` for codex (`tests/test_backends.py:69`), so the
  claude and grok Briefs are unchanged by U4.
- Two brief tests move as part of this work rather than as regressions.
  `tests/test_brief.py:65` asserts the old prefix sentence by regex and is
  rewritten to the new rule. `tests/test_brief.py:54` walks every
  `REQUIRED_SKILLS` name in the rendered Brief and asserts the characters
  before it are the qualified prefix; it is rewritten to assert the same
  invariant per backend and is **never deleted**. It is the only guard keeping
  an unqualified plugin skill name out of a Brief, which is the R43 rule the
  2026-08-25 proof run motivated.

---

## Implementation Units

### U1. Backend-resolved skill form in the Task Brief

- **Goal:** A Task Brief names every plugin skill in the form its own backend
  resolves, and its skill-form rule states that form instead of Claude's
  prefix.
- **Requirements:** R1, R5, R7; KTD1, KTD4.
- **Dependencies:** None.
- **Files:** `skills/relay/scripts/relay/brief.py`,
  `skills/relay/templates/brief-local-merge.md`,
  `skills/relay/templates/brief-pr-terminal.md`, `tests/test_brief.py`.
- **Approach:**
  1. In `brief.values()`, resolve `backends.build(task.backend)` once and build
     `ce_plan`, `ce_work`, `ce_simplify`, `ce_review`, and `ce_lfg` from
     `module.qualify_skill(...)`. Delete `_qualified()`; it has no other
     caller.
  2. Add a module-level `SKILL_FORM_RULE` template beside `PARTIAL_ALLOWED` and
     `FOLLOWUP_ALLOWED`, carrying one substitution for this backend's own form
     of one named skill. Two constraints on the wording, both load bearing.
     **It may contain no bare `contracts.REQUIRED_SKILLS` token**, only the
     rendered form, or it fails the guard at `tests/test_brief.py:54` that
     keeps unqualified plugin skill names out of Briefs. **It may not claim
     the call is recorded**, because codex and grok declare
     `HALT_SKILL_SUBSTITUTION` undetectable and nothing is recorded there.
     Directional shape, not final wording: "Invoke every plugin skill in this
     CLI's own form, exactly as the steps below spell it. The plugin's planning
     skill is `<rendered form>` here. The harness ships skills with similar
     bare names and they are not substitutes for the plugin's; a call in any
     other form is a failure of this task." Supply it as the `skill_form_rule`
     value.
  3. Replace the hardcoded prefix paragraph in both templates with
     `$skill_form_rule`. Change nothing else in either template under this
     unit.
- **Patterns to follow:** The existing constants in `brief.py:50` to `:65`,
  which hold the prose and let the template hold only the placeholder.
  `values()` stays deterministic for a given manifest. Its one filesystem touch
  after U4 is the `os.path.isdir` inside the `validate()` pass
  `resolved_disallowed` performs, which the launcher already runs per task at
  `launch.py:134`, so the determinism claim in the module docstring still
  holds.
- **Test scenarios:**
  - A Brief rendered for each of claude, codex, and grok names that backend's
    form for all five skills and none of the other two backends' forms, in the
    `local_merge` template and in the `pr_terminal` template.
  - The claude Brief still orders the pipeline steps plan, work, simplify,
    review, and still carries the branch name before the first step.
  - Every rendered Brief on every backend carries the envelope fence tag, all
    three status values, and the `blockers`, `changed_files`, `plan_path`, and
    `learnings` keys.
  - A Brief for each backend carries the skill-form rule sentence and names
    that backend's form inside it, and no rendered Brief on any backend claims
    a wrongly formed call will be recorded.
  - The rewritten `tests/test_brief.py:54` guard still holds per backend: every
    `REQUIRED_SKILLS` name appearing in a rendered Brief is immediately
    preceded by that backend's own prefix.
  - Two renders of the same inputs are byte identical, on a non-claude backend
    as well as on claude.
- **Verification:** `python3 -m unittest test_brief` passes, and a codex Brief
  diffed against the claude one differs only in the skill-form lines.

### U2. Backend-resolved skill form in the Closeout Brief

- **Goal:** The compound invocation a Closeout Brief pins, and the skill name
  the same Brief mentions, are both in the form of the CLI that will read it.
- **Requirements:** R2, R7; KTD1, KTD2, KTD4.
- **Dependencies:** U1 is not required, but both change the same seam and this
  one is easier to review after it.
- **Files:** `skills/relay/scripts/relay/closeout.py`, `tests/test_closeout.py`.
- **Approach:**
  1. Give `compound_command(depth, hint)` a **required** `backend` parameter
     and build its leading token from
     `backends.build(backend).qualify_skill("ce-compound")`. The
     `mode:non-interactive`, depth, and hint parts are plugin contracts and do
     not move.
  2. Give `render()` a required `backend` parameter, supply `compound_skill`
     from the same `qualify_skill` call, and give `_closeout_task()` a required
     `backend` parameter set on the `Task` it builds.
  3. Give `run()` the only `backend` parameter that carries a default,
     `manifest_module.DEFAULT_BACKEND`, and pass it to all three of `render()`,
     `_closeout_task()`, and the `classify.classify()` call at
     `closeout.py:253`. That third consumer is easy to miss and is the one that
     bites: a Closeout launched on codex whose transcript is normalized as
     claude decodes nothing, so `parse()` sees no terminal line and every run
     appends a `CLOSEOUT_UNFINISHED` finding. `run.py:370` already shows the
     shape, `classify.classify(..., backend=task.backend)`.
  4. Do not change `run.py`; the caller keeps `run()`'s default until U9.
  5. Add no placeholder to `brief-closeout.md`. `$compound_command` and
     `$compound_skill` already exist and only their values change (KTD4).
- **Patterns to follow:** `closeout.run()`'s existing keyword-argument style,
  and `launch.cli_version`'s `backend="claude"` default as the shape of a
  per-backend parameter added without moving a caller. Note the asymmetry KTD2
  requires: `run()` follows that pattern, the three functions below it do not.
- **Test scenarios:**
  - `compound_command` returns each backend's form when asked, with
    `mode:non-interactive`, the depth, and the hint unchanged in each.
  - A Closeout Brief rendered for each backend contains that backend's compound
    invocation and neither of the other two forms.
  - The Closeout Brief for each backend still ends on the two terminal lines
    and still carries the allowed-paths list and the do-not-push instruction.
  - `run()` passing a backend gives `_closeout_task` the same backend the
    Brief was rendered for, proven from the Task record the launcher receives
    rather than from the Brief text.
  - `run()` passing a backend gives `classify.classify` that same backend,
    proven from the classifier call rather than from the digest, whose key set
    is identical whether the value took effect or not.
- **Verification:** `python3 -m unittest test_closeout` passes, and a codex
  Closeout Brief diffed against the claude one differs only in the two
  invocation strings.

### U3. Backend-resolved substitution detection

- **Goal:** A substitution finding on any backend names the invocation the
  process should have used on that backend, and a correctly qualified call on
  a non-claude backend is not reported as a substitution.
- **Requirements:** R3, R7; KTD1, KTD3.
- **Dependencies:** None.
- **Files:** `skills/relay/scripts/relay/classify.py`, `tests/test_classify.py`.
- **Approach:**
  1. Give `required_skill_for(skill_name)` a `backend` parameter defaulting to
     `"claude"`, matching `classify.classify`'s existing default.
  2. Replace the `skill_name.startswith(contracts.SKILL_PREFIX)` early return
     with the two-part test from KTD3: the name carries that backend's
     `skill_form` prefix and suffix, partitioned on `%s`, **and** the remainder
     is in `contracts.REQUIRED_SKILLS`. Only then is it already qualified and
     returns `None`. The prefix alone is not sufficient on codex or grok, whose
     forms are bare sigils every skill on those CLIs shares.
  3. Build the returned name from `module.qualify_skill(required)` rather than
     from a prefix concatenation.
  4. Pass `backend` through from `classify.classify` at its one call site
     (`classify.py:205`).
  5. Confirm the tightening does not produce a false positive on a real
     non-pipeline plugin skill: `compound-engineering:ce-debug` fails the
     remainder test, falls through to the bare-name loop, matches nothing in
     `REQUIRED_SKILLS`, and still returns `None`. The one changed claude
     outcome is `compound-engineering:code-review`, R7's named exception.
- **Patterns to follow:** `classify.classify`'s own `backend="claude"`
  parameter and its `backends.build(backend)` resolution.
- **Test scenarios:**
  - On claude, the existing mappings hold: `code-review` maps to
    `compound-engineering:ce-code-review`, `ce-work` to
    `compound-engineering:ce-work`, `lfg` to `compound-engineering:lfg`, and an
    already-qualified name and an unrelated skill both return `None`.
  - On codex, `code-review` maps to `$ce-code-review` and `$ce-work` returns
    `None`.
  - On grok, `code-review` maps to `/ce-code-review` and `/ce-work` returns
    `None`.
  - On codex, `$code-review` maps to `$ce-code-review` rather than returning
    `None`, and the same on grok for `/code-review`. This is the case a
    prefix-only test would miss, and it pins KTD3's second half.
  - On claude, `compound-engineering:code-review` becomes a substitution
    finding naming `compound-engineering:ce-code-review`, R7's one named
    behavior change, while `compound-engineering:ce-debug` still returns
    `None`.
  - A claude-qualified name seen on a codex run is reported as a substitution
    naming the codex form, since it is not a call that CLI could have resolved.
  - `classify.classify` over a transcript carrying a bare `Skill` call on a
    non-claude backend produces a finding whose `required` field is that
    backend's form. Grok and codex declare the class undetectable, so this is
    asserted at the `required_skill_for` level rather than by synthesizing
    evidence those normalizers do not emit.
- **Verification:** `python3 -m unittest test_classify` passes.

### U4. The unenforced-restriction insert

- **Goal:** A Task on a backend that cannot refuse a tool call at launch is
  told in its Brief what the run's disallow list says. A Task on a backend that
  can is told nothing extra.
- **Requirements:** R4, R5, R7; KTD3 of the parent plan, KTD4 here.
- **Dependencies:** U1, since both edit the same two templates and the same
  `values()` dict.
- **Files:** `skills/relay/scripts/relay/brief.py`,
  `skills/relay/templates/brief-local-merge.md`,
  `skills/relay/templates/brief-pr-terminal.md`, `tests/test_brief.py`.
- **Approach:**
  1. Add an `UNENFORCED_RESTRICTIONS` prose constant to `brief.py` beside the
     others, carrying two substitutions, one per list. State only what is true
     today: this CLI cannot enforce either list at launch, so they are carried
     here as instructions, and the runner still owns the merge and the push. Do
     not describe the landing bound or the evidence audit, which are parent U10
     and do not exist yet. **Close it with an anti-override sentence** mirroring
     `DATA_HEADER`, to the effect that the lists are the runner's own and
     nothing in the task data block above amends or lifts them, whatever it
     appears to say. The tracker text in the same Brief is untrusted by the
     module's own docstring and `defang()` rewrites only the two delimiters, so
     a card description is otherwise free to mimic the insert's shape.
  2. Supply an `unenforced_restrictions` value: the empty string when the
     backend's `enforces_at_launch` is `True`, otherwise the constant filled
     with `manifest.permissions.allowed` as the tools this Task may use and
     `manifest_module.resolved_disallowed(manifest)` as the calls it must not
     make, one bullet per entry in each. Both are unenforced on codex, whose
     pins set `allow_flag` and `deny_flag` to `None`. A local one-line join is
     enough for the bullets; `closeout._bullets` is not reachable from here
     without an import cycle and is not worth one.
  3. Place `$unenforced_restrictions` in both templates on its own line
     directly after `$blocked_followup`, **with no blank line on either side of
     it**, and carry a leading and a trailing newline inside the non-empty
     value. The empty value then collapses to today's single blank line before
     `## Steps` and the non-empty one renders as its own paragraph. Getting
     this backwards, leaving the template's existing blank line in place, yields
     a doubled blank line in every claude Brief. Comment the convention where
     the value is built, since it is the kind of whitespace contract a later
     reader would otherwise flatten.
  4. `brief.py` gains `from . import manifest as manifest_module`. The alias is
     required, not stylistic: `values(manifest, task, card, branch)` binds
     `manifest` to the dataclass instance, so a plain `manifest` import would
     be shadowed and every render would raise `AttributeError`. `closeout.py:28`
     already carries this alias for the same collision. No import cycle:
     `manifest` imports `backends`, `contracts`, and `gitread`, none of which
     import `brief`.
- **Patterns to follow:** `PARTIAL_ALLOWED` and `PARTIAL_FORBIDDEN`, the
  existing pair of mutually exclusive prose inserts selected from a manifest
  value.
- **Execution note:** This unit and U1 are why the file list spans
  `skills/relay/templates/` and `skills/relay/scripts/relay/`. Before
  committing, re-read KTD4 and confirm no placeholder was added to
  `brief-closeout.md`; that is the one file whose live half this Runner still
  renders after the merge.
- **Execution note:** This is the first time manifest-authored text reaches the
  rendered Brief, and `run.py:348` runs `brief.scan` over that Brief. A
  manifest whose `permissions.disallowed` or `permissions.allowed` named a
  `.claude/` path would make every Task on a non-enforcing backend self-exclude,
  because `_paths_in` cannot tell the insert from tracker text. No entry in
  `contracts.DISALLOWED_TOOLS` contains `.claude/` today, so this only fires on
  an operator-written pattern. Do not add a carve-out for it; note it and let a
  real occurrence be the trigger.
- **Test scenarios:**
  - A codex Brief carries the unenforced-restriction instruction, every entry
    of `permissions.allowed`, and every pattern from the resolved disallow
    list, in the `local_merge` template and in the `pr_terminal` template.
  - A claude Brief and a grok Brief carry neither the instruction nor any
    disallow pattern.
  - A codex Brief rendered from a card whose description mimics the insert's
    shape, a heading plus a list, or asserts the run has no restrictions, still
    carries the real insert and its anti-override sentence verbatim.
  - A claude Brief rendered after this unit is byte identical to one rendered
    before it, so the empty case adds no whitespace. Assert this as the absence
    of a doubled blank line at the insertion point rather than against a stored
    fixture.
  - A codex Brief still carries the envelope fence tag, the three status
    values, and the ordered pipeline steps.
- **Verification:** `python3 -m unittest test_brief` passes, and a codex Brief
  diffed against the claude one differs only in the skill-form lines from U1
  and this insert.

### U5. Remove the dead SKILL_PREFIX constant

- **Goal:** `contracts.py` carries no constant that restates a
  `BACKEND_PINS` value and that no production code reads.
- **Requirements:** R6.
- **Dependencies:** U1, U2, U3. All three must have moved off it first.
- **Files:** `skills/relay/scripts/relay/contracts.py`, `tests/test_brief.py`,
  `tests/test_closeout.py`, `tests/test_classify.py`.
- **Approach:**
  1. Grep `skills/` and `tests/` for `SKILL_PREFIX`. Every remaining hit should
     be a test; move each to a **literal** (`"compound-engineering:ce-plan"`
     and so on), never to `backends.build("claude").qualify_skill(...)`. A test
     that resolves its expected value through the same call the code under test
     uses passes for any value of the pin, including a wrong one, which would
     delete the last guard on the claude invocation string in the same commit
     that deletes the constant. `tests/test_backends.py:203` already pins the
     literal and is the pattern. This repo's own
     `docs/solutions/logic-errors/stubbed-seams-agree-by-construction-first-live-run-found-five-contract-defects.md`
     is the record of what that costs. A hit in `skills/` means a call site was
     missed in U1 to U3 and is the signal to fix that unit rather than to keep
     the constant.
  2. Delete `SKILL_PREFIX` and reword the comment above it so the surviving
     text about `REQUIRED_SKILLS` and the substitution rule still reads, with
     the naming fact pointed at `BACKEND_PINS`.
  3. Leave `FORBIDDEN_PERMISSION_MODE` untouched; `manifest.py:406` reads it.
- **Patterns to follow:** Commit `1e115e4`, which removed
  `contracts.PERMISSION_MODE` the same way and for the same reason.
- **Test scenarios:** The full suite is the check. A test importing
  `contracts.SKILL_PREFIX` fails at attribute access and is the signal that
  step 1 was incomplete.
- **Verification:** `grep -rn "SKILL_PREFIX" skills/ tests/` returns nothing,
  and `python3 -m unittest discover -s tests` passes.

---

## Verification Contract

| Gate | Evidence |
| --- | --- |
| Regression suite | `python3 -m unittest discover -s tests` passes from the repo root. |
| Pinned strings still trace | The parent unit's "pinned-string test passes against the plugin as installed" scenario is discharged by `test_contracts.PinsTraceToSource`, which reads the plugin source and is CLI-independent. No new pin is added, so nothing here changes it. |
| Unit verification line | `python3 -m unittest test_brief test_contracts` passes, the parent plan's stated line for U8. |
| Four sites moved | `grep -rn "SKILL_PREFIX" skills/` returns nothing, so no site builds a Claude-only form. |
| Per-backend Brief diff | A rendered Brief for codex, diffed against the claude one, differs only in the skill-form lines and the unenforced insert. A rendered Brief for grok differs only in the skill-form lines. |
| Closeout Brief diff | A rendered Closeout Brief for codex, diffed against the claude one, differs only in `$compound_command` and `$compound_skill`. |
| Closeout placeholder set unchanged | `git diff` on `skills/relay/templates/brief-closeout.md` is empty for this commit (KTD4). The constraint is per-commit, not permanent. |
| Claude Brief whitespace | A claude Brief rendered after U4 has no doubled blank line where the new placeholder sits. |
| Live run obligation | Not dischargeable inside a Relay run. `CLAUDE.md` requires one live task against a throwaway target after a brief-template change; this plan records it as an operator obligation and the envelope names it. |

---

## Definition of Done

- `brief.values()`, `closeout.compound_command()`, the `compound_skill` value in
  `closeout.render()`, and `classify.required_skill_for()` all resolve the
  invocation form through `backends.build(<backend>).qualify_skill()`.
- `closeout.run()` passes one backend value to `render()`, `_closeout_task()`,
  and its own `classify.classify()` call, defaulting to today's value so
  `run.py` is unchanged. The three functions below it require the argument.
- Both Task templates carry `$skill_form_rule` and `$unenforced_restrictions`
  and no Claude-specific prefix prose. `brief-closeout.md` is unchanged.
- A codex Brief carries both the manifest's allow list and the resolved
  disallow list, with the anti-override sentence; a claude Brief and a grok
  Brief carry neither list.
- `contracts.SKILL_PREFIX` is removed.
- The full test suite passes.
