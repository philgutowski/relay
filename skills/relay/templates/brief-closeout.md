# Relay closeout for $task_id

The task process for $task_id has already exited. You are running unattended, after the fact, and
nobody is watching. You have exactly two duties, in that order, and then you stop. Do not
continue the task's work, do not touch its code, and do not judge whether it should have landed.

## What the runner saw

Outcome: $outcome
$landing_line
Branch: $branch
Plan: $plan_path
Timing: $timing
Gate: $gate

Blockers the task process reported:

$blockers

Denied tool calls the runner recorded in the task's transcript:

$denials

Other findings:

$findings

## The task, and what its card already says

$data_header

$data_begin
$title

$description

Comments on the card since this run started:

$comments
$data_end

## Duty one: record the outcome on the tracker

$duty_one

Write once. If a comment above already says what you were about to say, do not repeat it.

## Duty two: the compound judgment

Decide whether this task produced a learning a future session would get wrong without it: a
cause that was not where it looked, a contract or seam whose rules are not visible in the code,
a decision reversed with a reason, or a trap that cost real time. Routine work that taught
nothing gets no document, and a blocked task is often where the best one is.

If there is one, run exactly this:

    $compound_command

$compound_skill does not commit. Commit whatever it wrote yourself, in one commit, touching only
these paths and nothing else:

$allowed_paths

Do not push. The runner diffs what you committed against that list, resets your commit if
anything falls outside it, and otherwise pushes it under the project's gate.

## How to end

Your final message must end with exactly one of these two lines, with nothing after it:

    $complete_line
    $skipped_line

Print `$skipped_line` when you judged there was no learning worth keeping. That is the common
and correct answer, not a failure, and the runner treats it as success either way.
