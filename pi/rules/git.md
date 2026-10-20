# Git
- Conventional Commits: build/chore/ci/docs/feat/fix/perf/refactor/revert/style/test
- Use message files for writing commit messages and PR bodies
- Commit body suffix should always be "Closes #xxx" when that commit resolves an issue on our issue tracker
- Never force-push to master/main -- force-pushing to a branch is OK, as is pushing to master/main
- Naming: when creating branches, prefix them with "kjames/"
- Fetch and branch from the latest default branch before code changes
- PRs will be squash-merged, so subsequent PRs need to be based off the HEAD of their parent and be rebased by you -- create a watch polling for the parent PR's merge to do this automatically
- Only stack PRs when the child genuinely needs the parent's code
- Always work in worktrees stored in a temporary directory and clean them up when done
