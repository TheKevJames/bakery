# Bakery

Bakery is the operator's agentic harness: the shared configuration used
interactively, and the autonomous claws that work alongside the operator on a
shared task list.

## Language

### Actors

**Operator**:
The one human who configures the claws, answers their questions on the task
list, and reviews their work.
_Avoid_: owner, user, Kevin

**Claw**:
An autonomous agent run by the gateway, with its own profile, policy, and
memory.
_Avoid_: bot

**Gateway**:
The long-lived daemon that runs the claws, starts their jobs, enforces their
limits, and connects them to the operator over Discord.

**Profile**:
One complete configuration of the harness: the interactive profile, or one
per claw.
_Avoid_: agent dir

**Scout**:
The claw that finds work in the operator's repositories and files it as tasks
for triage.

**Triage**:
The claw that researches each task in `bakery > triage` and routes it.

**Build**:
The claw that implements tasks in `bakery > build` and opens pull requests for
them.

**Baker**:
The claw that reviews how the other claws work and files a task for each gap
it finds.

### Code

**Clone**:
A claw's own copy of a repository, cloned from GitHub into its state.
_Avoid_: checkout, mirror

### Work

**Job**:
A claw's configured source of work, naming what starts it and what it is
asked to do.

**Trigger**:
An event that starts a run, such as a schedule, a file change, a manual
request, or a message from the operator.

**Unit of work**:
One continuous piece of a claw's work, from the trigger that starts it through
every reply that continues it.
_Avoid_: session, thread, conversation

**Work key**:
The name that identifies a unit of work, eg. `triage/task-151`.

**Run**:
One prompt to a unit of work, until the claw settles. A unit of work's first
run is new work; later runs resume it.
_Avoid_: session

**Settled**:
The outcome of a run that finished normally.

**Parked**:
The outcome of a run stopped to wait for the operator: by a limit, an
exhausted budget, or a pause. It can be resumed.

**Failed**:
The outcome of a run ended by an extension error, a model error, or pi
exiting.

**Interrupted**:
The outcome of a run cut off by the gateway stopping. It is never restarted
automatically.

**Limit**:
A per-run cap on cost, time, or turns. A run that hits one is parked.

**Daily budget**:
The most one claw, or all claws together, may spend in a day.

**Pause**:
Stopping a claw, or every claw, from starting new runs; with abort, also
parking its runs in progress.

**`silent_ok`**:
A job's permission to end a run with nothing to say, posting nothing and
opening no thread.

**Heartbeat**:
A scheduled job with `silent_ok`, such as a claw's dream.

**Collector**:
Deterministic code that gathers a job's input before its run. When it finds
nothing, there is no run, unless the job runs anyway (`skip_empty = false`).

**Candidate**:
Something a collector found that may become a task.

**Masked failure**:
A failure reported during a CI run that still passed: an error a tool logged,
or a step that was allowed to fail.
_Avoid_: hidden error, green-run error, tolerated failure

**Lineage**:
The identity of a task's source: its link, ignoring line anchors and case. A
task is filed only if no open task, in any namespace, and no rejection has its
lineage.

**Gap**:
A way the claws are failing, which baker files as a task.

### Safety

**Policy**:
The limits on what a claw may read, write, and reach, enforced outside the
model.
_Avoid_: permissions, tier

**Sandbox**:
The OS-level confinement a claw's whole process runs in, enforcing the path
and network parts of its policy.

### Memory

**Memory**:
What a claw keeps between units of work: its long-term memory and its daily
notes.
_Avoid_: transcript

**Long-term memory**:
A claw's curated memory, which it sees at the start of every unit of work.

**Daily note**:
One day's timestamped memory entries, each with its memory source, kept
forever as evidence.

**Memory source**:
Who a memory entry came from: the operator, the claw itself, or external text
such as issues, web pages, and logs.

**Dream**:
A claw's nightly job that distills its daily notes into long-term memory, and
rechecks what the change feed may have made wrong.

**Change feed**:
The operator's commits to the bakery checkout the claws run from, since a
claw's last dream, handed to that dream.
_Avoid_: changelog

**Teach**:
Giving a claw a lesson from the operator, which it verifies before correcting
its long-term memory; the operator sees the resulting diff.
_Avoid_: train, tell

**Lesson**:
What the operator teaches a claw: something to recheck, and why.

**Flush**:
The turn in which a claw nearing compaction saves anything durable to its
daily notes.

### Task list

**Task list**:
The operator's task store, shared with the claws.

**Task**:
An entry in the task list, whether filed by the operator or a claw.
_Avoid_: ticket, issue, item

**Tag**:
A flat name that places a task in a section of its namespace, eg. `build`.

**Namespace**:
A part of the task list in its own directory, so access is granted per
namespace. `default` is the operator's; `bakery` is the claws', the only one
their task rules let them change. Written `bakery > build` for a tag within
one.

**Task id**:
A task's address, `<namespace>:<id>`, eg. `bakery:12`; ids are unique only
within a namespace, and a bare id means `default`.

**Claim**:
An exclusive hold on a task, so that no one else changes it: the operator's,
or a claw's unit of work's. A claw claims every task it adds or changes, and
its claims end with the run that holds them.
_Avoid_: owner, claimant, assignee

**Task rules**:
What a claw may create, claim, and change on the task list, enforced by the
gateway; whatever they do not grant is denied.
_Avoid_: permissions, policy

**Link**:
The identity of a task's source, such as an issue URL or a fingerprinted
code location, used to recognise that source again.

**Route**:
The `bakery` tag a task is moved to, which decides who handles it next.

**`bakery > triage`**:
The route for tasks awaiting triage.

**`bakery > build`**:
The route for tasks build can implement and verify on its own.

**`bakery > human`**:
The route for tasks waiting on the operator: a claw's questions, or changes
only the operator may approve.

**Question**:
Something a claw needs from the operator to finish a task, asked on the task
(task's own Question) as it is routed to `bakery > human`, with its context
and default; the operator answers every one.
_Avoid_: ask, prompt

**`bakery > blocked`**:
The route for valid, scoped tasks waiting on their blockers.

**`bakery > wontfix`**:
The route for tasks that should not be worked on: false positives, done, or
obsolete.

**`bakery > review`**:
The route for tasks whose pull request awaits the operator's review.

**Rejection**:
The operator's record that a link should never be filed again.
_Avoid_: a wontfix

**Blocker**:
One thing a blocked task waits on: another task being decided or done, or an
external condition.

**Cleared**:
Of a blocker: no longer holding.

**Decided**:
Of a task: done, or no longer in `bakery > triage`, `bakery > human`, or
`bakery > blocked`.

**Canonical**:
The task a duplicate waits on: the lowest-numbered open task for the same
work, never a malformed one, or the operator's own task whatever its id.

**Malformed task**:
A task which is not real work: its description is empty or placeholder text,
or its link does not hold what it describes.
_Avoid_: broken ticket
