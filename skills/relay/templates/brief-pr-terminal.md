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

Work on the branch `$branch` and nothing else. Do not close the tracker card and do not move it
to a terminal status. The runner launches a separate process for that once it has confirmed the
pull request and its checks.

$blocked_partial

$blocked_followup
$unenforced_restrictions
## Steps

1. Create `$branch` from `$default_branch` and stay on it for the rest of the session.
2. Run `$ce_lfg` with the task text above. It plans, builds, reviews, pushes the branch, and
   opens the pull request.
3. Stop when it prints its terminal token, `$lfg_token`. The runner reads the pull request and
   its checks itself; your report of them is not evidence.
4. Before you stop, print one line: `Learnings: <one line, or "none">`. Use it for a cause that
   was not where it looked, a contract or seam whose rules are not visible in the code, a
   decision reversed with a reason, or a trap that cost real time. Print `Learnings: none` on an
   ordinary run rather than filling it by reflex.

## If you cannot finish

Comment the blocker on the tracker card before you stop, and say in your final message what is
blocked. Leave the branch as it stands rather than reverting it. A blocked task is a normal
outcome and the run continues without it.
