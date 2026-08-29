# Backend fixtures, captured in U1

Every file here was produced by a real CLI running a real task against
`~/Documents/PhilAI/relay-proof/target` on 2026-08-28. Nothing is synthesized, paraphrased, or
hand written. `docs/solutions/logic-errors/stubbed-seams-agree-by-construction-first-live-run-found-five-contract-defects.md`
is why: a fixture written alongside the parser that reads it proves only that the two agree.

**One deliberate edit, applied after capture.** This repository is public, and a task told to
find something the proof repository does not hold will search outward before concluding that:
one blocked run read across the operator's home directory and pulled paths and prose from
unrelated private work into its transcript. Those payloads are redacted, along with the commit
author's email address, which becomes `relay@example.com`. Event structure, ordering, and
decodable line counts are untouched, which is what these fixtures are read for. A redacted path
is still a path and a redacted word is still a word, so a normalizer sees the shape it saw
before. Treat the files as verbatim in structure and redacted in content.

`_scrub.py` is what performed it and is idempotent, so a later capture is cleaned by running it
again from the repository root before committing. Run it on anything captured from a real session
here, and read its substitution list before trusting it against a machine holding different work
than this one.

U6's normalizers and U8's brief inserts are tested against these. The stubs in `tests/stub-claude/`
are not evidence for the same contract, because a stub and a normalizer written in one session
agree by construction.

## The shapes

Each backend carries the same six shapes, one file per shape, so a test can ask the same question
of all three.

| File | What it is |
|---|---|
| `session-transcript-complete.jsonl` | the CLI's own session log for a task that finished |
| `session-transcript-blocked.jsonl` | the same, for a task that stopped deliberately |
| `stdout-complete.jsonl` | the structured stream the runner captures, task complete |
| `stdout-blocked.jsonl` | the same, task blocked |
| `last-message-complete.txt` | the final message carrying a `status: complete` Envelope |
| `last-message-blocked.txt` | the final message carrying a `status: blocked` Envelope with prose blockers |
| `closeout-last-message-skipped-long.txt` | a Closeout message whose terminal line sits past the 200 character head |
| `closeout-stdout.jsonl` | the Closeout process's stream |
| `denial-refusal.jsonl` | a captured refusal of a denied tool call (R25) |

`codex/` has no `denial-refusal.jsonl`, and its absence is the finding rather than a gap: Codex
exposes no per-tool deny flag, so it cannot demonstrate a refusal and is recorded as not enforcing
at launch. R19, R21, and R24 are what cover it instead.

## Provenance, per backend

**claude** (2.1.250). The complete and blocked pairs are the T-4 and T-1 tasks from the
2026-08-26 and 2026-08-28 proof runs, taken from `~/.claude/projects/` and from the Relay state
directory's own `logs/` so the stdout is what a Runner actually captured rather than a
reconstruction. The denial and long-Closeout shapes are two small purpose-run probes.

**codex** (codex-cli 0.149.0). T-6 added `capitalize_words` and landed; T-8 was given a task
naming a Slack webhook this project does not hold, and blocked on it. Both ran all seven pipeline
stages. The Closeout ran against T-6's landing.

**grok** (grok 1.0.5). T-14 added `is_palindrome` and landed; T-15 blocked on the same
unavailable webhook. Both ran all seven stages. `denial-refusal.jsonl` is a `rm -rf` attempt
refused by a `--deny` rule, captured with the target file confirmed still present afterwards.

## What the captures corrected

Two of the plan's Assumptions did not survive contact with the installed CLIs. Both are recorded
in `contracts.py` beside the pins they govern.

Grok's `--permission-mode dontAsk` cancels every tool call in a headless run rather than
approving it, so a Task under it does nothing at all. `auto` is the mode that both executes and
enforces the deny list. The plan had assumed Grok's mode vocabulary mirrored Claude's.

Grok registers plugin skills under bare names, so the invocation is `/ce-plan` rather than a
namespaced form. The plan left this open for U1 to settle.

Codex additionally needs `--add-dir <repo>/.git` beside `-C <repo>`, or its `workspace-write`
sandbox refuses every write under `.git/` and no Task can commit.
