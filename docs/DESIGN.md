# Bakery design

Bakery is the single home for the operator's agentic harness: the shared pi
configuration used interactively, and a set of claws run by a local gateway
daemon and controlled over Discord. Its terms are defined in
[GLOSSARY.md](../GLOSSARY.md); this document describes how they are built.

## Repository layout

| Path | Contents |
| --- | --- |
| `pi/` | Shared pi resources: `extensions/`, `claw-extensions/` (claws only: `policy/`, `memory/`, `task.ts`, `ask-operator.ts`), `claw-sandbox/` (the claws' sandbox launcher), `claw-prompts/` (claws only, eg. `/dream`), `skills/` (incl. vendored), `prompts/`, `agents/`, `rules/` (instruction fragments). |
| `interactive/` | The interactive profile; used directly as `PI_CODING_AGENT_DIR`. |
| `claws/<name>/` | One profile per claw: `claw.toml`, `context.toml`, `settings.json`, `SOUL.md`, `IDENTITY.md`, `AGENTS.md`. |
| `bakery/` | Python project: the `bakery` CLI, the gateway, and collectors. |
| `bin/` | Helper scripts (eg. `bin/vendor`). |
| `GLOSSARY.md` | The project's language: every domain term, defined once. |
| `AGENTS.md` | Repo-level agent instructions, loaded by pi for sessions in this repo; its `## Agent skills` block points vendored skills at `docs/agents/`. |
| `docs/agents/` | Per-repo config read by the vendored engineering skills: `issue-tracker.md` (GitHub Issues), `domain.md` (glossary/ADR consumer rules). |

Every profile directory is a pi agent dir. Its `settings.json` loads shared
resources from `../pi/...` (or `../../pi/...` for claws). Pi writes some
runtime state into the agent dir (`models-store.json`, `trust.json`, `npm/`);
that is gitignored. Pi also rewrites `settings.json` (e.g.
`lastChangelogVersion`), which is accepted churn.

Claw profiles list their extensions explicitly rather than loading all of
`pi/extensions/`: they need `context/`, `bakery.ts`, `time-awareness.ts`,
`ketch.ts`, `context7.ts`, `read-mutation-queue.ts`, `watch/`, and
`pi/claw-extensions/` (the gateway adds
`policy/` itself), inject `pi/claw-rules/` through their `context.toml`,
and load `pi/claw-prompts/` as prompts, but not the TUI-only extensions or
`subagent/` (its child pi runs would escape the gateway's cost ledger).

Every profile disables pi's built-in `codemode`, `tool-search`,
`llama.cpp`, and `mcp` extensions (`-builtin:<name>` in its `extensions`),
so a subagent's child pi does too. None is used: library docs come from the
`context7` tool rather than an MCP server, whose declared tools cost prompt
tokens on every request. Re-enabling `mcp` would also need pi's
`mcp-auth.json` back in the sandbox's profile writes and `deny_read`.

## State

All mutable claw state lives outside the repo, under
`$XDG_STATE_HOME/claws/`, which is a local-only git repo auto-committed after
every run so memory changes can be reviewed and rolled back. Each commit
holds only what its writer owns: a run, its claw's dir; `bakery wontfix`,
`_shared/wontfix.json`; setup, the files it seeds. The operator commits
their own edits (eg. to `_shared/OPERATOR.md`).

```
$XDG_STATE_HOME/claws/
├── _shared/OPERATOR.md      # facts about the operator, injected into every profile
├── _gateway/                # gitignored: run ledger (runs.db), control socket, bakery sockets, sandbox violations
└── <name>/
    ├── MEMORY.md            # curated long-term memory
    ├── memory/YYYY-MM-DD.md # daily notes (kept forever)
    ├── sessions/            # gitignored: pi session transcripts
    ├── clones/              # gitignored: the claw's clones, as <owner>/<repo>
    ├── home/                # gitignored: the claw's XDG config, cache, and data
    └── tmp/                 # gitignored: its pi children's $TMPDIRs
```

The interactive profile keeps using `$XDG_STATE_HOME/pi/{sessions,bakery}`.
`bakery list|show|read|send|pin|attach` cover both the interactive and the
claw locations (claw sockets live in `_gateway/bakery/<claw>/`, named after the work
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
path = "${XDG_STATE_HOME}/claws/_shared/OPERATOR.md"
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
`IDENTITY.md` → `_shared/OPERATOR.md` → the claw's `AGENTS.md` (every turn),
then `MEMORY.md` and the two most recent daily notes (session start only).

Every profile uses the same rule fragments, including the "ask me" rules in
`pi/rules/workflow.md`. Each claw's `AGENTS.md` defines what asking means
for it (see [Ask policy](#ask-policy)).

Shared extensions' npm dependencies are declared in `pi/package.json` and
locked in `pi/bun.lock`; run `bun install --frozen-lockfile --cwd pi` after
cloning or when the lockfile changes (dotsystem's `sync` does this). The
`typecheck` prek hook runs `tsc` over every extension against the
`@earendil-works/pi-coding-agent` devDependency, which is pinned to the pi
version CI installs; pi supplies its own copy at runtime.

## Memory

Memory lives in the claw's state dir (`$BAKERY_MEMORY_DIR`), which claws
read like any other files, and is meant to be written through tools
(`pi/claw-extensions/memory/`). Bash shares pi's sandbox, which can write
the claw's state dir, so this is convention rather than enforcement; the
state repo's commits keep every change reviewable.

- `memory_append(text, source)` - a timestamped entry in today's
  `memory/YYYY-MM-DD.md`, tagged with its memory source: `operator`, `self`,
  or `external`.
- `memory_edit(edits | content)` - `MEMORY.md` only: exact replacements or a
  full rewrite, capped at `[memory] max_chars` (20k, its context budget).
  Every run has it, but its description says it is only for the dream and
  learn jobs.

Curation:

- **Flush**: once the context is within
  `[memory] flush_margin_tokens` (8k) of pi's compaction threshold, the
  memory extension injects a hidden message at `turn_end` asking the model to
  save anything durable with `memory_append` and reply `NO_REPLY`. It fires
  once per compaction cycle, as an ordinary model turn using the claw's own
  model and context, so its cost is accounted.
- **Dream job**: defined once in `claws/defaults.toml` (03:00 daily,
  `prompt = "/dream"` from `pi/claw-prompts/dream.md`, Opus with medium
  thinking, `silent_ok`). It distills recent
  daily notes into `MEMORY.md` and never promotes `external` notes, which
  stay in the daily notes as evidence; its report lists what it held back.
- **Teach**: `bakery teach <claw|all> "<lesson>"` runs each claw's `learn`
  job (`claws/defaults.toml`: no cron, Opus with xhigh thinking, `ask =
  "assume"`; `pi/claw-prompts/learn.md`) with the operator's lesson
  appended. The claw verifies the lesson where it can, rechecks the memory
  it bears on, corrects `MEMORY.md`, and appends an `operator` note so its
  next dream keeps the correction; it never asks, since the operator is
  waiting. The CLI waits for every run (`--no-wait` does not) and prints
  each reply and the `MEMORY.md` diff from the run's state commit; it exits
  non-zero if a run did not settle. Ctrl-C stops waiting, not the runs.
  `all` teaches every enabled claw at once, skipping paused ones. Baker's
  tasks give lessons as ready-to-run `bakery teach` lines
  (`bakery/bakery/gateway/teach.py`).
- **Change feed**: the dream's collector (`changes`,
  `bakery/bakery/collectors/changes.py`) hands it the operator's commits to
  the bakery checkout the claws run from (its HEAD, unmerged work included;
  bots' commits left out), since the HEAD the claw's last settled dream
  covered (its `bakery_commit`), or from the last 14 days when there is none
  or it was rewritten away. The dream rechecks every memory line those
  commits could have made wrong (eg. a widened sandbox, a removed tool, a
  new scout exclude), reading them read-only in that checkout, and fixes or
  removes it. A dream that does not settle leaves the base where it was.
- **Memory rules** (`pi/claw-rules/memory.md`, in every claw's context, since
  every run writes the notes a dream promotes): leave out volatile
  references (task ids, line numbers, counts); promote a rule only from the
  operator or two separate occasions (routing and scope rules only from the
  operator); never keep sandbox or tool limits, which the operator changes
  often (the sandbox's standing facts are in `pi/claw-rules/sandbox.md`,
  beside the code that sets them); date facts about the outside world, and
  move a date forward only after a recorded check; recheck or remove lines
  dated more than 14 days ago; let a later `operator` note win over earlier
  `self` ones; remove a line something may have made wrong when it cannot
  be verified; keep `MEMORY.md` under about 10k characters, half its cap.
  `MEMORY.md` is a cache of what is still true: a missing fact is found
  again, a false one is believed. These rules are in prompts, since a tool
  cannot judge text.
- **Commits**: after every run the gateway commits the claw's state dir
  (`<work_key>: <status>`), and nothing else: runs of different claws
  overlap, so a run's commit holds exactly its own claw's changes, and every
  memory change can be reviewed and reverted. Commits run one at a time, as
  git allows one writer per index.

Identity changes need the operator's approval:

- `_shared/OPERATOR.md` is private (the state repo is local-only) and claws
  cannot write it. `propose_operator_update(edits, reason)` files a
  `bakery > human` task with the diff (checked to apply, and to stay within
  4000 characters), which the operator applies by hand; the same proposal is
  the same task (its link is `operator-md:<hash of the result>`).
- `SOUL.md` / `AGENTS.md` (and other claw configuration) edits are proposed
  as PRs on this repo from the claw's own clone (see
  [Safety](#safety)), merging being the approval.

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
extension still loads (sockets under
`$XDG_STATE_HOME/claws/_gateway/bakery/<claw>`). The child runs through the
claws' sandbox launcher (see [Safety](#safety)); one whose command or
environment (and so sandbox or policy) changed since it started, eg. after a
`bakery gateway reload`, is replaced at its unit's next run, resuming the
session from disk.

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

A unit of work is stored as one pi session and shown as one Discord thread
in the claw's channel.

Work keys look like `scout/daily-20261002-0700`, `triage/task-151`, or
`discord/<thread-id>`; a unit's pi session id is its work key with `/`
replaced by `-`, which is also the session's name (so `bakery read
triage-task-151` works). Resuming a unit starts pi again with the same
`--session-id`, which restores it from the claw's `sessions/` dir. Runs of
one unit never overlap.

- A cron, heartbeat, queue, or manual trigger starts a fresh session and opens
  a thread; replies in that thread continue the same session.
- A top-level message in a claw's channel starts a new thread and session.
- For `triage` and `build`, one task is one unit of work for its whole life
  (including PR feedback). For `scout`, one run is one unit of work.

Continuity between units of work comes from memory files, not long-lived
transcripts.

### Triggers

- **collectors**: a job may name a `collector` (`bakery/bakery/collectors/`),
  deterministic Python run in a worker thread before the job's run. Its
  output is appended to the job's prompt; if it finds nothing, there is no
  run, unless the job sets `skip_empty = false` (eg. the dream, which runs
  every night, with or without a change feed). One collection or run per
  claw and job at a time, so the same
  candidates are never handed over twice (a manual trigger while one is
  under way is refused). `bakery status` shows collections in progress, and
  a manual trigger says when it is collecting. Problems are posted to
  `#bakery`. A collector may also name the run's unit of work (eg. one per
  task, `triage/task-151`), so a task keeps one unit of work.
  Job starting lives in `bakery/bakery/gateway/jobs.py`.
- **templates**: a run's opening prompt that names a claw template
  (`/dream`, from `pi/claw-prompts/`) is expanded by the gateway, which
  appends any text after it (a collector's output, the operator's words)
  verbatim; pi would shell-parse that text and drop it, since the templates
  take no arguments. This covers job prompts, `bakery trigger --prompt`, and
  Discord messages that start a run; replies steered into a running unit are
  left to pi (`bakery/bakery/gateway/prompts.py`).
- **repeat**: a job with `repeat = true` starts again after each settled run
  (not after replies or resumes), until its collector finds nothing, the
  claw is paused, or its budget runs out; for working through a queue. If
  the collector hands back the unit that just settled, the run did not get
  anywhere: the repeat stops with a notice rather than looping.
- **cron**: 5-field expressions in `claw.toml`, Europe/Lisbon, optional
  `active_hours`. Missed runs (Mac asleep, gateway down) are skipped. A job
  without one runs only when triggered (eg. `learn`). Jobs in
  `defaults.toml`'s `[claw]` table apply to every claw (a claw's job of the
  same name replaces one); a job may override `model`, `thinking`, and the
  claw's ask policy (`ask`) for its units of work, which keep them on later
  runs (eg. replies).
  `bakery trigger <claw>` runs the claw's only own job; shared jobs (eg.
  `dream`) must be named.
- **heartbeat**: a cron job flagged `silent_ok` (eg. every claw's dream,
  triage's `recheck`, and baker's `review`); a run whose only reply is
  `NO_REPLY` posts nothing and creates no thread. A `NO_REPLY` message never
  becomes any run's reply.
- **file watch**: `watch = "<path>"` starts the job once files under the
  path have changed and then been quiet for `debounce_seconds` (default
  120). The gateway polls names, sizes, and modification times every 10s
  (`bakery/bakery/gateway/watcher.py`); nothing fires at startup.
- **manual**: `bakery trigger <claw>` or the Discord equivalent.
- **Discord messages**: the operator's top-level messages and thread
  replies.

### Concurrency

Configurable in `claw.toml`; defaults:

- at most 4 pi processes globally,
- at most 1 active new run per claw (others queue),
- `build` may additionally run up to 2 PR-feedback resumes concurrently,
- idle RPC children exit after 15 minutes and resume from their session file
  on the next thread reply.

### Limits and accounting

A SQLite run ledger at `$XDG_STATE_HOME/claws/_gateway/runs.db`, one row
per run, including the bakery checkout's HEAD its input was gathered
against (`bakery_commit`: at collection for jobs with a collector, else when
the run starts). Defaults, all configurable per claw:
$5, 30 minutes, and 50 turns per run; $20 per claw per day; $50 per day
globally. Daily budgets reset at midnight in the gateway timezone.

A run's cost budget is the smallest of its per-run limit and what remains of
the claw's and the global daily budgets. Cost is the change in pi's session
stats (`get_session_stats`) over the run, reconciled after every turn and
compaction, so compaction summaries count too; each assistant message is also
charged provisionally so a costly reply stops the run before its tool calls
execute. The time limit excludes time spent waiting on the operator in a
dialog.

A run ends `settled`, `parked`, `failed`, or `interrupted`. `bakery resume
<work-key>` continues a parked run. A model error fails a run only after
pi's own retries.

### Ask policy

Each claw sets its ask policy: `ask`, `assume`, or `park`. With `ask`, the
question is posted to the thread (the `ask_operator` tool); its timeout
(default 4h) and its fallback, `assume` or `park`, are also set per claw.

`ask_operator` (`pi/claw-extensions/ask-operator.ts`) reads the policy from
`BAKERY_ASK_*` variables the gateway sets per child. It asks through a pi
`input` dialog with the timeout attached; cancelling counts as no answer.
Parking ends the run without another model call (`terminate`) and returns
`details.park`, which the runner turns into a parked run (`waiting for an
answer: …`); replying in the thread then resumes the unit with the
operator's reply as the prompt. A question that times out is labelled `timed
out` in Discord even when pi's own dialog timeout, which starts earlier, ends
the run first.

### Claw environment

A claw's pi child gets only `PATH`, `HOME`, `USER`, `LANG`, `TASK_FOLDER`,
`XDG_*` (three replaced, below), and `LC_*` from the gateway's environment,
plus its `secrets`: entries `NAME` or `NAME=ITEM`, setting variable NAME
from secret ITEM (default: NAME). The default is `ANTHROPIC_API_KEY` and
`GH_TOKEN=BAKERY_GITHUB`. Secret values come only from the Keychain (generic
password, account `bakery`, service = ITEM), never the gateway's environment,
which holds all of the operator's shell's secrets when the gateway is
started from a shell. A missing secret fails the run. Secrets are handed to
pi and its extension tools, not to bash (see [Safety](#safety)).

The gateway also reads secrets of its own from the Keychain, by fixed item
names, and never hands them to a claw: `DISCORD_BOT_TOKEN`, and
`BAKERY_GITHUB` for collectors (`bakery/bakery/collectors/base.py`), which
only ever make GET requests and git fetches with it.

`XDG_CONFIG_HOME`, `XDG_CACHE_HOME`, and `XDG_DATA_HOME` point into the
claw's own `home/` (in its state dir), so tools find an empty config and
keep their caches there instead of failing on the unreadable real ones under
`$HOME`. Tools that read `~/.something` directly still get "permission
denied". The sandbox launcher creates each child's `$TMPDIR` in the claw's
`tmp/` and removes it when the child exits.

### Control

The control socket backs `bakery trigger|teach|pause|resume|status|budget`
and `bakery gateway reload` (a request may wait, eg. `teach_result` on a
run; stopping the gateway ends such waits); the slash commands in `#bakery` (`/pause [claw|all]`,
`/resume`, `/status`, `/budget`) call the same operations in-process.
`pause` stops new runs; `pause --abort` also parks in-flight ones. `resume`
takes a claw, `all`, or a work key (re-running a parked unit).

## Discord

One bot application in a private guild; the gateway creates what is missing
on connect:

- a `bakery` category,
- `#bakery` - gateway notices (start/stop, rejected reloads, exhausted daily
  budgets) and slash commands,
- `#bakery-<claw>` per enabled claw (eg. `#bakery-scout`), each with one
  bot-owned webhook so the claw posts under its own name (and
  `claws/<name>/avatar.png`, if present). Claws added later need a gateway
  restart for their channel.

Only the operator's user ID is acted on (others' messages are ignored and
their clicks and commands refused); bots, webhooks, and DMs are ignored.
Settings live in `claws/defaults.toml` as `[gateway.discord]` (`guild_id`,
`operator_id`); the bot token is the `DISCORD_BOT_TOKEN` secret.

Routing:

- The operator's top-level message in `#bakery-<claw>` starts a thread on it;
  the work key is `<claw>/thread-<thread-id>` and the message is the prompt.
- Gateway-started work (cron, manual, queues) gets a thread named after its
  work key. Work-key ↔ thread mappings are kept in `runs.db`.
- The operator's reply in a thread answers a pending `input` dialog if there
  is one, else steers the in-flight run, else queues a run with the reply as
  its prompt. Replies are acknowledged with a reaction.
- `silent_ok` runs create no thread unless they produce something other than
  `NO_REPLY` or need attention.

In each thread:

- one status message per run, edited at most every 5s
  (`⏳ <key> · <tool> · turn 3 · $0.42 · 2m10s`) and replaced by the outcome;
- the final reply, in ≤2000-character chunks, or attached as `reply.md` if it
  would take more than 4 messages;
- for parked, failed, or interrupted runs, an @mention with the reason and a
  **Resume** button;
- dialogs, @mentioning the operator: `confirm` as Approve/Deny; `select` as
  buttons (up to 5 options) or a select menu; `input`/`editor` as "reply in
  this thread", each with Cancel. Answered, timed-out, and cancelled dialogs are edited to
  say so and lose their buttons. Buttons survive restarts (discord.py
  `DynamicItem`s); clicking one whose request is gone says it expired.

Slash commands (guild-scoped): `/status`, `/budget`, `/trigger claw [job]
[prompt]`, `/pause target [abort]`, `/resume target`, `/reload`.

Code: `bakery/bakery/chat/` - `relay` (all behaviour; implements the
gateway's `Channel`), `transport` (the interface), `discord_transport`
(discord.py, kept thin), and `render` (text, shared with the CLI).

### Running it

1. One-time Discord setup (done by hand): a `bakery` application whose bot
   has Public Bot off and the Message Content intent on; the bot invited to
   the operator's server with the `bot` and `applications.commands` scopes and
   permissions to view, manage, and post in channels and public threads,
   manage webhooks, read history, attach files, and add reactions; its token
   stored as the `DISCORD_BOT_TOKEN` Keychain secret; and the server's and the
   operator's IDs in `[gateway.discord]`. `bakery gateway check` verifies it
   (connects, creates the layout, posts to `#bakery`), and reports
   collector settings only the outside world can show to be wrong (eg. a
   `scout.toml` override for a repo the claws cannot reach).
2. `bakery service install|uninstall|restart` manages the launchd agent
   `in.thekev.bakery` (`~/Library/LaunchAgents/in.thekev.bakery.plist`):
   `bakery gateway run`, kept alive, with `PATH`, `HOME`, `LANG`,
   `TASK_FOLDER`, `XDG_*`, and `BAKERY_REPO` recorded at install time.
   Install from the pipx `bakery` so the plist points at a stable path.
3. Logs: `$XDG_STATE_HOME/claws/_gateway/gateway.log` (rotating), and
   `launchd.log` for anything before logging starts. SIGHUP reloads config.

## Safety

Prompt-level rules are not enforcement. Each claw's `[policy]` (defaults in
`claws/defaults.toml`, overridable per claw; there are no named tiers; a
claw's value for a key replaces the default list) is enforced by the OS
sandbox the claw's whole pi runs in, and its GitHub token limits what it can
do there. Nothing else is a boundary: bash shares pi's sandbox, so anything
pi's process could refuse, bash could do anyway. So:

- Every claw has pi's default tools (`read`, `bash`, `edit`, `write`) and
  every extension tool its profile loads. Which to use, and how, are rules
  in its instructions.
- Which tasks a claw may change is the sandbox's: it writes only the claws'
  namespace of the task list. The task tools' checks within it (claims,
  `[tasks] tag`, blocked lists) are guard rails on the easy path;
  `pi/claw-rules/tasks.md` tells every claw to change tasks only through
  them.
- `memory_edit` is meant only for the dream job, by its description.

`pi/claw-extensions/policy/`, which the gateway always loads with
`--extension` so no profile can omit it, keeps from bash the secrets it is
not handed (see below) and notes sandbox refusals. The gateway passes it `BAKERY_POLICY`;
without a valid one, pi fails to start and every tool call is blocked.

The `[policy]` keys:

- `read_paths`: the only readable paths under `$HOME`, besides what pi needs
  to run (below). Default `$TASK_FOLDER`; baker adds the claw state root.
  The operator's checkouts are never among them (claws read repos from
  their own clones, which reflect GitHub rather than unpushed work); the
  bakery repo is readable only because pi runs from it. Outside `$HOME` everything is readable except
  per-user temp (`/var/folders/...`) and the claw state root, of which a claw
  reads only its own state dir and `_shared`.
- `deny_read`: paths and globs unreadable even within `read_paths`:
  `~/**/.env*`, `~/**/*.tfvars`, `~/**/*.pem`, SSH key names
  (`~/**/id_rsa*` etc.), `interactive/auth.json`, and other claws' pi
  credentials (`claws/*/auth.json`; the gateway re-allows the claw's own).
- `write_paths`: writable besides the claw's own state dir, pi's files in its
  profile, and its `$TMPDIR`. Default `$TASK_FOLDER/bakery`, the claws'
  namespace of the task list (see "Shared task list"). The memory tools are
  the intended writers of memory, but bash could write it too.

A claw's `[tasks] tag` is the section of the claws' namespace (`bakery`,
`store.NAMESPACE`) that `task_add` files into (default `triage`); the tool
does not let the model choose another.

Enforcement:

1. **The sandbox.** The gateway runs each pi child through
   `pi/claw-sandbox/launch.mts`, which runs it inside Anthropic's
   `sandbox-runtime` (sandbox-exec; bakery is macOS-only, and CI runs on
   macOS) with settings built by `bakery/bakery/gateway/sandbox.py`. pi and everything it
   starts (bash, `gh`, `rg`, `task`) share that one sandbox:
   macOS cannot nest sandboxes, so nothing inside gets a stricter one.
   - Reads: `$HOME`, per-user temp, the claw state root, and
     sandbox-runtime's shared `/tmp/claude` are denied; `read_paths` and
     what pi needs to run are re-allowed; `deny_read` applies within them.
     What pi needs: the bakery repo, the claw's profile and its own pi
     credentials, its state dir, `_shared`, its socket dir, macOS's global
     preferences, and the tools pi and the claw extensions run (`pi`,
     `node`, `rg`, `fd`, `gh`, `git`, `task`, `bakery`), each through its
     link on `PATH` and the installation it resolves into (a venv's base
     interpreter and the source of its editable installs included).
   - Writes: `write_paths`, the claw's state dir and socket dir, its
     `$TMPDIR`, and exactly the files pi writes in its profile (`auth.json`,
     `models-store.json`, `settings.json`, and their locks); the rest of
     the repo is read-only. sandbox-runtime also always blocks writes to
     `.git/hooks`, `.git/config`, and shell rc files.
   - Network: through the sandbox's proxy, which allows every host to pi
     and bash alike (its allowlist is empty; a callback in `launch.mts`
     approves every host) but refuses hostnames other than `localhost` and
     `*.localhost` resolving to loopback, link-local, this Mac's own
     addresses, or cloud metadata endpoints. IP literals are dialed as
     given, so LAN hosts (and this Mac, by IP) are reachable. Loopback is
     also reachable directly (`allowLocalBinding`; Go never proxies
     localhost), and processes may listen on local ports. Node's `fetch`
     uses the proxy (`NODE_USE_ENV_PROXY=1`); private addresses go through
     it too rather than around it. The sandbox needs a network config to
     keep the proxy at all: without one, sandbox-runtime allows all
     network, unix sockets included. Unix sockets are blocked except the
     claw's own socket dir (so no ssh-agent or gateway control socket).
   - Go programs (`gh`, `ketch`) verify TLS through macOS's trust service,
     `com.apple.trustd.agent`, which `enableWeakerNetworkIsolation` makes
     reachable. That opens no Keychain access: `gh` sees only the claw's
     `GH_TOKEN`, never the operator's own token.
   - Other processes: `ps` sees only the sandbox's own processes, and the
     Keychain is unreachable.
   - What it blocks (denied reads, writes, and network) is appended to
     `_gateway/violations/<claw>/<session>.jsonl`, out of the claw's reach,
     for baker. A tool result showing a refusal (`Operation not permitted`,
     `EPERM`, or the proxy's 403) gets a note from the policy extension that
     it is policy rather than missing auth or files.
   - `bakery sandbox show <claw>` prints its settings (with the caller's
     `$PATH`); `bakery sandbox exec <claw> -- <command>` runs a command as
     the claw's pi would, in its sandbox and environment, secrets included.
2. **Secrets** reach pi's environment; bash only gets a claw's
   `bash_secrets` (default `GH_TOKEN`). That is hygiene, not a boundary:
   bash shares pi's sandbox and could read pi's environment.
3. **Repositories and pushing.** Claws read and change repos only in their
   own clones, `<state>/clones/<owner>/<repo>`, cloned from GitHub, fetched
   and checked out at `origin/HEAD` before every use, with a `git worktree`
   per piece of work (`pi/claw-rules/repos.md`, in every claw's context).
   That includes bakery, whose checkout pi runs from may hold
   the operator's unmerged work. It commits on `kjames/bakery-*` branches, pushes over HTTPS, and
   opens PRs with `gh`; the operator reviews and merges. The gateway writes
   the claw's git config (`<state>/home/config/git/config`, its `GIT_CONFIG_GLOBAL`, so git never
   trips on an unreadable `~/.gitconfig`) at every start: the claws' GitHub
   user's identity (`[claw.git]`), no signing, no hook templates (the
   sandbox refuses writes to any `.git/hooks`, but allows `.git/config` in
   the claw's own clones), and `gh` as the only credential helper, answering
   with `GH_TOKEN`. GitHub's rulesets, not the claw, keep pushes off the
   operator's default branches.

An extension that fails while pi starts (eg. a bad policy or `context.toml`)
fails the run before the model is called.

Credentials (macOS Keychain, least privilege, selected per claw by `secrets`):

- `DISCORD_BOT_TOKEN` (gateway only), `ANTHROPIC_API_KEY`.
- `BAKERY_GITHUB`: a classic PAT (`repo` scope only, one-year expiry) of the
  claws' own GitHub user, a collaborator on the public `TheKevJames`
  repositories they work on. Collaborators on a personal account's
  repositories always have write access, and fine-grained PATs can't reach
  another user's repositories, so GitHub enforces the limits instead: the
  rulesets every repository gets from the operator's repo template
  (dotsystem's `repo-template`) require a PR approved after its last push for
  the default branch. Tags are not protected yet, so the user can create and move them
  (and so trigger tag-driven releases). The user has no admin rights. Private
  repositories have no rulesets on GitHub Free, so the user is never invited
  to them.

`web_search` and `web_fetch` (`pi/extensions/ketch.ts`, running
[ketch](https://ketch.run) from `pi/package.json`: search falls back
through keyless hosted providers, fetch extracts markdown locally, with
unpdf as its PDF converter; no API key and no headless browser, so pages
rendered by JavaScript come back nearly empty), and `context7`
(`pi/extensions/context7.ts`, Context7's search API, unauthenticated)
replace the former skills in every profile.

## Shared task list

Claws share the operator's existing `task` tool
(`~/src/personal/tools/task`, data in `$TASK_FOLDER`) with the operator.
What claws rely on:

- tasks live in namespaces, one directory each: `$TASK_FOLDER/<ns>/tasks.db`
  (SQLite, rollback journal so the Syncthing backup only ever sees
  self-contained files), with each description a plain `<id>.txt` beside it,
  editable by hand. `default` is the operator's; `bakery` is the claws';
- a task is addressed as `<ns>:<id>` (ids are unique within a namespace, and
  never reused); a bare id means `default`. `set -n <ns>` moves a task under
  a new id, keeping its tag, and refuses an owned task;
- writers take `BEGIN IMMEDIATE` on the namespaces they write, so the
  operator and claws serialize and lose nothing; readers take no lock;
- tags are flat lowercase names (`--tag build`), sections of a namespace;
  `list` covers every namespace, and `-f namespace=bakery,tag=build`
  narrows it;
- `owner`, `link`, `priority` (low, medium, high), and `size` (small, medium,
  large) fields; `set --owner X` is a compare-and-set (fails if owned by
  someone else unless `--force`); `unset owner` releases;
- `done` deletes a one-off task, with its description, and advances a
  recurring one;
- `created` and `updated` timestamps, owned by the store: every change to a
  task bumps `updated`;
- `set --description-append`, `add --link`, `owner`/`link`/`priority`/`size`
  filters, `--json` on `list` and `show`;
- `--json` on every write, printing the task as it stands after the command
  (`null` once `done` deletes it), so tools need no follow-up read;
- property tests (DB round-trip, SQL filters against a reference; concurrent
  writers and moves lose nothing).

Claws read it with `task list --json` and `task show --json` in bash, and
change it through tools in `pi/claw-extensions/task.ts`: `task_add` (with an
optional priority and size) and `task_set` (tag, claim, release, append
notes). Both address only the claws' namespace, `bakery` (`store.NAMESPACE`,
passed as `BAKERY_TASK_NAMESPACE`): `task_set` takes a bare id there.
`task_set` refuses to move a task to `blocked` unless `bakery blocked check`
accepts its blockers (see triage). Claims are always made as the claw itself
(`BAKERY_CLAW`), and `task_add` always files into the claw's `[tasks] tag`.
Which claw uses which tool is a rule in its `AGENTS.md`; no claw completes
or deletes tasks (a deleted task's source would be filed again).

Claws read every namespace but write only `bakery`: the sandbox grants
`$TASK_FOLDER` for reading and only `$TASK_FOLDER/bakery` for writing (see
the `[policy]` defaults), so the operator's tasks are out of their reach.
Within `bakery`, the tools' checks are guard rails: bash could still change
those tasks directly. To take over a task a claw owns, the operator
unsets its owner first.

### Task flow

Tags here are sections of the `bakery` namespace.

1. `scout` adds tasks to `triage` with a `link`. The operator hands a task
   over by adding it there too (`task add -n bakery`), or moving one of
   their own (`task <id> set -n bakery --tag triage`), usually without a
   `link`.
2. `triage` claims those (lowest id first), researches, appends notes, sets
   `priority` and `size`, then re-tags to `build`, `human`, `blocked`, or
   `wontfix` and releases.
3. The operator answers a `human` task by appending their answer to its
   description and moving it to `triage`, where triage re-routes it.
4. `triage` moves a `blocked` task back to `triage` once what it waits on
   clears (another task decided or done, or an upstream change), to be
   re-routed.
5. `build` claims `build` tasks (high priority first), moves them to
   `review` once a PR is open, and runs `done` after merge.
6. `baker` adds tasks about the claws themselves straight to `human`, with
   their priority and size; the operator moves them on.

The operator closes `wontfix` tasks, with `done`, after `bakery wontfix add
bakery:<id> --reason "…"` if the link should never be filed again. That
records a rejection: the link (without its `#…` fragment), the task's
summary, the reason, and the date, in
`$XDG_STATE_HOME/claws/_shared/wontfix.json`, committed to the state repo
(`bakery wontfix list` and `rm <link>` read and undo them;
`bakery/bakery/task_list/wontfix.py`). Any task with a link can be
rejected, whatever its namespace or tag. Scout and baker skip links on open
tasks (in any namespace) and on rejections, so done tasks free their link: a
gap which comes back after it was fixed is filed again. A `blocked` task is
open, so its link is not filed again either, and its source stays as it is.

## Claws (v1)

All configuration (models, limits, schedules, ask policy, concurrency) lives
in each claw's `claw.toml`. Models have no default: every claw sets `model`
and `thinking` (one of pi's levels, `off` to `max`).

### scout - read-only GitHub

Finds things in the operator's repos that belong on the task list. Profile:
`claws/scout/` (`claw.toml`, `scout.toml`, `AGENTS.md`, `SOUL.md`,
`IDENTITY.md`, `context.toml`).

- The `daily` job (07:00, or `bakery trigger scout`) uses the `scout`
  collector (`bakery/bakery/collectors/scout.py`) over every unarchived
  repo the claws' GitHub user can reach (`GET /user/repos?affiliation=
  collaborator`): inviting it is how a repo is added, so the public
  `TheKevJames` repos with rulesets, and no private ones. `scout.toml`
  holds per-repo `exclude` globs by name, and `ignore_ci_lines`. The
  collector keeps scout's own clone of each, in scout's `clones/`: an
  ordinary clone, as `pi/claw-rules/repos.md` has the model use, from which
  the collector only reads refs:
  - **CI**: the latest completed default-branch run of each workflow failed
    (Dependabot's own update jobs excluded). Link: the first failed run of
    the current failure streak, so a fresh failure after a fix is new.
  - **Passing runs' logs** (`scout_logs.py`): each workflow's latest
    passing run, unless its workflow file has changed on the default branch
    since its commit (or its commit is gone), so a fix that has not run yet
    does not resurface its old findings. Only annotations the runner
    rendered (`##[warning]…`, `##[error]…`) count, never a raw
    `::warning::`/`::error::`: the runner prints each step's script before
    running it, so an `echo "::error::…"` appears whether or not it ran.
    Lines matching any of `scout.toml`'s `ignore_ci_lines` regexes (eg. brew
    test-bot's cleanup noise) are dropped first, so they never take up a
    per-repo cap. The detail names the run, its commit, and its date.
    - **Masked failures**: `##[error]` lines, from a tool, or `Process
      completed with exit code N.` from a step allowed to fail. One per
      message per run, its detail listing every job and step (`Run …`
      header) it was in; at most 20 per repo. Link: the workflow URL with
      `?error=<fingerprint>`.
    - **Warnings**: `##[warning]`, `DeprecationWarning`, `FutureWarning`,
      and `PendingDeprecationWarning` lines, deduplicated across the repo's
      runs, at most 20 per repo. Node deprecations (`(node:<pid>) [DEP…]`)
      printed inside a third-party action's step or a post step are
      dropped: they come from the action's bundled dependencies, which Node
      does not treat as `node_modules`, so only its maintainers can fix
      them. Node's pid prefix is stripped before fingerprinting, as it
      differs every run. Link: the workflow URL with
      `?warning=<fingerprint>`.
  - **Issues and PRs**: all open issues, and PRs not by bots, except those
    labelled `ready-for-human`. Link: their URL.
  - **TODO/FIXME comments** on the default branch, fetched with scout's
    token over HTTPS into scout's clone, minus per-repo `exclude` globs.
    Only mentions inside comments count: Pygments lexes each file, picking
    its lexer by file name, so strings, docstrings and prose are skipped;
    files with no lexer keep every mention. One task per comment. Link: `…/blob/<branch>/<path>?todo=<fingerprint>#L<line>`;
    the fingerprint is of the comment's text, so the line can move.
- **Lineage, not memory**: a candidate is new unless an open task or a
  rejection (`bakery wontfix`) has its link (ignoring the `#L…` anchor).
  There is no "seen" store, so a done task comes back if its source still
  exists and was not rejected. Scout therefore never merges or drops
  candidates and copies links verbatim.
- At most `max_candidates` (25) per run, ordered CI, masked failures, PRs,
  issues, warnings, TODOs; the rest wait for the next run. Repos are
  collected in parallel (8 at a time). Repos that cannot be read are reported in `#bakery` and in
  scout's report; the others still run.
- Scout reads untrusted issue and PR text, so the model's `GH_TOKEN` is
  `BAKERY_GITHUB_READONLY`: a classic PAT with no scopes, which reads public
  data and can list the repos its user collaborates on, but cannot write.
  The collector, which runs in the gateway and never acts on what it reads,
  uses the gateway's `BAKERY_GITHUB`: GitHub refuses Actions log downloads
  ("Must have admin rights to Repository") to a token with no scopes, even
  on public repos and despite its docs.
- The model (Haiku, high thinking, $2 per run, ask policy `assume`) writes
  each candidate up with `task_add` (into `bakery > triage`) and reports what
  it added.

### triage - read-only + web

Profile: `claws/triage/`. Sonnet, high thinking (Haiku, low thinking, for
`unblock`), $3 per task (and per `unblock` or `recheck` run).

- The `queue` job's `triage` collector (`bakery/bakery/collectors/triage.py`)
  hands over the lowest-numbered unowned task tagged `bakery > triage`
  (scout's, the operator's handed over, and tasks sent back), skipping
  scheduled (recurring) ones, as the unit `triage/task-<id>`. It runs hourly,
  within two minutes of `$TASK_FOLDER` changing (scout or the operator adds
  tasks), manually
  (`bakery trigger triage queue`), and on repeat until the queue is empty.
- Per task: claim it, check the source still exists, research (code,
  history in its clones, issues, docs, web; no reproduction, tests, or
  edits), append notes
  (route and reason, source check, code locations, hypothesis, approach,
  acceptance criteria, open questions), always set `priority` and `size`,
  then re-tag and release:
  - `bakery > build`: a TheKevJames repo the claws can push to, clear
    scope, checkable by tests, lint, or CI;
  - `bakery > human`: needs a decision on this task, reproduction,
    credentials, outside systems, or anything uncertain;
  - `bakery > blocked`: valid and scoped, but waiting on something checkable:
    an upstream change, another task, or the operator's decision on another
    task; and every duplicate of an open task, which waits on its canonical
    being decided (or done, for the operator's own tasks);
  - `bakery > wontfix`: false positive, done, or obsolete (including a
    duplicate of a `bakery > wontfix` task), with what to change at the
    source.
- A blocked task's notes end with a `**Blocked on:**` list, one blocker per
  line: `#<ns>:<id> decided` (refused for the operator's own tasks, outside
  `bakery`, which record no decisions), `#<ns>:<id> done`, or
  `<url>: <condition>`. The last list
  counts; the task clears once all of its blockers have.
  `bakery/bakery/task_list/blocked.py` parses and checks it, for `bakery
  blocked check` (run by `task_set` before it moves a task to
  `bakery > blocked`) and for the collectors below. Done tasks are deleted,
  so a task which does not exist counts as done. A list is refused if it is
  malformed, names the task itself, or names a task blocker which has
  already cleared, including one which does not exist (triage routes on the
  merits instead).
- The `unblock` job's `triage-unblock` collector hands over, in one run,
  every unowned blocked task whose blockers are all tasks and have all
  cleared; triage appends an `## Unblocked` note and moves each to
  `bakery > triage`. It runs hourly and within two minutes of `$TASK_FOLDER`
  changing, so a task clears soon after its blocker is decided.
- The `recheck` job (Mondays at 06:00, silent when nothing clears) uses the
  `triage-recheck` collector: unowned blocked tasks with an external
  blocker whose task blockers have cleared, grouped by URL so each is
  checked once, in one run. Triage moves those whose conditions all hold to
  `bakery > triage` likewise. The collector also reports blocked tasks it
  cannot read (eg. moved by hand without a valid list) in `#bakery`; weekly,
  rather than hourly in `unblock`, so they do not flood it.
- Task tools: `task_set` only; it never adds tasks, which would file them
  into its own queue.
- Ask policy `ask`, falling back to `assume`; every question's assumption is
  "route this task to human", so an unanswered question routes it
  there and the reply says triage could not finish.
- A task left claimed by a failed or parked run is not picked up again;
  its thread's Resume button continues it.

### build - clones + PRs

- Picks `bakery > build` tasks by `priority` (`high`, then `medium`, then
  `low`), then lowest id.
- Works in its own clones (see [Safety](#safety)), a worktree per task;
  removed after merge/close.
- Pushes `kjames/bakery-*` branches and opens PRs; never merges.
- Polls its PRs every 10 minutes; new review comments or failing CI resume
  that task's unit of work in the same thread.
- Merged → `done`. Closed unmerged → `bakery > human` with a note, released.
- Task tools: triage's, plus `task_link` and `task_done`, to be added back
  to `task.ts` with build.
- Ask policy: `ask`, falling back to `park`.

### baker - reads every claw, files for the operator

Reviews how the claws learn and files a task for each gap. Profile:
`claws/baker/`. Opus, high thinking, $5, 45 minutes, and 100 turns per run.

- The `review` job (04:00, after the dreams) uses the `baker` collector
  (`bakery/bakery/collectors/baker.py`, transcripts parsed by
  `bakery/bakery/transcripts.py`). It digests everything since baker's last
  settled review:
  - runs by claw, job, and outcome;
  - tool calls and failures per tool;
  - calls the sandbox refused, and other failed calls, grouped,
    with how many units of work each spans;
  - everything the sandbox blocked (its violation logs), whether a tool
    result showed it or not, eg. a file pi newly needs to write;
  - memory sizes, `memory_append` sources, and the state-repo commits that
    changed `MEMORY.md` or `_shared`;
  - baker's own tasks, and rejections of its links;
  - gateway warnings and errors.

  Sections are capped (about 38k characters in all) and say how to read
  what they cut. No other claw ran since the last review means no run.
- Gap types, the `<type>` in each task's link `baker:<type>/<slug>`:
  `tool-gap`, `false-learning`, `no-actuator`, `operator-feedback`,
  `self-policy`, `memory-health`, `knowledge-silo`, `plumbing`, and `other`.
  A sandbox refusal becomes a `tool-gap` when it causes repeated failures
  (3+ units of work) or a wrong conclusion. Bash that stays out of protected
  paths and other unix sockets is fine.
- Baker verifies with the same tools the claws have, without changing
  anything, and says how. Each task records its evidence, impact, root
  cause, fix, and an acceptance check baker re-runs while the task is open.
- Its policy's `read_paths` cover the whole state root (every claw's memory
  and transcripts, `runs.db`, `gateway.log`); unix sockets stay blocked.
- `[tasks] tag = "human"`: fixes change the claws themselves, so they
  never go straight to build.
- Never `task_set`, by its `AGENTS.md`: changes to other claws' memory or to
  tasks are steps in a task's **Fix**, for the operator.
- It keeps its own memory (the slugs it filed). It never files a link
  already on an open task or a rejection.
- Ask policy `assume`; `silent_ok`, replying `NO_REPLY` when it filed
  nothing and none of its tasks is still open.

### Models

`claude-haiku-5-5` for scout and triage's `unblock`; `claude-sonnet-5-5`
for triage; `claude-opus-5-5` for build, baker, and every claw's dream. A
memory flush runs on its unit's model. The gateway runs whatever `pi` is on
`PATH`.

## Testing

- `task`: property tests (hypothesis).
- Extensions and the gateway: real `pi` against a fake OpenAI-compatible
  model (`bakery/testing/harness.py` plus `fake_provider.ts`), which records
  every request so tests can assert on the exact system prompt and
  transcript. The Discord adapter sits behind an interface with a fake
  implementation.
- Tests wait on events, never on time: a model call in flight is a held
  reply (released by the test, or ended by pi hanging up), and "X did not
  happen" is shown by waiting for a later event that X would have preceded.
  Tests observe the gateway from outside (control `status`, channel events,
  the ledger, logs), not its in-memory state.
- Hermetic: no real network, credentials, or Keychain items.
- The suite runs in parallel (pytest-xdist; `-n0` for serial) with a 30s
  per-test timeout, so tests share no state beyond their own temp dirs.
- One manual end-to-end smoke test against real pi on a cheap model.

## Delivery plan

1. bakery: import from dotsystem (history preserved), restructure,
   `bin/vendor`, this document.
2. dotsystem: point at bakery, pipx entry, trim `vendor`/`discover`/
   pre-commit.
3. tools/task: locking, atomic writes, conflict guard, tag paths,
   `owner`/`link`, filters, `--json`, `--description-append`, tests.
4. `context` extension and `OPERATOR.md`.
5. Gateway core: RPC client, scheduler, ledger, control socket, CLI.
6. Discord adapter and setup wizard; `bakery gateway run` and
   `bakery service install|uninstall` (launchd).
7. Policy extension, sandbox, secrets, web/context7/github tools.
8. Memory tools, flush, dream job, `OPERATOR.md` proposals.
9. scout.
10. triage.
11. build, on the claws' own clones and pull requests.
12. baker: `read_paths` and `task_tag` policies, sandbox violation
    reporting, the transcript parser and `baker` collector, and the claw.
