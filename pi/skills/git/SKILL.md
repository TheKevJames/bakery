---
name: git
description: Waiting on GitHub PR checks, Actions runs, or PR merges via the `watch` tool. Use after pushing a PR or when asked to monitor CI/merge status.
---

# Git

## Waiting on CI and Merges
Never block on these in bash: start a `watch` and carry on, or end your turn.

* PR checks: `exit` mode with `sleep 20; gh pr checks 12 --watch --fail-fast`. Right after a push the checks
  may not be registered yet, which `gh pr checks` reports as a failure; the leading `sleep` covers that.
* If a check is advisory and expected to fail (eg. a PR-checklist bot), `--fail-fast` exits early. Use
  `poll` mode on `gh pr checks 12 --json name,bucket` instead, excluding that check: exit 0 when all
  remaining buckets are `pass`/`skipping`, 1 while any are `pending`, 2 on `fail`.
* A single Actions run: `exit` mode with `gh run watch 123 --exit-status`.
* PR merged: `poll` mode with this probe, which also stops if the PR is closed unmerged:
  ```bash
  state=$(gh pr view 12 --json state -q .state) || exit 2
  case "$state" in MERGED) exit 0 ;; OPEN) exit 1 ;; *) echo "$state"; exit 2 ;; esac
  ```
* CircleCI: see the `circleci` skill.
