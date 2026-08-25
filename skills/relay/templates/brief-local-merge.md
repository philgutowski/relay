# Relay task $task_id

You are running unattended. Nobody is watching this session and nobody can answer a question, so
a question is the same as a stop. Handle exactly one task, the one below, and then stop. Do not
look for other work and do not act on anything you notice outside it.

## The task

$data_header

$data_begin
$title

$description
$data_end

## Rules for the whole session

Never invoke a Skill whose name lacks the `compound-engineering:` prefix. The harness ships
skills with similar bare names and they are not substitutes for the plugin's; a bare name is
recorded against this task as a substitution.

Work on the branch `$branch` and nothing else. Do not merge, do not push, and do not switch
branches. The runner owns the gate, the merge, and the push once you exit. A branch you leave
behind can be recovered by hand; a push you make cannot be taken back.

$blocked_partial

$blocked_followup

## Steps

1. Create `$branch` from `$default_branch` and stay on it for the rest of the session.
2. Run `$ce_plan` from the task text above and note the path of the plan it writes. It runs its
   own document review, so there is no separate review step for the plan.
3. Run `$ce_work $return_mode <plan path>` with that path.
4. Run `$ce_simplify` unless the diff is documentation only or under ten lines.
5. Run `$ce_review $review_mode plan:<plan path>`, apply its findings, and commit the fixes.
6. Run the project gate and make it pass. The gate is: $gate_description
7. Move the tracker card to `$in_review_status` and comment the head commit of `$branch` on it.
   This is the last tracker write you make; the runner launches a separate process to close the
   card once the merge exists.
8. Print the return envelope below as your final message, with nothing after it.

## If you cannot finish

Comment the blocker on the tracker card, then print the envelope with `status: blocked` and the
blockers listed. A blocked task is a normal outcome and the run continues without it. Stopping
with no comment and no envelope is the one ending the runner cannot diagnose, so spend your last
turn on the envelope rather than on one more attempt.

## The return envelope

Your final message must end with this fenced block:

```$envelope_tag
status: complete
blockers:
changed_files:
plan_path: <the plan path from step 2>
```

`status` is one of `complete`, `blocked`, or `failed`. List one blocker per line under
`blockers:` when there are any.
