# shellcheck shell=bash
# Sourced by the claws' git hooks: claws change repositories only in a
# worktree of their own clones, under $BAKERY_MEMORY_DIR/worktrees/
# (pi/claw-rules/repos.md).

refuse() {
    echo "bakery: $1 (see pi/claw-rules/repos.md)" >&2
    exit 1
}

require_worktree() {
    local git_dir common top worktrees
    worktrees="$(cd "${BAKERY_MEMORY_DIR:?}" && pwd -P)/worktrees"
    git_dir="$(git rev-parse --path-format=absolute --git-dir)"
    common="$(git rev-parse --path-format=absolute --git-common-dir)"
    if [[ "${git_dir}" == "${common}" ]]; then
        refuse "work in a worktree of your clone, not the clone itself"
    fi
    top="$(cd "$(git rev-parse --show-toplevel)" && pwd -P)"
    if [[ "${top}/" != "${worktrees}/"* ]]; then
        refuse "keep worktrees under \$BAKERY_MEMORY_DIR/worktrees/, not ${top}"
    fi
}
