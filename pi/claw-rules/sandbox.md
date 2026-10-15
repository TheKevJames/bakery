# Sandbox

- `/tmp` is not writable: use `$TMPDIR` for scratch files. It is yours alone
  and does not outlast your process. Keep scratch files out of
  `$BAKERY_MEMORY_DIR`, which is committed.
