# Task list

- Read tasks with `task list --json` and `task show <id> --json`. Filter a
  list with `-f`: comma-separated `field<op>value` on summary, tag, owner,
  link, priority, or size, with `=`, `!=`, `~` (contains), or `!~`; eg.
  `task list --json -f 'tag=bakery/build,owner='` (unowned).
- Change tasks only with your task tools (`task_add`, `task_set`), never with
  `task` in bash: they claim as you, file into your section, and check what
  may change.
- Only change tasks tagged under `bakery/`, and never move one out of it: the
  rest are the operator's.
- Never complete or delete a task; route it instead (eg. to
  `bakery/wontfix`). Once a task is gone, scout no longer recognizes its
  source and files it again.
