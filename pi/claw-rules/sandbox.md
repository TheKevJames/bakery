# Sandbox

- `/tmp` is not writable: use `$TMPDIR` for scratch files. It is yours alone
  and does not outlast your process. Keep scratch files out of
  `$BAKERY_MEMORY_DIR`, which is committed.
- Your home holds your XDG config, cache, data, and bin dirs, and Go's
  `GOPATH` and `GOCACHE`; it persists across your runs. Install tools there,
  not in `$TMPDIR`: `uv tool install` (and `uvx`) links them into
  `$XDG_BIN_HOME`, which is on your `PATH`.
- Your policy's `commands` (in your claw.toml) are on `PATH` as the operator
  installed them: check `command -v` before installing your own copy.
