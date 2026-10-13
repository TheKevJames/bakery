# Repositories

- Work only in your own clones, under your state dir:
  `git clone https://github.com/<owner>/<repo> "$BAKERY_MEMORY_DIR/clones/<owner>/<repo>"`.
  This includes `TheKevJames/bakery`: the copy your tools run from may hold
  the operator's unmerged work, so it is not evidence of what is on GitHub.
- Reuse a clone next time. Before every use, bring it up to date and read
  the default branch as it is on GitHub, never a stale local branch:
  `git fetch && git checkout --detach origin/HEAD`.
- To change a repository, add a `git worktree` of its clone, under
  `$BAKERY_MEMORY_DIR/clones/`, for each separate piece of work.
- Commit on a `kjames/bakery-<topic>` branch, push it to `origin`, and open a
  pull request with `gh pr create`. The operator reviews and merges; never
  push to the default branch (GitHub refuses it anyway).
- Changes you want to your own instructions (`SOUL.md`, `AGENTS.md`,
  `claw.toml`, ...) are proposed the same way: a pull request on
  `TheKevJames/bakery`.
