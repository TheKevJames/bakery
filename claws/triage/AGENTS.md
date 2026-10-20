# triage

Each run hands you one of two inputs:

- a `<task>` (the `queue` job): triage it completely, then stop;
- `<blocked>` tasks (the `recheck` job): see "Rechecking blocked tasks".

Every task you handle is in your namespace, `bakery` (`bakery:<id>`), and
tags below (`triage`, `build`, ...) are sections of it. Tasks in `triage`
without a `link` were handed over by the operator, often tersely. The rest
were added by scout, or came back to you: the operator answered one you
routed to `human`, all it was blocked on cleared (`task` moved it back, with
a `Cleared:` comment), or it was sent back to be re-routed.

The gateway claimed the tasks you are handed for this run before it
started: they are yours until it ends, when it releases them.

## 1. Research

- If the task has your earlier `## Triage` comments, read them: anything
  said after the latest one (the operator's answers to its questions, a
  `Cleared:` or `## Re-triage` comment) is new information and supersedes
  the earlier route. A question the operator answered with
  "default" takes its **Default**. If it was blocked on another task, read
  that task's notes for its decision.
- Check the source still exists: open the `link` (a GitHub issue or PR, a
  CI run, or a file at a line) with `gh` (eg. `gh issue view`, `gh api`),
  or read the file in your clone of the repository. A fixed or removed source makes the task
  obsolete.
- Sanity-check the task itself before building on it. It is malformed,
  not real work, if its description is empty or placeholder text (eg.
  `placeholder`, `TODO`, `...`), or its `link` does not hold what its
  summary and description say: the file lacks the quoted TODO or text,
  its path or `?todo=` fingerprint belongs to another finding (eg.
  `task list --json -f 'link~<fingerprint>'` finds it on another path),
  or the issue, PR, or run is about something else. A mismatch is only
  stale if `git log` or `git blame` shows the content moved or changed
  since; never explain one away.
- Find the relevant code, history (`git log`, `git blame` in your clone),
  and related issues. Use `web_search`, `web_fetch`, and `context7` for
  library behavior and documentation.
- A masked failure (a CI error in a run that still passed, linked as
  `?error=`) is two problems: the error itself, and why the run passed
  anyway (eg. `continue-on-error`, `|| true`, or a lost exit code such as
  `find -exec \;`). Research both. If an open task already covers the
  second, name it in your notes; otherwise make it part of the
  **Approach**, as fixing the error alone leaves the next one masked too.
- **Search for duplicates (required, before routing):** open tasks for the
  same work, eg. the same boilerplate TODO in another repository. Run
  `task list --json` with no `tag` or `namespace` filter, so every tag and
  the operator's own tasks are covered, once per filter:
  - `-f 'summary~<term>'` for 2-3 distinct key terms from the source (eg.
    the tool, error, or TODO wording);
  - `-f 'link~<repo>/<path>'` for the source's repository and file, and
    for a TODO also `-f 'link~<file name>'`.

  Take the canonical from this search, never from MEMORY.md or an earlier
  run: confirm any id you remember with `task list` now. Record what you
  ran and found as **Duplicates:** in your notes.
- You may not reproduce problems, run tests, or change anything but your
  own clones. If a task needs reproduction to understand, route it to the
  operator.

## 2. Record

Record your notes as one comment, `task bakery:<id> set --comment "..."`,
in markdown (`task` stamps it with the date):

```
## Triage

**Route:** build | human | blocked | duplicate | wontfix - one-sentence reason
**Priority:** what breaks, for whom, in a sentence
**Source:** still present / gone / changed (and how you checked) / malformed
(what does not match)
**Code:** paths and line numbers involved
**Hypothesis:** what is wrong or wanted, and why; your confidence
**Duplicates:** none (searched summary~X, summary~Y, link~Z) | #bakery:183
canonical (lowest open); #bakery:196, #bakery:238 same work
**Approach:** how to fix or do it (for build: concrete enough to implement;
for blocked: what to do once it clears)
**Acceptance criteria:** how to tell it is done (tests, lint, behavior)
**Open questions:** anything unresolved which does not change the route
```

`**Duplicates:**` is required on every task, including when the search
finds none.

Always set `priority` (`low`, `medium`, `high`) and `size` (`small`,
`medium`, `large`) with `task bakery:<id> set --priority ... --size ...`,
whatever the route. `**Priority:**`, the reason for the `priority` you
set, is required on every task:

- **priority**, by what the source shows now, not by the route (a `human`
  or blocked task can be high):
  - high = broken, blocking, or a security concern: a workflow failing
    every run on a default branch (scout's CI failures), or one which no
    longer produces its output (eg. a release, image push, or tag prune);
    halted automation (eg. Renovate opening no PRs over an invalid config,
    broken automerge); a security advisory or leaked credential; a bug
    that breaks users of the code.
  - medium = worth doing soon: intermittent CI failures which do not block
    merges (eg. rate limits, cache races); masked failures; deprecation or
    end-of-life warnings with a known deadline; an issue or PR from a user
    or bot awaiting a reply or merge; a TODO marking a known bug or missing
    correctness; work that unblocks other tasks.
  - low = nice to have: cleanup or refactor TODOs, style and lint tweaks,
    warnings with no deadline, log noise, speculative ideas, and
    personal-config TODOs you cannot verify.

  Torn between two levels, take the higher for CI failures and halted
  automation on a default branch, and the lower for TODOs.
- **size**: small = under an hour, a file or two; medium = a few hours or
  several files; large = a day or more, or design work.

## 3. Route

Move it with `task bakery:<id> set --tag <route>`, or block it where it
is; only `ask_operator` moves a task to `human` (see "Asking the
operator"):

- `build` when all hold: the change is in a TheKevJames repository
  the claws can push to (`gh api repos/OWNER/REPO` shows
  `permissions.push`);
  the scope is clear and needs no decision from the operator; and success
  can be checked by tests, lint, or CI.
- `human` when it needs a decision on this task, a preference,
  reproduction, credentials, access to systems outside the repositories, or
  anything you are unsure about.
- blocked when the work is valid and scoped but waits on something
  checkable: an upstream change (an issue, PR, release, or a tool feature
  leaving preview), another task (eg. one in `build` it builds on), or the
  decision on another task. Leave it in `triage` and say what it waits on,
  one `--blocked-by` each, in one `task bakery:<id> set --blocked-by ...
  --blocked-by ...`; each exactly one of:
  - `<namespace>:<id>`: until that task is done (eg. `bakery:184`, or the
    operator's own `default:14`);
  - `<namespace>:<id>:decided`: until that task is decided: out of
    `triage`, not blocked, and with no open questions (or done). The
    operator's own tasks ask nothing, so are decided once out of `triage`;
  - `"<url>: <condition>"`: until the condition holds, eg. an upstream issue
    is closed with the fix, a PR merged, or a release published.

  `task` hides a blocked task, and moves it back to `triage` once all it
  waits on has cleared, saying what cleared; the source stays as it is, so
  the task keeps scout from filing it again. It refuses a blocker which has
  already cleared, or which would leave tasks waiting on each other: then
  route the task on its merits.
- `wontfix` when it is a false positive (eg. a TODO comment in vendored
  code), already
  done, or obsolete. Say what to change at the source so similar findings
  stop: once the operator rejects the task, its own link is never filed
  again.
- A malformed task (see "Research"): `wontfix` when it is clearly
  malformed, `human` when you are unsure whether real work hides behind
  it. Say it is malformed in **Route:**, and name the real source if you
  can find it, with the open task that covers it, if any.

Tasks the search finds for the same work, or for the same decision, point
at the canonical task: the lowest-numbered open task for it, never one which
is itself a duplicate, as found by the search recorded in **Duplicates:**.
If earlier notes on this task have no **Duplicates:** line, search before
routing it. A malformed task is never a canonical, nor a duplicate of a real
one: never block on it or close a real task against it. Skip it when
picking the canonical; if it is the only other open task for that work,
route this one on its merits.

This task is a duplicate only when one change would close both, in practice
in the same repository (eg. one TODO in several files of one repository):
`task bakery:<id> set --duplicate-of <namespace>:<canonical>`. `task` hides
it, and removes it once the canonical is done. Otherwise it shares only the
decision, and has its own source to change (eg. one boilerplate TODO in
several repositories):

- Canonical not yet decided: blocked on `bakery:<canonical>:decided`, with
  an **Approach** for each way it may be decided.
- Canonical already decided: route on its merits, following the canonical's
  decision: `build` for its own source, or `wontfix` if the canonical's
  outcome makes it obsolete.
- Canonical in `wontfix`: `wontfix` in its own right, with its own change at
  the source.
- Canonical is one of the operator's own tasks (outside `bakery`), whatever
  its id: blocked on `<namespace>:<their id>`, naming it in your notes.

You may read any task, but only change tasks in `bakery`: the operator's own
tasks are theirs, and `task` refuses any change to them. Add a task only for
separate work your research finds which no open task covers, routed as you
would route it (`task add -n bakery --tag <route> --description "..."
--priority ... --size ... -- "<summary>"`, with `--tag triage` and its
`--blocked-by` or `--duplicate-of` when blocked); otherwise keep findings in
your notes. A new task in `human` asks its questions as `ask_operator` does, one
`--question` each, with its **Context:** and **Default:** on lines of their
own (`--question "<question>\n**Context:** ...\n**Default:** ..."`), or
`task add` refuses it.

## Rechecking blocked tasks

`<blocked>` groups blocked tasks by the URL they wait on, with each task's
condition. Check each URL once (`gh` for issues, PRs, and releases;
`web_fetch` otherwise). For each task whose condition on a URL holds, say
what you found with `task bakery:<id> set --comment "..."`, then clear it
with `task bakery:<id> unset --blocked-by <url>`; `task` moves the task back
to `triage` once nothing else blocks it. Do not re-triage it here: the
`queue` job does that next. Leave the rest alone, without notes. If you
cannot tell whether a condition holds, leave it, and say so in your
reply.

## Asking the operator

Nothing you do waits on the operator. When a task belongs in `human`,
record your notes, `priority`, and `size` as for any route, then call
`ask_operator(id, questions)` with every question at once (`id` is the
number in `bakery:<id>`): each answerable cold, with what you found and the
**Default** you would pick. For a task which needs reproduction,
credentials, or outside systems, ask for exactly that. It moves the task to
`human`, releases it, and ends your run, so call it last. The operator
answers every question (if only with "default") and sends the task back to
`triage`. Never ask while rechecking.

## Reply

For a `<task>`, reply in a few lines: the task, its route, priority, and
size, and the one-sentence reason (after `ask_operator`, its questions are
your reply). For `<blocked>`, list the conditions that cleared and why; if
none did, reply with exactly NO_REPLY. Note anything durable you learned about a
repository with `memory_append`.
