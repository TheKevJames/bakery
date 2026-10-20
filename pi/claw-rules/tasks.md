# Task list

- The task list has namespaces. Yours is `bakery`, and its tags (`triage`,
  `build`, `human`, `blocked`, `wontfix`, ...) are its sections; every other
  namespace is the operator's. Tasks are named `<namespace>:<id>` (eg.
  `bakery:12`), and a bare id means the operator's `default` namespace.
- Use `task` in bash; see `task --help` and `task <command> --help`. Read
  tasks with `task list --json` and `task show <namespace>:<id> --json`.
  Filter a list with `-f`: comma-separated `field<op>value` on namespace,
  summary, tag, claim, link, priority, or size, with `=`, `!=`, `~`
  (contains), or `!~`; eg. `task list --json -f
  'namespace=bakery,tag=build,claim='` (unclaimed).
- Change tasks with `task add`, `task <id> set`, and `task <id> release`
  (always `--json`, to see the task as it stands after). The gateway runs
  them for you, as you, under your rules: what you may create, claim, and
  change. A refusal says why; it is not something to work around.
- A task you change is claimed for you, and a task you add is yours, until
  your run ends, when the gateway releases them; meanwhile no one else may
  change them. You cannot change a task someone else has claimed. Release a
  task early only to hand it to someone else in this run.
- Never complete or delete a task, except as your own instructions allow;
  route it instead (eg. to `wontfix`). Once a task is gone, scout no longer
  recognizes its source and files it again.
- Never wait on the operator. To ask them about a task, use `ask_operator`
  if you have it; otherwise decide, and state your assumptions in your
  reply. In a conversation the operator started in Discord, end your reply
  with your questions instead: their reply continues it.
