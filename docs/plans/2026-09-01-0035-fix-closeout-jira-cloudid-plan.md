---
title: Closeout Jira cloudId Without Discovery Tool - Plan
type: fix
date: 2026-09-01
artifact_contract: ce-unified-plan/v1
artifact_readiness: implementation-ready
product_contract_source: ce-plan-bootstrap
execution: code
---

# Closeout Jira cloudId Without Discovery Tool - Plan

## Goal Capsule

**Objective:** a Jira tracked Closeout process can comment and transition the card in a headless run, because it already knows which site to pass as `cloudId`.

**Means:** put the Manifest tracker site into `JiraAdapter.closeout_instructions`, which already fills `$duty_one` in the Closeout brief (KTD1). Do not add `getAccessibleAtlassianResources` to `CLOSEOUT_TOOLS` (KTD2).

**Authority hierarchy:** this plan's Key Technical Decisions; then `CLAUDE.md` (halt classes stay closed, the Runner never writes the tracker, Closeout tool surface stays least privilege); then outer loop KTD16 (each adapter owns tracker knowledge so Atlassian names do not leak into the shared Closeout template).

**Stop conditions:** the unittest suite passes; `CLOSEOUT_TOOLS` is still exactly the four existing Jira tools; a rendered Jira Closeout brief names the tracker site as `cloudId` and forbids the discovery tool.

**Execution profile:** one session, both units together.

**Tail ownership:** the calling Relay Task process runs the project gate and lands the branch. This plan does not describe shipping.

## Product Contract

### Summary

Give the Jira Closeout process the tracker site it already needs as `cloudId`, through the brief it already reads. Keep its tool allowlist at the four write and read tools. Do not grant a discovery call.

### Problem Frame

Every Atlassian MCP write needs a `cloudId`. The Runner already has the site in Manifest `[tracker] site`. The Closeout process does not. On the 2026-08-31 support-workbench overnight run against IW-146, Closeout called `getAccessibleAtlassianResources` to find it. That tool is not in `CLOSEOUT_TOOLS` in `skills/relay/scripts/relay/adapters/jira.py`, and the project's IW gate hook does not match it either, so headless `dontAsk` denied it silently. Every later Jira call failed for lack of a `cloudId`. The classifier recorded `tracker_write_denied`. The card got no comment. The Atlassian tool description already says a site hostname usually works as `cloudId` with no discovery call.

### Key Decisions

- Keep the Closeout Jira tool surface at the four existing tools. Do not add `getAccessibleAtlassianResources`. (session-settled: user-directed — chosen over granting the discovery tool: least privilege, the Runner already knows the site) Governs R3.

### Requirements

**Brief carries the site**

- R1. Every `JiraAdapter.closeout_instructions` outcome (landed, blocked, halted) names the adapter's tracker site and tells the process to pass that hostname as `cloudId` on every Atlassian MCP call.
- R2. Those same instructions tell the process never to call `getAccessibleAtlassianResources`.
- R3. `CLOSEOUT_TOOLS` stays exactly `getJiraIssue`, `getTransitionsForJiraIssue`, `transitionJiraIssue`, and `addCommentToJiraIssue` under the `mcp__atlassian__` prefix. It does not gain `getAccessibleAtlassianResources`.
- R4. GitHub and markdown `closeout_allowed_tools` and `closeout_instructions` stay as they are.

**Proof**

- R5. Tests prove a Jira Closeout brief (the substituted `$duty_one` text) carries the site as `cloudId`, and that the Jira Closeout tool list does not grow.

### Success Criteria

- SC1. `JiraAdapter.closeout_instructions` for landed, blocked, and halted contains `example.atlassian.net`, the `cloudId` instruction, and the sentence that forbids `getAccessibleAtlassianResources`. A landed `closeout.render` brief against that adapter contains the hostname.
- SC2. `JiraAdapter.closeout_allowed_tools()` returns exactly the four current names and not `mcp__atlassian__getAccessibleAtlassianResources`.
- SC3. `python3 -m unittest discover -s tests` passes.

### Scope Boundaries

- In scope: `JiraAdapter.closeout_instructions`, the existing `$duty_one` substitution, and tests in `tests/test_adapters.py` plus a Jira render case in `tests/test_closeout.py`.
- Out of scope: a new placeholder in `skills/relay/templates/brief-closeout.md`. A new name there would split the live template from the frozen `closeout.py` module on this self hosted run, and it would put Atlassian vocabulary in the shared brief (KTD16).
- Out of scope: adding `getAccessibleAtlassianResources` to `CLOSEOUT_TOOLS` or to `FakeAdapter`.
- Out of scope: Task process briefs. The card names Closeout only.
- Out of scope: removing the support-workbench Manifest band-aid. That is the operator's call.
- Out of scope: a new halt class.

### Deferred to Follow-Up Work

- Removing `[closeout] allowed_tools = ["mcp__atlassian__getAccessibleAtlassianResources"]` from support-workbench's overnight Manifest, once this lands.

## Planning Contract

### Key Technical Decisions

- KTD1. **Put the site in `JiraAdapter.closeout_instructions`, not in a new Closeout template placeholder.** `$duty_one` already renders adapter text into the brief. The adapter already holds `self._site`. A new `$tracker_site` placeholder would require a paired `closeout.py` values key. On a self hosted run the template is live and the module is frozen, so that pair raises `BriefError` on the landing Closeout (`docs/solutions/workflow-issues/change-spanning-a-live-template-and-a-frozen-module-breaks-the-landing-run.md`). Outer loop KTD16 also forbids Atlassian names in the shared template. The card named `brief-closeout.md` and `closeout.py`; this plan keeps the brief-level fix (the rendered brief carries the site) and drops the new placeholder. Governs R1, R2, R5.
- KTD2. **`CLOSEOUT_TOOLS` is unchanged.** The four tools already cover read, transition, and comment. Discovery is unnecessary once R1 is true. Governs R3.
- KTD3. **`FakeAdapter` gains no new method.** The KTD16 interface already has `closeout_instructions`. Tests that need a site in the brief pass it through that existing method, or use a real `JiraAdapter`. Governs R5.
- KTD4. **Pass the Manifest site hostname as `cloudId`, not a scheme-prefixed URL.** `[tracker] site` is a hostname. The Jira REST client already prepends `https://` for its own calls. The Closeout instruction names that same hostname. Governs R1.

### Assumptions

- A1. A Jira Cloud site hostname, the Manifest `[tracker] site` value with no scheme, works as `cloudId` on `mcp__atlassian__*` calls, as the discovery tool's own description and the issue body both state. The adapter already stores that hostname in `self._site`. Not revalidated against a live MCP call here.
- A2. A Jira Manifest always has `tracker.site` (validate already requires it), so Closeout instructions never have to invent a fallback for an empty site.
- A3. The GitHub Closeout path is already working in this run (Bash `gh`), so leaving its instructions untouched is correct.

### Implementation Constraints

- Halt classes stay a closed set.
- The Runner still never writes the tracker.
- Python 3 standard library only.
- Prose in this repo uses no dashes of any kind.

### Sources

- Issue 56 body (IW-146 overnight, 2026-08-31).
- `skills/relay/scripts/relay/adapters/jira.py` (`CLOSEOUT_TOOLS`, `self._site`, `closeout_instructions`).
- `skills/relay/scripts/relay/closeout.py` (`render` values, `$duty_one`).
- Outer loop plan KTD16 (`docs/plans/2026-08-25-1346-feat-relay-outer-loop-plan.md`).
- `docs/solutions/workflow-issues/change-spanning-a-live-template-and-a-frozen-module-breaks-the-landing-run.md`.

## Implementation Units

### U1. Jira Closeout instructions name the site as cloudId

**Goal:** every Jira Closeout brief tells the process the tracker site and forbids the discovery tool.

**Requirements:** R1, R2, R4

**Dependencies:** none

**Files:**
- `skills/relay/scripts/relay/adapters/jira.py`

**Approach:**
1. Keep `CLOSEOUT_TOOLS` as the current four-tuple.
2. Extend `closeout_instructions` so each of the three outcome strings still says what to write, then names `self._site` as the `cloudId` to pass on every Atlassian call, and says not to call `getAccessibleAtlassianResources`.
3. Leave GitHub and markdown adapters untouched.

**Patterns to follow:** the existing three-branch `closeout_instructions` in `jira.py`. Site formatting already strips a trailing slash in `__init__`.

**Test scenarios:** covered by U2.

**Verification:** a Jira adapter built from `example.atlassian.net` returns instructions containing that hostname, `cloudId`, and the forbid on `getAccessibleAtlassianResources` for landed, blocked, and halted.

### U2. Tests prove the brief carries the site and the tool list does not grow

**Goal:** the suite pins R1 through R5 so a later change cannot grant the discovery tool or drop the site from the brief.

**Requirements:** R3, R5, SC1, SC2, SC3

**Dependencies:** U1

**Files:**
- `tests/test_adapters.py`
- `tests/test_closeout.py`
- `tests/_fakes.py` (read only; no edit unless a compile error forces one)

**Approach:**
1. In the existing Jira `test_the_closeout_tools_are_explicit_and_carry_no_confluence_tool` (or a sibling next to it), assert `closeout_allowed_tools()` equals the four-tuple and does not contain `mcp__atlassian__getAccessibleAtlassianResources`.
2. Add a Jira case that `closeout_instructions` for landed, blocked, and halted contains `example.atlassian.net` and `cloudId`, and that it names `getAccessibleAtlassianResources` only as a tool not to call.
3. Add a `test_closeout.py` case that renders a brief with a real `JiraAdapter` (same env and opener pattern `AdapterCase.jira` already uses) and asserts the substituted text contains the site. That is the "brief carries the site" proof, not only the adapter method.
4. Do not grow `FakeAdapter`.

**Patterns to follow:** `tests/test_adapters.py` `Jira` class helpers (`jira_manifest`, `jira`, `opener`); `tests/test_closeout.py` `CloseoutCase.render`.

**Test scenarios:**
- Given a Jira adapter for `example.atlassian.net`, when `closeout_allowed_tools()` is read, then it is exactly the four current tool names and does not include `mcp__atlassian__getAccessibleAtlassianResources`.
- Given that adapter, when `closeout_instructions` is called for landed, blocked, and halted, then each string contains `example.atlassian.net`, `cloudId`, and `getAccessibleAtlassianResources` named only as a tool not to call.
- Given that adapter, when `closeout.render` writes a landed brief, then the brief text contains `example.atlassian.net`.
- Given GitHub and markdown adapters, when closeout tools and instructions are read, then they still match today's strings (existing `AdapterCase` tests keep passing).

**Verification:** `python3 -m unittest tests.test_adapters tests.test_closeout` is green, then the full suite.

## Verification Contract

| Gate | Command | Proves |
|---|---|---|
| Adapter and Closeout tests | `python3 -m unittest tests.test_adapters tests.test_closeout` | SC1, SC2, R1 through R5 |
| Full suite | `python3 -m unittest discover -s tests` | SC3, no regression |

No live Atlassian call. The stub suite is the gate for this branch. A1 is assumed, not proven. A live Jira Closeout after merge is the operator's existing owed live-run rule in `CLAUDE.md`, not a merge blocker for this card. This change does not alter the envelope grammar, the Closeout terminal line, or a template placeholder set.

## Definition of Done

- U1: Jira `closeout_instructions` name `self._site` as `cloudId` and forbid the discovery tool, for all three outcomes.
- U2: tests pin the four-tool allowlist and the site in both the adapter method and a rendered brief.
- `CLOSEOUT_TOOLS` is byte-identical in membership to today's four names.
- `skills/relay/templates/brief-closeout.md` is unchanged.
- `python3 -m unittest discover -s tests` passes.
- Abandoned edits are not left in the tree.
