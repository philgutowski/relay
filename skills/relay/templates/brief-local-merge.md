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

Run every command in the foreground and wait for it to finish. Never start a command or an agent
in the background and end your turn to wait for it: in this session, ending your turn is exiting,
and everything still running is killed with you. A mutation driver, a test suite, or a build that
takes twenty minutes is twenty minutes of waiting, not a reason to background it. A background
command's completion notification does not survive the final turn either, so run the gate itself
in the foreground and wait for it the same way. Never end a turn on a promise to resume, "standing
by", "will check back", "once it finishes", whether or not you actually backgrounded anything:
there is no next turn to keep that promise on.

$skill_form_rule

Work on the branch `$branch` and nothing else. Do not merge, do not push, and do not switch
branches. The runner owns the gate, the merge, and the push once you exit. A branch you leave
behind can be recovered by hand; a push you make cannot be taken back.

$blocked_partial

$blocked_followup
$unenforced_restrictions
## Steps

1. Create `$branch` from `$default_branch` and stay on it for the rest of the session.
2. Run `$ce_plan` from the task text above and note the path of the plan it writes. It runs its
   own document review, so there is no separate review step for the plan.
3. Run `$ce_work $return_mode <plan path>` with that path.
4. Run `$ce_simplify` unless the diff is documentation only or under ten lines.
5. Run `$ce_review $review_mode plan:<plan path>`, apply its findings, and commit the fixes.
6. Run the project gate and make it pass. The gate is: $gate_description
7. $tracker_review_step
8. Print the return envelope below as your final message, with nothing after it.

## If you cannot finish

$tracker_blocked_step A blocked task is a normal outcome and the run continues without it. Stopping
with no comment and no envelope is the one ending the runner cannot diagnose, so spend your last
turn on the envelope rather than on one more attempt.

## The return envelope

Your final message must end with this fenced block:

```$envelope_tag
status: complete
blockers:
changed_files:
plan_path: <the plan path from step 2>
learnings:
```

`status` is one of `complete`, `blocked`, or `failed`. List one blocker per line under
`blockers:` when there are any. Under `learnings:`, judge whether this task found something a
future session would get wrong without knowing it: a cause that was not where it looked, a
contract or seam whose rules are not visible in the code, a decision reversed with a reason, or a
trap that cost real time. Leave it empty on an ordinary run rather than filling it by reflex, and
write it as plain prose with no colon led sub header line (`Cause:`, `Fix:`, `Status:`), since a
line shaped that way is read as the start of a new field. A line starting with the word status is
the most dangerous shape: it is read as replacing the `status:` you already declared above, even
buried inside a sentence describing a past state such as "status: failed until I disabled
caching."
