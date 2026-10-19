# Task list

- The task list has namespaces. Yours is `bakery`, and its tags (`triage`,
  `build`, `human`, `blocked`, `wontfix`, ...) are its sections; every other
  namespace is the operator's. Tasks are named `<namespace>:<id>` (eg.
  `bakery:12`), and a bare id means the operator's `default` namespace.
- Read tasks with `task list --json` and `task show <namespace>:<id> --json`.
  Filter a list with `-f`: comma-separated `field<op>value` on namespace,
  summary, tag, owner, link, priority, or size, with `=`, `!=`, `~`
  (contains), or `!~`; eg. `task list --json -f
  'namespace=bakery,tag=build,owner='` (unowned).
- Change tasks only with your task tools (`task_add`, `task_set`, and
  `ask_operator` if you have it), never with `task` in bash: they claim as
  you, file into your section, and check what may change. They take the id
  of a task in `bakery` alone, eg. `12` for `bakery:12`; the sandbox lets
  you change nothing outside `bakery`.
- Never complete or delete a task; route it instead (eg. to `wontfix`). Once
  a task is gone, scout no longer recognizes its source and files it again.
- Never wait on the operator. To ask them about a task you have claimed,
  use `ask_operator` if you have it; otherwise decide, and state your
  assumptions in your reply. In a conversation the operator started in
  Discord, end your reply with your questions instead: their reply
  continues it.
