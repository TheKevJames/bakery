---
name: git
description: Git-related skillset, use whenever you're working with git or Github.
---

# Git

Help with Git operations and best practices.

## CLI Best Practices

* Prefer `gh` commands over raw `git` commands for GitHub-specific operations (issues, PRs, releases)
* Use `gh repo view` to quickly inspect repository details
* Use `gh pr checkout NUMBER` to checkout the branch corresponding to a given PR number
* Check `gh --help` for discovering subcommands and capabilities

## Waiting on CI and Merges
Never block on these in bash: start a `watch` and carry on, or end your turn.

* PR checks: `exit` mode with `sleep 20; gh pr checks 12 --watch --fail-fast`. Right after a push the checks
  may not be registered yet, which `gh pr checks` reports as a failure; the leading `sleep` covers that.
* A single Actions run: `exit` mode with `gh run watch 123 --exit-status`.
* PR merged: `poll` mode with this probe, which also stops if the PR is closed unmerged:
  ```bash
  state=$(gh pr view 12 --json state -q .state) || exit 2
  case "$state" in MERGED) exit 0 ;; OPEN) exit 1 ;; *) echo "$state"; exit 2 ;; esac
  ```
* CircleCI: see the `circleci` skill.

## Commit Messages
Follow Conventional Commits:
```
feat(scope): add new feature
fix(scope): fix bug description
refactor(scope): restructure code
docs(scope): update documentation
test(scope): add/update tests
chore(scope): maintenance tasks
```

## Conflict Resolution
1. `git diff --name-only --diff-filter=U` — Find conflicted files
2. Read each conflicted file
3. Understand both sides of the conflict
4. Resolve with minimal changes preserving intent from both sides
