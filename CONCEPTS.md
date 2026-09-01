# Concepts

Shared domain vocabulary for this project, entities, named processes, and status concepts with
project-specific meaning. Seeded with core domain vocabulary, then accretes as ce-compound and
ce-compound-refresh process learnings; direct edits are fine. Glossary only, not a spec or
catch-all.

## Relationships

A Runner reads one Manifest and drives a series of Tasks. Each Task gets its own Task process and,
once it lands, its own Compound process. The Runner decides a Task's outcome by Verify-landed,
which consults git and the Tracker through a Tracker adapter, never the Task process itself. The
Shipping mode named in the Manifest decides what landing means for that project.

## The loop

### Runner
The Relay process that drives a Manifest to completion: it launches one Task process per Task in
order, decides each outcome, and halts rather than continuing past an outcome it cannot confirm.

The Runner holds no project knowledge of its own. Everything project-specific reaches it as
Manifest data. It reads the Tracker but does not write to it, so a defect in the Runner can never
move a card.

A Runner is one process for the length of a Manifest, so the code it runs is the code it loaded
when it started. Where Relay drives its own repository, a Task that lands a change to the Runner's
own code does not change the Runner already running, and that run's own record is written by the
older code. What a Task lands becomes visible inside the same run only where the Runner reads
something at the moment it uses it rather than loading it once at the start. A change spanning both
kinds is the case that does more than go unobserved: the run holds the half it loaded at the start
and reads the half it takes at the moment of use, and stops at the first use that needs the two to
agree.

### Lease
The claim a Runner holds while it drives a Manifest, which is what stops two Runners from
interleaving work against one repository. There are two: one over the Manifest, so a second run
of the same Manifest refuses to start, and one over the target repository, so two different
Manifests naming the same repository cannot merge into it at the same time.

A Lease is renewed on a heartbeat rather than held for the length of the work, so it expires on
its own if a Runner dies. A Lease past its expiry is stale and the next Runner reclaims it,
marking any Task the dead Runner left in flight as halted. The expiry is deliberately shorter
than any Task timeout, so a crashed Runner never blocks a repository for the length of a Task;
the cost of that choice is that every long operation a Runner performs has to keep renewing, and
one that does not is how a Runner ends up acting without the claim it thinks it holds.

Reclaiming marks only the Task it inherited; it never records the run's own outcome, because at
the moment of reclaim the reclaiming run has not yet decided whether it halts there or continues
past the halt. Only the run's own later conclusion may record that the run itself is over.

### Follower
A reader attached to a running Manifest, which decodes the Task processes' output and reports the
run's phase events. It takes no Lease, decides nothing, and writes nothing, so a Follower can be
started, stopped, and started again beside a live Runner without touching it.

A Follower reports on a run rather than participating in one, so a run with no Follower is not
diminished, only unobserved. It is also the only component that notifies, which means the same
run halting with nobody following it announces itself to nobody. A Follower launched beside a run
starts from a floor taken before that run began, because the state directory outlives any one run
and its Task logs are appended to rather than replaced.

### Manifest
The single file, one per project, carrying every project-specific fact a Runner needs: the Task
list, the Tracker adapter to use, the Shipping mode, the permission allowlist and disallow list,
per-Task timeouts, and how each of the project's qualifying properties is satisfied.

### Task
One unit of work the operator defined before the run started, identified by a Tracker record. Tasks
in a Manifest are independent of each other by requirement; a Task that depends on another belongs
in a later run. A Task may be marked excluded from unattended runs, with a stated reason, when
something about it needs a human present. The same `reason` field is required when a Task's
backend differs from the manifest default. A Task that matches the default needs none.

### Task process
The single headless agent invocation that carries one Task from plan to landing. It starts with an
empty context, knows nothing of any other Task, and its report of its own success is not evidence.
It creates and stays on a branch named the Manifest's `project.branch_prefix` plus the Task id.
The prefix defaults to `relay/` when the Manifest omits the key. An empty prefix is the Task id
alone, which is not the same as omitting the key. Retry of a blocked Task looks at the branch
name stored on that Task's record, so a later prefix edit cannot hide stranded commits.

A Task process owns whatever it spawns. Subagents and gate commands run underneath it and can
outlive it, so the Runner bounds the whole group rather than the one invocation, and a Task
process that has exited is not by itself evidence that its work has stopped.

Its own turn ending and the process exiting are the same event, so anything it starts in the
background is at the mercy of that exit rather than surviving it. This is why the Runner's own
bounding of the whole group is a backstop rather than the first line of defense: what usually
kills a background command is the process's own turn ending, not the Runner noticing afterward.

### Backend
The CLI that runs a Task process and that Task's Closeout process, one of `claude`, `codex`, or
`grok`. A Task names its backend. A Manifest may default it. Absence of every backend key means
`claude`. `/relay` proposes one from a written rubric while the Manifest is authored. The
operator sees every proposal and can change it. The Runner launches on that CLI. It does not
choose or change the backend during a run. `/relay` itself still runs in Claude Code. Only
the launched processes vary.

### Capability record
The frozen facts the Runner reads about one backend: whether it enforces tool restrictions at
launch, its permission flags and forbidden spellings, the version it was tested against, how to
query its plugin, its credential prefixes and nesting markers, and whether the session id is
runner chosen. The launch seam, the readiness probe, and the Brief inserts all read this record
rather than a second per backend table.

### Task path bound
The commit-scope prefix list a Manifest names for Task branches. On a backend that cannot refuse
tools at launch, the Runner diffs the Task branch against this list before it merges, and refuses
the merge when the commit falls outside it, leaving the branch intact. It is a different set from
the Closeout's own path allowance, and it does not observe which tools the Task invoked.

### Brief
The instruction text a Runner hands a process it launches, rendered for that process alone from a
template plus the Task's own facts. There is one shape per Shipping mode for a Task process, and one
for a Closeout process.

A Brief renders deterministically, so the same inputs produce the same text and a re-run after a
halt does not change what a process was told. Its template is read at the moment of rendering rather
than held from the Runner's start, which is what lets a Task change the Brief that later processes in
the same run receive, and equally what lets a template naming a value the running Runner cannot yet
supply stop the run outright.

### Envelope
The structured block a Task process prints at the end of its work to report what it did: whether it
completed, was blocked, or failed, the blockers if any, the files it changed, the plan it worked
from, and anything it judged worth keeping as a learning.

An Envelope is a claim, not evidence. The Runner reads it to classify how a Task process exited, and
never to decide whether the Task landed, which is Verify-landed's job from git and the Tracker
alone. A Task process that exits without one gets its own Halt class rather than the benefit of the
doubt.

### Digest
The record the Runner builds by reading a Task process's transcript once, holding the Envelope
alongside what the Runner itself observed: whether the process timed out, its exit status, how
many tool calls it made, and the Halt class the Runner assigned. The Runner holds it in memory and
renders pieces of it into the Closeout process's own Brief before launching that process; it is
also written once per Task as a durable record, though nothing in the run loop reads that file
back, so the Closeout process itself never sees the Digest directly, only what the Runner rendered
from it.

A Digest is not the same claim as the Envelope it carries. The Envelope is the Task process's own
account of what happened; the Digest is the Runner's account of the process's exit, built without
trusting that account, and the Envelope is only one field inside it. An optional field's presence
in the Digest proves the Runner's transcript parsing found it, never that a later stage, such as a
rendered Brief, actually carries it forward.

### Closeout process
A separate short agent invocation the Runner launches after every Task process exit except a
timeout that left the tree dirty. It has two ordered duties: write the Task's outcome to the Tracker (the closing reference
when Landed, a comment carrying the Runner's blocker digest when Blocked), then the Compound
judgment. It exists because the Runner never writes to the Tracker and the Task process exits
before the landing commit exists, so neither can name it.

Its ending is a contract: the final line of its last message says whether the Compound judgment
wrote a learning or skipped one, and the Runner reads that line from the end of the message, not
the start. A Closeout process that ends any other way is recorded as unfinished, which is a
finding for the operator rather than a halt.

### Compound process
The second duty of the Closeout process: judging whether a Task produced a learning worth keeping
and writing it if so. It is kept out of the Task process because a Task process at the end of its
context is the worst available judge of its own learning. Runs for Blocked Tasks too, since a
blocker is often the learning.

### Halt class
The Runner's classification of one Task process exit, drawn from a closed set and decided from the
session transcript plus git and Tracker evidence. Every class carries the evidence its Cause line
needs, so an operator learns why a Task did not land without reading a transcript.

Because a class is decided from what the exit left behind, the set can only name causes the
evidence records. A cause lying outside the Task process, such as the account's model allowance
running out mid run, leaves the same absence as a crash and is classified as one, so the Cause
line reads as though the Task misbehaved. The set is closed deliberately, so the answer to such a
cause is a finding attached to the record, or a check made before the run starts, rather than a
new class.

Three classes are run scoped and always stop the run, named in `contracts.RUN_SCOPED_HALT_CLASSES`:
each puts something outside the failing Task in question, the remote, the Lease, or the Runner
itself. Any other halt can be continued past when the Manifest opts in with
`on_halt.continue_past_task_halt` and the repository, after the Runner returns it to the default
branch, is one the next Task could start from: a clean tree at the remote's head. The Runner checks
that rather than inferring it from the class, because one class can leave the repository usable or
not. A Task continued past stays halted, is listed by the summary as a check by hand, and is retried
on the next run like any other halt.

### Cause line
The one sentence a run summary prints to say why a Task did not land, and the only diagnosis an
operator who was not watching gets. Each is a fixed template belonging to a Halt class, filled from
the evidence the Runner recorded when it stopped. Findings that attach to a Task without being its
own class carry one too, so a Cause line is not exclusively a property of a Halt class.

A template and the evidence that fills it are two halves of one contract, written in different
places by different code. Two rules keep them joined. Evidence a template names must be a plain
value, because a structured one cannot be rendered into a sentence and is dropped. And where the
evidence and the Task's own record both carry a field of that name, the evidence wins, since the
record acquired most of its fields after the stop and would otherwise describe the aftermath rather
than the cause.

A Cause line is a derived form, and the record keeps the raw sentence it was derived from beside
it: the words the code that stopped the run actually wrote. The summary prints that sentence under
the Cause line whenever the two differ, so a template that fits the class loosely, such as a
refused retry reported under the class for a dirty tree, cannot be the only account of the stop.

## Outcomes

### Verify-landed
The Runner's own determination, from git and the Tracker alone, of whether a Task actually landed.
It never reads the Task process's exit code, printed result, or claims as evidence, because a
headless run has every incentive to report success.

The landing it recognises does not have to be the Runner's own merge. An operator who finishes a
Task by hand between runs and lands it through the project's own tooling has produced the same
outcome by another means, and Verify-landed accepts it on the same terms, because it reads git and
the Tracker rather than trusting which actor performed the merge.

### Landed
A Task whose work is durably where the Shipping mode says it belongs and whose Tracker record names
the landing. Both halves are required: code that merged while the card stayed put is a partial
landing, not a landing, and it halts the run.

### Blocked
A Task whose process stopped deliberately without landing, leaving the repository as it found it. A
Blocked Task is a normal outcome rather than a failure.

A Blocked Task is only legible to an operator who was not watching if the blocker reaches somewhere
outside the run's own transcript. The Closeout process writes that record, as a comment on the
Tracker, and the Runner never does: it reads the Tracker afterwards to confirm a comment appeared
and reports a Blocked Task whose card carries none as a check for the operator to make by hand. So a
Blocked Task the Closeout failed to record is visible in the run summary rather than on the board,
which is the accepted cost of the Runner holding no write path at all.

### Shipping mode
The per-project choice of what landing means: a merge to the default branch performed outside the
Task process, or a pull request whose checks have decided. The Manifest names one, and Verify-landed
applies the matching checks. Only the merge mode is implemented; the pull request mode is named in
the schema and refused before a run starts, so no Task can be driven under it today.

## Surroundings

### Tracker adapter
The read-side interface between a Runner and whatever holds the Task list and their statuses. Each
adapter exposes the same operations regardless of what sits behind it, and not one of them writes:
listing candidate Tasks, reading one Task's status and its recent comments, confirming that a
closing reference is present, and supplying what the Task process and the Closeout process need in
order to write to that Tracker themselves. The absence of a write operation is the whole guarantee.
A Runner cannot move a card by mistake because it holds no way to move one at all.

### External gate
The project's own mechanism that refuses a broken change independently of any agent's judgment,
such as a pre-push hook or continuous integration. Relay requires one to exist before it will run
against a project, and verifies its presence rather than providing it.

Those two forms are not interchangeable to the Runner, and the difference is where each spends its
time. A gate wired into a pre-push hook runs inside the Runner's own push, so the gate's whole
runtime is charged to that one command and has to fit whatever bound the push carries. A gate that
runs after the push returns costs the Runner no time at all and is bounded separately, if at all.
A project can therefore satisfy the requirement in either form and hand the Runner a very different
problem.

The pre-push form carries a further cost the other does not: it inherits the git process's own
environment, which can include scoping variables git sets to let the hook find the repository it
is gating. Anything the hook spawns that also shells out to git, such as a suite that builds its
own throwaway repositories to test against, inherits those same variables when git sets them and
can be silently redirected back at the repository the hook is protecting. A gate that runs after
the push, in its own job environment, does not share
this exposure.

### Qualifying properties
The three conditions a project must meet before Relay can run against it: its Tasks are independent,
the state carried between Tasks is durable in git or on the Tracker, and an External gate refuses
broken changes. These describe the project. A Task can still be individually unrunnable while all
three hold.
