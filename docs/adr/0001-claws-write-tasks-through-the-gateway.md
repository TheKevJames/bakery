# Claws write tasks through the gateway, as their unit of work

The gateway embeds task's server (see task's ADR 0001), and gives each unit of
work its own unix socket, which only that unit's sandbox may connect to and
none may write; the socket's path is the caller's identity, so nothing a claw
passes in can change who it is. A claw's claims are held as its work key (eg.
`triage/task-151`), and the gateway releases them when the run holding them
ends, however it ends; collectors claim the tasks they hand a run before it
starts. Rules for every claw live in `claws/gateway.toml`, where no claw's own
configuration can override them.

## Considered Options

- **A standalone task daemon.** Rejected: only the gateway knows when runs
  start and end, so a daemon would need a protocol to learn it, and would have
  to cope with the gateway restarting without it.
- **One socket per claw.** Rejected: a claw can have several units of work in
  flight, any of which could then act as another.
- **A token in the claw's environment.** Rejected: bash shares pi's sandbox,
  so it reads the token too.
- **Claims owned by the claw (`triage`).** Rejected: the end of one run could
  not release its own claims without releasing a concurrent run's too.
- **A lease separate from the claim, or a wall-clock expiry.** Rejected: a
  unit of work has at most one run in flight, so its work key already bounds a
  claim exactly, and renewing an expiry would keep writing to a database
  Syncthing is syncing. A gateway crash is covered by releasing every claw's
  claims when the gateway starts.
