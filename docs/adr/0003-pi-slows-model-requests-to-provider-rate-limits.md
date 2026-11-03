# Pi slows model requests to provider rate limits

Providers cap traffic per model (eg. 15 requests/minute, 500 requests/day,
reset at midnight Pacific), and the cap is external to any one pi process,
while one session can spawn up to four parallel subagent processes. So pi
enforces it itself: the rate-limits extension holds each model request in
`before_provider_request` until a shared, append-only event log in the state
dir shows the window has room, and fails the request when the wait would
exceed ten minutes.

## Considered Options

- **Per-process in-memory counters.** Rejected: the session and its subagents
  would each hold the full quota, multiplying actual traffic by up to five,
  and a restart would silently reset the daily counter.
- **A single JSON state file, read-modify-write with atomic rename.**
  Rejected: with parallel subagents, two processes can read the same state and
  each append their own event, and the last rename wins, so under sustained
  parallel bursts the counter systematically undercounts. Appending a single
  line is atomic and needs no merging.
- **A rolling 24h window for the daily limit.** Rejected: the provider resets
  on a calendar-day boundary (midnight Pacific for Google), so a rolling
  window over-throttles for up to a day after the reset. The window grammar
  (`rolling:<duration>`, `calendar:<timezone>`) keeps both as data.

## Consequences

- The gate checks the shared count and then appends its request event, so
  concurrent processes racing for the last slot of a window can collectively
  send slightly more than the limit. Jitter on wake plus a re-check after each
  wait keeps this to the boundary moment, and the provider's 429 and pi's
  built-in retry remain the backstop.
- In-process nested model calls (eg. `qna`, cache warming) do not fire
  `before_provider_request`, so they are not gated or counted; subagents are
  separate processes and are.
- A request that later fails (eg. network) still counts against the window:
  conservative by design.
- Tokens are counted from actual usage after each response, so one response
  can push a window slightly past its limit before the next request waits.
