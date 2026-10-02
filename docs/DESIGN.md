# Bakery design

Bakery is the single home for my agentic harness: the shared pi configuration
used interactively, and a set of autonomous agents ("claws") run by a local
gateway daemon and controlled over Discord.

## Repository layout

| Path | Contents |
| --- | --- |
| `pi/` | Shared pi resources: `extensions/`, `skills/` (incl. vendored), `prompts/`, `agents/`, `rules/` (instruction fragments). |
| `interactive/` | The interactive profile; used directly as `PI_CODING_AGENT_DIR`. |
| `claws/<name>/` | One profile per claw: `claw.toml`, `context.toml`, `settings.json`, `SOUL.md`, `IDENTITY.md`, `AGENTS.md`. |
| `bakery/` | Python project: the `bakery` CLI and the gateway. |
| `bin/` | Helper scripts (e.g. `bin/vendor`). |

Every profile directory is a pi agent dir. Its `settings.json` loads shared
resources from `../pi/...` (or `../../pi/...` for claws). Pi writes some
runtime state into the agent dir (`models-store.json`, `trust.json`, `npm/`);
that is gitignored. Pi also rewrites `settings.json` (e.g.
`lastChangelogVersion`), which is accepted churn.

## State

All mutable claw state lives outside the repo, under
`$XDG_STATE_HOME/claws/`, which is a local-only git repo auto-committed after
every run so memory changes can be reviewed and rolled back.

```
$XDG_STATE_HOME/claws/
├── _shared/USER.md          # facts about me, injected into every claw
├── _gateway/                # gitignored: run ledger (runs.db), control socket, bakery sockets
└── <name>/
    ├── MEMORY.md            # curated long-term memory
    ├── memory/YYYY-MM-DD.md # daily notes (kept forever)
    ├── sessions/            # gitignored: pi session transcripts
    └── worktrees/           # gitignored: build worktrees
```

The interactive profile keeps using `$XDG_STATE_HOME/pi/{sessions,bakery}`.
`bakery list|read|attach` scan both the interactive and the claw locations.

## Context assembly

A `context` extension (loaded by every profile, including `interactive/`)
reads the profile's `context.toml`: an ordered list of files, each with a
character budget and an injection mode (`every_turn` or `session_start`),
plus a total budget. It injects them at `before_agent_start`. Defaults:
20k chars per file, 60k total, 4k for `USER.md`.

Typical claw order: shared rule fragments from `pi/rules/` → the claw's
`AGENTS.md` → `SOUL.md` → `IDENTITY.md` → `_shared/USER.md` (every turn), then
`MEMORY.md` and the two most recent daily notes (session start only).

The interactive profile uses the same mechanism; its "ask me before choosing
an approach" rules are an interactive-only fragment.

## Memory

Memory is only written through tools, never via bash:

- `memory_append` — append to today's daily note.
- `memory_search` — keyword (ripgrep) search across the claw's memory files.
- `memory_get` — read a memory file or line range.
- `memory_edit` — edit `MEMORY.md`; only used by the flush and dream jobs.

Curation:

- **Pre-compaction flush**: before compaction, a silent turn writes durable
  facts to the daily note (hooked into pi's compaction lifecycle, falling back
  to a context-usage threshold checked at `turn_end`).
- **Dream job**: a nightly cron per claw (03:00) distills daily notes into
  `MEMORY.md`.
- Content from untrusted sources (issues, PR comments, web pages, CI logs) is
  recorded as evidence only, never promoted to durable memory without my
  confirmation.

Identity changes need my approval:

- `USER.md` edits are proposed in Discord; applied and auto-committed on
  approval.
- `SOUL.md` / `AGENTS.md` edits are proposed in Discord and opened as a
  `kjames/bakery-identity-*` PR on this repo; merging is the approval.

## Gateway

A single Python daemon (launchd agent, installed with
`bakery service install`) owns everything long-lived:

- the Discord connection (`discord.py`),
- the scheduler, pollers, and file watchers,
- routing of work to claws,
- the run ledger and limits,
- a control socket at `$XDG_STATE_HOME/claws/_gateway/gateway.sock`.

Each unit of work is a `pi --mode rpc` child with a persisted session, run
with the claw's profile as `PI_CODING_AGENT_DIR`. The gateway speaks pi's
JSONL RPC protocol directly, waits for `agent_settled` to detect completion,
and forwards `extension_ui_request` dialogs to Discord as buttons. The bakery
extension still loads, so `bakery list|read|send` keep working on claw runs.

### Units of work

One unit of work = one pi session = one Discord thread in the claw's channel.

- A cron, heartbeat, queue, or manual trigger starts a fresh session and opens
  a thread; replies in that thread continue the same session.
- A top-level message in a claw's channel starts a new thread and session.
- For `triage` and `build`, one ticket is one session and one thread for its
  whole life (including PR feedback). For `scout`, one run is one session.

Continuity between units of work comes from memory files, not long-lived
transcripts.

### Triggers

- **cron**: 5-field expressions in `claw.toml`, Europe/Lisbon, optional
  `active_hours`. Missed runs (Mac asleep, gateway down) are skipped.
- **heartbeat**: a cron job flagged `silent_ok`; a `NO_REPLY` result posts
  nothing and creates no thread. (Built, unused by the v1 claws.)
- **queue pollers**: e.g. `task` filters, GitHub PR state.
- **file watch**: with debounce.
- **manual**: `bakery trigger <claw>` or the Discord equivalent.
- **Discord messages**: top-level messages and thread replies.

### Concurrency

Configurable in `claw.toml`; defaults:

- at most 4 pi processes globally,
- at most 1 active new run per claw (others queue),
- `build` may additionally run up to 2 PR-feedback resumes concurrently,
- idle RPC children exit after 15 minutes and resume from their session file
  on the next thread reply.

### Limits and accounting

A SQLite run ledger at `$XDG_STATE_HOME/claws/_gateway/runs.db`. Defaults,
all configurable per claw: $5, 30 minutes, and 50 turns per run; $20 per claw
per day; $50 per day globally. On breach: abort, park, notify.

### Ask policy

Configurable per claw:

- `ask` — post the question to the thread (an `ask_user` tool) and wait; on
  timeout (default 4h) fall back to either `assume` or `park`, per claw.
- `assume` — record the assumption and continue.
- `park` — stop and wait for me.

### Control

The control socket backs both `bakery trigger|pause|resume|status` and the
slash commands in `#bakery`: `/pause [claw|all]`, `/resume`, `/status`,
`/budget`. `/pause all` stops new runs; `/pause all --abort` also aborts
in-flight runs.

## Discord

One bot application in a private guild:

- `#bakery` — gateway status and control commands.
- `#bakery-<claw>` (e.g. `#bakery-scout`, `#bakery-triage`, `#bakery-build`)
  — one channel per claw. Each claw posts through a per-channel webhook with
  its own name and avatar.
- Only my user ID is accepted; DMs are disabled.

In each thread: one live status message edited in place (current tool, turns,
cost, elapsed); questions and approvals as buttons that @mention me; the final
assistant message; failures and limit breaches (also @mentioning me). Full
transcripts are available via `bakery read|attach`.

Setup (bot application, Message Content intent, channels, Keychain token) is
a generated `wizard` script.

## Safety

Prompt-level rules are not enforcement. Four layers:

1. **Tool allowlist** per tier (`--tools` / `defaultTools`).
2. **`claw-policy` extension** gating `tool_call`: write/edit path allowlists,
   bash deny patterns, the `task` subcommand allowlist; anything else routed
   to Discord for approval.
3. **`sandbox-exec` around bash**: filesystem writes limited to the claw's
   worktree and state dir; `$TASK_FOLDER` is write-denied (only the `task`
   tool may touch it). Read-only tiers get no network from bash.
4. **`pre-push` hook** in claw worktrees rejecting any ref other than
   `kjames/bakery-*`.

Credentials (macOS Keychain, injected by the gateway per child, least
privilege):

- Discord bot token and model API keys (`auth.json` holds only `$ENV`
  references).
- A read-only fine-grained GitHub PAT (metadata, contents, issues, PRs,
  actions: read) for read-only claws.
- A read-write fine-grained GitHub PAT (contents, PRs: write) for `build`.

Because read-only claws have no network from bash, network access goes
through extension tools: `web_search`, `web_fetch`, `context7`, and a GET-only
`github` tool (via `gh api`). These replace the former skills in every
profile.

## Shared task list

Claws share my existing `task` tool (`~/src/personal/tools/task`, data in
`$TASK_FOLDER`) with me. Required additions to `task`:

- every command (reads included, since loading may normalize and write) runs
  under an `fcntl.flock`; writes are atomic (temp file + `os.replace`);
- refuse to run if a sync conflict file exists (Dropbox `(conflicted copy)`
  or Syncthing `.sync-conflict`);
- tag paths: `--tag Bakery/build` writes `## Bakery` / `### build`; filters
  match full paths or leaves;
- `owner` and `link` fields, stored as YAML frontmatter, which force a task
  into its own `<id>.md` file (the rule becomes: description, owner, or link);
- `set --owner X` is a compare-and-set (fails if owned by someone else unless
  `--force`); `unset owner` releases;
- `set --description-append`, `add --link`, `owner`/`link` filters, `--json`
  on `list` and `show`;
- property tests (parse/render round-trip; concurrent writers lose nothing).

Claws access it through a `task` pi tool with a per-claw subcommand
allowlist.

### Ticket flow

1. `scout` adds tasks (auto-tagged `Triage`) with a `link`.
2. `triage` claims `tag=triage` tasks (including ones I add by hand),
   researches, appends notes and a priority tag, then re-tags to
   `Bakery/build`, `Bakery/human`, or `Bakery/wontfix` and releases.
3. `build` claims `Bakery/build` tasks, moves them to `Bakery/review` once a
   PR is open, and runs `done` after merge.

I delete `Bakery/wontfix` tasks myself.

## Claws (v1)

All configuration (models, limits, schedules, ask policy, concurrency) lives
in each claw's `claw.toml`.

### scout — read-only

Finds third-party TODOs and syncs them into `task`.

- Deterministic Python collectors in the gateway, over `TheKevJames` repos
  listed in `scout.toml`: TODO comments in checked-out repos (one task per
  comment), GitHub issues and PRs, CI failures, and deprecation/other warnings
  grepped from CI logs on the default branch.
- Dedup key: `link` + summary. TODO links are
  `https://github.com/TheKevJames/<repo>/blob/HEAD/<path>` with the TODO text
  in the summary; issues/PRs use their URL; CI uses the workflow URL with the
  job/test in the summary. A changed TODO text is a new task; triage handles
  the stale one.
- Already-tracked candidates are filtered in Python; the LLM only sees new
  ones (to write summaries/descriptions and call `task add`) and is not
  invoked at all when there are none.
- `task` allowlist: `list`, `add`.
- Runs daily at 07:00, or manually.

### triage — read-only + web

- Research only: code locations, root-cause hypothesis, acceptance criteria,
  size, priority, route. Anything needing reproduction routes to
  `Bakery/human`.
- `task` allowlist: `list`, `show`, `set` (tag, owner, description-append).
- Ask policy: `ask`; on timeout, move to `Bakery/human` and report failure.
- One ticket at a time. Triggered by a file watch on `$TASK_FOLDER` (2-minute
  debounce), an hourly catch-up cron, or manually.

### build — worktree + push

- Picks `Bakery/build` tasks: `highpri` first, then lowest id.
- Works in `$XDG_STATE_HOME/claws/build/worktrees/<repo>/<task-id>`, created
  with `git worktree add` from the checkouts listed in `build.toml`; removed
  after merge/close. Full network (package registries).
- Pushes `kjames/bakery-*` branches and opens PRs; never merges.
- Polls its PRs every 10 minutes; new review comments or failing CI resume
  that ticket's session in the same thread.
- Merged → `done`. Closed unmerged → `Bakery/human` with a note, released.
- `task` allowlist: triage's plus `link` and `done`.
- Ask policy: `ask`, falling back to `park`.

### Models

`claude-sonnet-5-5` for scout, triage, flush, and dream; `claude-opus-5-5`
for build. The gateway runs whatever `pi` is on `PATH`.

## Testing

- `task`: property tests (hypothesis).
- Gateway: a fake pi RPC child replaying JSONL recorded from real pi, and the
  Discord adapter behind an interface with a fake implementation.
- One manual end-to-end smoke test against real pi on a cheap model.

## Delivery plan

1. bakery: import from dotsystem (history preserved), restructure,
   `bin/vendor`, this document.
2. dotsystem: point at bakery, pipx entry, trim `vendor`/`discover`/
   pre-commit.
3. tools/task: locking, atomic writes, conflict guard, tag paths,
   `owner`/`link`, filters, `--json`, `--description-append`, tests.
4. `context` extension and `USER.md`.
5. Gateway core: RPC client, scheduler, ledger, control socket, CLI.
6. Discord adapter and setup wizard.
7. Policy extension, sandbox, pre-push hook, secrets.
8. Memory tools, flush, dream job.
9. scout.
10. triage.
11. build.
