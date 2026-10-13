# Repositories

- `~/src` is the operator's and read-only to you; never try to change it.
- To change a repository, work in your own clone, under your state dir,
  borrowing objects from the operator's checkout:
  `git clone --reference ~/src/personal/<repo> https://github.com/TheKevJames/<repo> "$BAKERY_MEMORY_DIR/worktrees/<repo>"`.
  Reuse it next time (`git fetch`), and add `git worktree`s of it for
  separate pieces of work.
- Commit on a `kjames/bakery-<topic>` branch, push it to `origin`, and open a
  pull request with `gh pr create`. The operator reviews and merges; never
  push to the default branch (GitHub refuses it anyway).
- Changes you want to your own instructions (`SOUL.md`, `AGENTS.md`,
  `claw.toml`, ...) are proposed the same way: a pull request on
  `TheKevJames/bakery`.
