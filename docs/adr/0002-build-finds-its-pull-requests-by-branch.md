# Build finds its pull requests by branch, and the gateway watches them

A gateway collector (`build-review`, every 10 minutes) watches build's pull
requests, not the model. It finds each task's pull request by its branch name,
`kjames/bakery-<id>-<slug>`, among the claws' GitHub user's pull requests, and
judges what is new against when the task's unit last started a run, from the
run ledger. So nothing about a pull request is stored anywhere: not on the
task, and not in bakery's state. Only a pull request with news costs a model
run.

## Considered Options

- **The model waits with the `watch` tool.** Rejected: a held run keeps a
  process slot and runs into its time limit while a review takes days, and a
  run ending (a limit, a gateway restart) loses the watch.
- **The pull request's URL on the task** (in a comment, or a new field).
  Rejected: comments are free text the collector would have to parse, and the
  task's `link` is its source's identity, which lineage depends on. A new
  field would mean changing task for one claw.
- **Recording what was last seen** (comment ids, head SHAs) in bakery's state.
  Rejected: one more store to keep consistent, when the ledger already says
  when the unit last ran. The cost is approximate counting: an event during a
  run resumes the unit once more after it, and automatic resumes are capped
  per task since the operator last said anything.

## Consequences

- Build must push from a branch with its task's prefix, or its pull request is
  never found, and the task's unit is resumed with "no pull request" (an
  automatic resume, so capped).
- Finding pull requests needs one search, covering pull requests updated since
  the oldest task in `review` was created, then one request per pull request
  it returns, on every poll.
