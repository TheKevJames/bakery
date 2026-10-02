# Bakery design

Bakery is the single home for my agentic harness: the shared pi configuration
used interactively, and a set of autonomous agents ("claws") run by a local
gateway daemon and controlled over Discord.

## Repository layout

| Path | Contents |
| --- | --- |
| `pi/` | Shared pi resources: `extensions/`, `claw-extensions/` (claws only: `policy/`, `memory/`, `task.ts`, `ask-user.ts`, `github.ts`), `claw-prompts/` (claws only, eg. `/dream`), `skills/` (incl. vendored), `prompts/`, `agents/`, `rules/` (instruction fragments). |
| `interactive/` | The interactive profile; used directly as `PI_CODING_AGENT_DIR`. |
| `claws/<name>/` | One profile per claw: `claw.toml`, `context.toml`, `settings.json`, `SOUL.md`, `IDENTITY.md`, `AGENTS.md`. |
| `bakery/` | Python project: the `bakery` CLI, the gateway, and collectors. |
| `bin/` | Helper scripts (eg. `bin/vendor`). |

Every profile directory is a pi agent dir. Its `settings.json` loads shared
resources from `../pi/...` (or `../../pi/...` for claws). Pi writes some
runtime state into the agent dir (`models-store.json`, `trust.json`, `npm/`);
that is gitignored. Pi also rewrites `settings.json` (e.g.
`lastChangelogVersion`), which is accepted churn.

Claw profiles list their extensions explicitly rather than loading all of
`pi/extensions/`: they need `context/`, `bakery.ts`, `time-awareness.ts`,
`web.ts`, `mcp-servers.ts`, and `pi/claw-extensions/` (the gateway adds
`policy/` itself), and load `pi/claw-prompts/` as prompts, but not the
TUI-only extensions or `subagent/` (its child pi runs would escape the
gateway's cost ledger).

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
`bakery list|show|read|send|pin|attach` cover both the interactive and the
claw locations (claw sockets live in `_gateway/bakery/`, named after the work
key, eg. `bakery read triage-task-151`). `bakery attach` refuses a claw
session the gateway is running, and runs pi with the claw's profile
otherwise. `bakery gc` never collects claw sessions, since units of work
resume from them.

## Context assembly

A `context` extension (`pi/extensions/context/`, loaded by every profile)
reads the profile's `context.toml`:

```toml
file_max_chars = 20000   # default per-file budget
total_max_chars = 60000  # default budget across all files, in order

[[file]]
path = "../../pi/rules/workflow.md"  # relative to the profile; ~ and ${VAR} expand

[[file]]
path = "${XDG_STATE_HOME}/claws/_shared/USER.md"
max_chars = 4000
optional = true          # missing is fine; otherwise a missing file is an error

[[file]]
path = "${XDG_STATE_HOME}/claws/scout/memory/*.md"
latest = 2               # glob; the N lexicographically-last matches
inject = "session_start" # default: every_turn
```

- `every_turn` files are re-read before each agent run and added to pi's
  context files (rendered like `AGENTS.md`), so edits apply on the next
  prompt.
- `session_start` files are sent once as a hidden custom message, and re-sent
  whenever that message is no longer in the model's context (resumed
  sessions, compaction).
- Over-budget files are truncated with a notice telling the model to read the
  file directly. Budgets are allocated over all entries in order regardless
  of mode, so a file's share is stable across turns.
- Config errors and missing required files are reported as pi extension
  errors; the gateway treats any extension error as fatal for a claw run.

Pi itself still loads `<profile>/AGENTS.md` natively, after the
`context.toml` files and before any project `AGENTS.md`. So a claw's own
instructions live in `claws/<name>/AGENTS.md` and are not listed in its
`context.toml`; the interactive profile has no `AGENTS.md`.

Typical claw order: shared rule fragments from `pi/rules/` → `SOUL.md` →
`IDENTITY.md` → `_shared/USER.md` → the claw's `AGENTS.md` (every turn),
then `MEMORY.md` and the two most recent daily notes (session start only).

Every profile uses the same rule fragments, including the "ask me" rules in
`pi/rules/workflow.md`. Each claw's `AGENTS.md` defines what asking means
for it (see [Ask policy](#ask-policy)).

Shared extensions' npm dependencies are declared in `pi/package.json` and
locked in `pi/bun.lock`; run `bun install --frozen-lockfile --cwd pi` after
cloning or when the lockfile changes (dotsystem's `sync` does this).

## Memory

Memory lives in the claw's state dir and is only written through tools
(`pi/claw-extensions/memory/`), which run in pi's process; the bash sandbox
cannot write the state dir.

- `memory_append(text, source)` — a timestamped entry in today's
  `memory/YYYY-MM-DD.md`, tagged with its source: `owner` (I said it),
  `self` (the claw's own work or conclusions), or `external` (issue/PR text,
  web pages, logs).
- `memory_search(query)` — case-insensitive keyword search (ripgrep) over
  `MEMORY.md` and the daily notes, newest first, capped at 50 lines.
- `memory_get(file, offset?, limit?)` — read `MEMORY.md` or a daily note.
- `memory_edit(edits | content)` — `MEMORY.md` only: exact replacements or a
  full rewrite, capped at `[memory] max_chars` (20k, its context budget). Not
  in the default tools; only the dream job adds it.

Curation:

- **Pre-compaction flush**: once the context is within
  `[memory] flush_margin_tokens` (8k) of pi's compaction threshold, the
  memory extension injects a hidden message at `turn_end` asking the model to
  save anything durable with `memory_append` and reply `NO_REPLY`. It fires
  once per compaction cycle, as an ordinary model turn using the claw's own
  model and context, so its cost is accounted.
- **Dream job**: defined once in `claws/defaults.toml` (03:00 daily,
  `prompt = "/dream"` from `pi/claw-prompts/dream.md`, Sonnet, `silent_ok`,
  `tools = ["memory_edit"]`). It distills recent daily notes into `MEMORY.md`
  and never promotes `external` notes, which stay in the daily notes as
  evidence; its report lists what it held back. This rule is in the prompt:
  a tool cannot judge text.
- **Commits**: after every run the gateway commits the state repo
  (`<work_key>: <status>`), so every memory change can be reviewed and
  reverted.

Identity changes need my approval:

- `propose_user_update(edits, reason)` shows the diff as a confirm dialog
  (Discord Approve/Deny); approved edits are applied to `_shared/USER.md`
  (capped at 4000 characters) and committed after the run. Unapproved or
  timed-out (`ask.timeout_hours`) proposals change nothing.
- `SOUL.md` / `AGENTS.md` edits are to be proposed as
  `kjames/bakery-identity-*` PRs on this repo, merging being the approval.
  This needs build's push mechanism and lands after step 11.

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
extension still loads (sockets under `$XDG_STATE_HOME/claws/_gateway/bakery`).

Code lives in `bakery/bakery/gateway/`: `config` (claw.toml), `rpc` (the pi
child), `runner` (one run and its limits), `pool` (warm children), `ledger`,
`scheduler`, `core` (queueing and control operations), `control` (socket),
and `channel` (the interface Discord implements).

### Configuration

`claws/defaults.toml` holds a `[gateway]` table (timezone, global process
cap, global daily budget) and a `[claw]` table of defaults. Each
`claws/<name>/claw.toml` overrides any `[claw]` value (tables merge key by
key) and declares its `[[job]]`s. Loading is strict: unknown keys and bad
values are errors. Changes apply on `bakery gateway reload`; an invalid
config is rejected and the running one kept. In-flight runs keep the config
they started with.

### Units of work

One unit of work = one pi session = one Discord thread in the claw's channel.

Each unit has a work key, eg. `scout/daily-20261002-0700`,
`triage/task-151`, or `discord/<thread-id>`; its pi session id is the key with
`/` replaced by `-`, which is also the session's name (so `bakery read
triage-task-151` works). Resuming a unit starts pi again with the same
`--session-id`, which restores it from the claw's `sessions/` dir. A unit's
first run is new work; later runs are resumes. Runs of one unit never overlap.

- A cron, heartbeat, queue, or manual trigger starts a fresh session and opens
  a thread; replies in that thread continue the same session.
- A top-level message in a claw's channel starts a new thread and session.
- For `triage` and `build`, one ticket is one session and one thread for its
  whole life (including PR feedback). For `scout`, one run is one session.

Continuity between units of work comes from memory files, not long-lived
transcripts.

### Triggers

- **collectors**: a job may name a `collector` (`bakery/bakery/collectors/`),
  deterministic Python run in a worker thread before the job's run. Its
  output is appended to the job's prompt; if it finds nothing, there is no
  run. One collection or run per claw and job at a time, so the same
  candidates are never handed over twice (a manual trigger while one is
  under way is refused). `bakery status` shows collections in progress, and
  a manual trigger says when it is collecting. Problems are posted to
  `#bakery`. A collector may also name the run's unit of work (eg. one per
  ticket, `triage/task-151`), so a ticket keeps one thread and session.
  Job starting lives in `bakery/bakery/gateway/jobs.py`.
- **repeat**: a job with `repeat = true` starts again after each settled run
  (not after replies or resumes), until its collector finds nothing, the
  claw is paused, or its budget runs out; for working through a queue. If
  the collector hands back the unit that just settled, the run did not get
  anywhere: the repeat stops with a notice rather than looping.
- **cron**: 5-field expressions in `claw.toml`, Europe/Lisbon, optional
  `active_hours`. Missed runs (Mac asleep, gateway down) are skipped. Jobs in
  `defaults.toml`'s `[claw]` table apply to every claw (a claw's job of the
  same name replaces one); a job may override `model` and `thinking` and add
  `tools` for its units of work, which keep them on later runs (eg. replies).
  `bakery trigger <claw>` runs the claw's only own job; shared jobs (eg.
  `dream`) must be named.
- **heartbeat**: a cron job flagged `silent_ok`; a run whose only reply is
  `NO_REPLY` posts nothing and creates no thread. (Built, unused by the v1
  claws.) A `NO_REPLY` message never becomes any run's reply.
- **file watch**: `watch = "<path>"` starts the job once files under the
  path have changed and then been quiet for `debounce_seconds` (default
  120). The gateway polls names, sizes, and modification times every 10s
  (`bakery/bakery/gateway/watcher.py`); nothing fires at startup.
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

A SQLite run ledger at `$XDG_STATE_HOME/claws/_gateway/runs.db`, one row
per run (one prompt until pi settles). Defaults, all configurable per claw:
$5, 30 minutes, and 50 turns per run; $20 per claw per day; $50 per day
globally. Daily budgets reset at midnight in the gateway timezone.

A run's cost budget is the smallest of its per-run limit and what remains of
the claw's and the global daily budgets. Cost is the change in pi's session
stats (`get_session_stats`) over the run, reconciled after every turn and
compaction, so compaction summaries count too; each assistant message is also
charged provisionally so a costly reply stops the run before its tool calls
execute. The time limit excludes time spent waiting on me in a dialog.

Run outcomes:

- `settled` — finished normally.
- `parked` — a limit was hit, the daily budget was exhausted, or the claw was
  paused with `--abort`. `bakery resume <work-key>` continues it.
- `failed` — an extension error, a model error after retries, or pi exiting.
- `interrupted` — the gateway stopped mid-run. Not restarted automatically.

### Ask policy

Configurable per claw:

- `ask` — post the question to the thread (an `ask_user` tool) and wait; on
  timeout (default 4h) fall back to either `assume` or `park`, per claw.
- `assume` — record the assumption and continue.
- `park` — stop and wait for me.

`ask_user` (`pi/claw-extensions/ask-user.ts`) reads the policy from
`BAKERY_ASK_*` variables the gateway sets per child. It asks through a pi
`input` dialog with the timeout attached; cancelling counts as no answer.
Parking ends the run without another model call (`terminate`) and returns
`details.park`, which the runner turns into a parked run (`waiting for an
answer: …`); replying in the thread then resumes the unit with my reply as
the prompt. A question that times out is labelled `timed out` in Discord even
when pi's own dialog timeout, which starts earlier, ends the run first.

### Claw environment

A claw's pi child gets only `PATH`, `HOME`, `USER`, `LANG`, `TMPDIR`,
`TASK_FOLDER`, `XDG_*`, and `LC_*` from the gateway's environment, plus its
`secrets`: entries `NAME` or `NAME=ITEM`, setting variable NAME from secret
ITEM (default: NAME). The default is `ANTHROPIC_API_KEY`, `JINA_API_KEY`, and
`GH_TOKEN=BAKERY_GITHUB_READ`. Secret values come from the gateway's
environment if set, else the Keychain (generic password, account `bakery`,
service = ITEM). A missing secret fails the run. Secrets are visible to pi and
its extension tools, never to bash.

### Control

The control socket backs `bakery trigger|pause|resume|status|budget` and
`bakery gateway reload`; the slash commands in `#bakery` (`/pause [claw|all]`,
`/resume`, `/status`, `/budget`) call the same operations in-process.
`pause` stops new runs; `pause --abort` also parks in-flight ones. `resume`
takes a claw, `all`, or a work key (re-running a parked unit).

## Discord

One bot application in a private guild; the gateway creates what is missing
on connect:

- a `bakery` category,
- `#bakery` — gateway notices (start/stop, rejected reloads, exhausted daily
  budgets) and slash commands,
- `#bakery-<claw>` per enabled claw (eg. `#bakery-scout`), each with one
  bot-owned webhook so the claw posts under its own name (and
  `claws/<name>/avatar.png`, if present). Claws added later need a gateway
  restart for their channel.

Only my user ID is acted on (others' messages are ignored and their clicks
and commands refused); bots, webhooks, and DMs are ignored. Settings live in
`claws/defaults.toml` as `[gateway.discord]` (`guild_id`, `owner_id`); the
bot token is the `DISCORD_BOT_TOKEN` secret.

Routing:

- My top-level message in `#bakery-<claw>` starts a thread on it; the work key
  is `<claw>/thread-<thread-id>` and my message is the prompt.
- Gateway-started work (cron, manual, queues) gets a thread named after its
  work key. Work-key ↔ thread mappings are kept in `runs.db`.
- My reply in a thread answers a pending `input` dialog if there is one,
  else steers the in-flight run, else queues a run with the reply as its
  prompt. Replies are acknowledged with a reaction.
- `silent_ok` runs create no thread unless they produce something other than
  `NO_REPLY` or need attention.

In each thread:

- one status message per run, edited at most every 5s
  (`⏳ <key> · <tool> · turn 3 · $0.42 · 2m10s`) and replaced by the outcome;
- the final reply, in ≤2000-character chunks, or attached as `reply.md` if it
  would take more than 4 messages;
- for parked, failed, or interrupted runs, an @mention with the reason and a
  **Resume** button;
- dialogs, @mentioning me: `confirm` as Approve/Deny; `select` as buttons (up
  to 5 options) or a select menu; `input`/`editor` as "reply in this thread",
  each with Cancel. Answered, timed-out, and cancelled dialogs are edited to
  say so and lose their buttons. Buttons survive restarts (discord.py
  `DynamicItem`s); clicking one whose request is gone says it expired.

Slash commands (guild-scoped): `/status`, `/budget`, `/trigger claw [job]
[prompt]`, `/pause target [abort]`, `/resume target`, `/reload`.

Code: `bakery/bakery/chat/` — `relay` (all behaviour; implements the
gateway's `Channel`), `transport` (the interface), `discord_transport`
(discord.py, kept thin), and `render` (text, shared with the CLI).

### Running it

1. One-time Discord setup (done by hand): a `bakery` application whose bot
   has Public Bot off and the Message Content intent on; the bot invited to
   my server with the `bot` and `applications.commands` scopes and
   permissions to view, manage, and post in channels and public threads,
   manage webhooks, read history, attach files, and add reactions; its token
   stored as the `DISCORD_BOT_TOKEN` Keychain secret; and the server and my
   user IDs in `[gateway.discord]`. `bakery gateway check` verifies it
   (connects, creates the layout, posts to `#bakery`).
2. `bakery service install|uninstall|restart` manages the launchd agent
   `in.thekev.bakery` (`~/Library/LaunchAgents/in.thekev.bakery.plist`):
   `bakery gateway run`, kept alive, with `PATH`, `HOME`, `LANG`,
   `TASK_FOLDER`, `XDG_*`, and `BAKERY_REPO` recorded at install time.
   Install from the pipx `bakery` so the plist points at a stable path.
3. Logs: `$XDG_STATE_HOME/claws/_gateway/gateway.log` (rotating), and
   `launchd.log` for anything before logging starts. SIGHUP reloads config.

## Safety

Prompt-level rules are not enforcement. Each claw's `[policy]` (defaults in
`claws/defaults.toml`, overridable per claw; there are no named tiers) is
enforced in its pi child by `pi/claw-extensions/policy/`, which the gateway
always loads with `--extension` so no profile can omit it. The gateway passes
the policy as `BAKERY_POLICY`; without a valid one, every tool call is
blocked.

- `tools`: names or `*` patterns (eg. `mcp__context7__*`). Only these are
  declared to the model, and any other call is blocked. The default allows
  `read`, `grep`, `find`, `ls`, `bash`, `ask_user`, `web_search`,
  `web_fetch`, `github`, and the context7 tools; no `edit` or `write`.
- `write_paths`: where bash and `edit`/`write` may write, besides `$TMPDIR`.
  Empty by default; build's worktrees in step 11. Memory is only written
  through the memory tools.
- `deny_read`: paths and globs nothing may read: `~/.ssh`, `~/.aws`,
  `~/.gnupg`, `~/.netrc`, `~/Library/Keychains`, `~/.config/gh`,
  `~/.config/gcloud`, `~/.config/zsh/dropins/nobackup-*`, and
  `interactive/auth.json`. The gateway adds the whole claw state root, with
  the claw's own state dir and `_shared` re-allowed, so claws read neither the
  gateway's files nor each other's memory or transcripts.
- `network`: domains bash may reach (eg. `pypi.org`, `*.githubusercontent.com`);
  empty by default, so bash has no network. Build's allowlist arrives in step
  11.
- `confirm`: regexes on tool names or bash commands; a match asks me in
  Discord (Approve/Deny) before running.

Enforcement:

1. **Bash** runs inside Anthropic's `sandbox-runtime` (sandbox-exec on
   macOS, bubblewrap on Linux): writes only to `write_paths` and `$TMPDIR`,
   no reads under `deny_read`, and network only through its filtering proxy
   to `network` domains. It also always blocks writes to `.git/hooks`,
   `.git/config`, and shell rc files, and blocks unix sockets (so the
   ssh-agent too). Secrets are removed from bash's environment.
2. **pi's in-process file tools** (`read`, `ls`, `grep`, `find`, `edit`,
   `write`) are checked by the extension against the same rules (paths are
   resolved through symlinks first). `grep` and `find` are refused on a
   directory that contains a denied path, since they recurse.
3. **Tools that need the network or credentials** (`web_search`,
   `web_fetch`, context7, `github`) run in pi's process, outside bash.
   `github` is GET-only, through `gh api` with a read-only token.
4. **Pushing** (step 11): bash cannot push (no credentials, no ssh-agent), so
   build pushes through a `git_push` tool that only accepts `kjames/bakery-*`
   refs and runs git with `-c core.hooksPath=<bakery hooks>`, whose
   `pre-push` hook rejects anything else. The hook is not installed into
   worktrees, which share hooks with my own checkouts.

An extension that fails while pi starts (eg. a bad policy or `context.toml`)
fails the run before the model is called.

Credentials (macOS Keychain, least privilege, selected per claw by `secrets`):

- `DISCORD_BOT_TOKEN` (gateway only), `ANTHROPIC_API_KEY`, `JINA_API_KEY`.
- `BAKERY_GITHUB_READ`: a fine-grained PAT for `TheKevJames` repositories
  with metadata, contents, issues, pull requests, and actions: read.
- `BAKERY_GITHUB_WRITE` (step 11, build): as above plus contents and pull
  requests: write.

`web_search`, `web_fetch` (`pi/extensions/web.ts`), and context7 (an MCP
server registered by `pi/extensions/mcp-servers.ts`) replace the former
skills in every profile.

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
- `priority` (low, medium, high) and `size` (small, medium, large) fields,
  also frontmatter, set by triage;
- `set --description-append`, `add --link`, `owner`/`link`/`priority`/`size`
  filters, `--json`
  on `list` and `show`;
- property tests (parse/render round-trip; concurrent writers lose nothing).

Claws use it through tools in `pi/claw-extensions/task.ts`: `task_list`,
`task_show`, `task_add`, `task_set` (tag, claim, release, append notes),
`task_link`, and `task_done`. Each claw's `policy.tools` selects which it
gets; claims are always made as the claw itself (`BAKERY_CLAW`).

### Ticket flow

1. `scout` adds tasks (auto-tagged `Triage`) with a `link`.
2. `triage` claims `tag=triage` tasks (including ones I add by hand),
   researches, appends notes, sets `priority` and `size`, then re-tags to
   `Bakery/build`, `Bakery/human`, or `Bakery/wontfix` and releases.
3. `build` claims `Bakery/build` tasks (high priority first), moves them to
   `Bakery/review` once a PR is open, and runs `done` after merge.

I delete `Bakery/wontfix` tasks myself.

## Claws (v1)

All configuration (models, limits, schedules, ask policy, concurrency) lives
in each claw's `claw.toml`.

### scout — read-only

Finds things in my repos that belong on my task list. Profile:
`claws/scout/` (`claw.toml`, `scout.toml`, `AGENTS.md`, `SOUL.md`,
`IDENTITY.md`, `context.toml`).

- The `daily` job (07:00, or `bakery trigger scout`) uses the `scout`
  collector (`bakery/bakery/collectors/scout.py`) over the repos in
  `scout.toml` (every checked-out `TheKevJames` repo except `core`):
  - **CI**: the latest completed default-branch run of each workflow failed
    (Dependabot's own update jobs excluded). Link: the first failed run of
    the current failure streak, so a fresh failure after a fix is new.
  - **Warnings**: `DeprecationWarning`, `FutureWarning`,
    `PendingDeprecationWarning`, and `::warning` lines in the logs of each
    workflow's latest passing run, deduplicated, at most 20 per repo. Link:
    the workflow URL with `?warning=<fingerprint>`.
  - **Issues and PRs**: all open issues, and PRs not by bots, except those
    labelled `ready-for-human`. Link: their URL.
  - **TODO/FIXME comments** on the default branch, fetched with the read
    token over HTTPS into `refs/bakery/scout/<branch>` (my checkout and its
    `origin` refs are untouched), minus per-repo `exclude` globs. One task
    per comment. Link: `…/blob/<branch>/<path>?todo=<fingerprint>#L<line>`;
    the fingerprint is of the comment's text, so the line can move.
- **Lineage, not memory**: a candidate is new unless an existing task has its
  link (ignoring the `#L…` anchor). There is no separate "seen" store, so a
  task I delete comes back if its source still exists: before deleting a
  `Bakery/wontfix` task, I fix its source. Scout therefore never merges or
  drops candidates and copies links verbatim.
- At most `max_candidates` (25) per run, ordered CI, PRs, issues, warnings,
  TODOs; the rest wait for the next run. Repos are collected in parallel
  (8 at a time; about 13s for all 17). Repos that cannot be read are
  reported in `#bakery` and in scout's report; the others still run.
- The model (Sonnet, medium thinking, $2 per run, ask policy `assume`, no
  bash) writes each candidate up with `task_add` and reports what it added.

### triage — read-only + web

Profile: `claws/triage/`. Sonnet, medium thinking, $3 per ticket.

- The `queue` job's `triage` collector (`bakery/bakery/collectors/triage.py`)
  hands over the lowest-numbered unowned task tagged `Triage`, skipping
  scheduled (recurring) ones, as the unit `triage/task-<id>`. It runs hourly,
  within two minutes of `$TASK_FOLDER` changing (scout or I add tickets),
  manually, and on repeat until the queue is empty.
- Per ticket: claim it, check the source still exists, research (code,
  history, issues, docs, web; no reproduction, tests, or edits), append notes
  (route and reason, source check, code locations, hypothesis, approach,
  acceptance criteria, open questions), always set `priority` and `size`,
  then re-tag and release:
  - `Bakery/build`: a TheKevJames repo checked out under
    `~/src/personal`, clear scope, checkable by tests, lint, or CI;
  - `Bakery/human`: needs a decision, reproduction, credentials, outside
    systems, or anything uncertain;
  - `Bakery/wontfix`: false positive, done, obsolete, or duplicate, with
    what to change at the source.
- Tools: read-only defaults (bash without network or writes), web, `github`,
  context7, memory, `ask_user`, and `task_list`, `task_show`, `task_set`.
- Ask policy `ask`, falling back to `assume`; every question's assumption is
  "route this ticket to Bakery/human", so an unanswered question routes it
  there and the reply says triage could not finish.
- A ticket left claimed by a failed or parked run is not picked up again;
  its thread's Resume button continues it.

### build — worktree + push

- Picks `Bakery/build` tasks by `priority` (`high`, then `medium`, then
  `low`), then lowest id.
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
- Extensions and the gateway: real `pi` against a fake OpenAI-compatible
  model (`bakery/testing/harness.py` plus `fake_provider.ts`), which records
  every request so tests can assert on the exact system prompt and
  transcript. The Discord adapter sits behind an interface with a fake
  implementation.
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
6. Discord adapter and setup wizard; `bakery gateway run` and
   `bakery service install|uninstall` (launchd).
7. Policy extension, sandbox, secrets, web/context7/github tools.
8. Memory tools, flush, dream job, `USER.md` proposals.
9. scout.
10. triage.
11. build, with its `git_push` tool, `github` write access, and the
    pre-push hook.
