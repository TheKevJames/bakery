# Repositories

- Keep one bare clone of each repository under your state dir, and reuse it:
  ```
  clone="$BAKERY_MEMORY_DIR/clones/<owner>/<repo>.git"
  git init --bare "$clone"
  git -C "$clone" remote add origin https://github.com/<owner>/<repo>
  ```
  This includes `TheKevJames/bakery`: the copy your tools run from may hold
  the operator's unmerged work, so it is not evidence of what is on GitHub.
  The one exception: a commit named in a `<changes>` feed is what you run
  on, so read it, and only read it, in that copy: `git -C <checkout> show
  <sha>`.
- Before every use, bring the clone up to date, and read the default branch
  as it is on GitHub, never a stale local branch:
  `git -C "$clone" fetch --prune origin && git -C "$clone" remote set-head
  origin --auto`. Read it in place (`git -C "$clone" show
  origin/HEAD:<path>`, `git -C "$clone" grep <pattern> origin/HEAD`,
  `git -C "$clone" log origin/HEAD`) or in a worktree.
- Check files out, or change them, only in a worktree of the clone, one per
  repository and unit of work, at
  `$BAKERY_MEMORY_DIR/worktrees/<owner>/<repo>/$BAKERY_SESSION_ID`:
  `git -C "$clone" worktree add --detach <path> origin/HEAD` to read, or
  `-b kjames/bakery-<topic>` in place of `--detach` to change it. Remove it
  (`git -C "$clone" worktree remove <path>`) once you no longer need it:
  before your run ends if you only read it, or once its work is merged or
  dropped. git refuses commits and pushes anywhere else.
- The sandbox refuses to create shell rc files (`.zshrc`, `.bashrc`, ...)
  anywhere, your state dir included. In a repository that tracks one (eg.
  `root/~/.config/zsh/.zshrc` in `TheKevJames/dotsystem`), `worktree add`
  fails with `unable to create file <path>: Operation not permitted` and
  leaves no worktree; that line comes first, so never trim git's output
  (eg. with `tail`). Check out everything else instead, and read the rc
  file with `git -C "$clone" show origin/HEAD:<path>`:
  ```
  git -C "$clone" worktree add --no-checkout --detach <worktree> origin/HEAD
  git -C <worktree> sparse-checkout set --no-cone '/*' '!/<path>'
  git -C <worktree> checkout
  ```
  (`-b kjames/bakery-<topic>` in place of `--detach` to change it.)
- Commit on a `kjames/bakery-<topic>` branch, push it to `origin`, and open a
  pull request with `gh pr create`. The operator reviews and merges; git
  refuses pushes of any other branch or tag.
- Changes you want to your own instructions (`SOUL.md`, `AGENTS.md`,
  `claw.toml`, ...) are proposed the same way: a pull request on
  `TheKevJames/bakery`.
