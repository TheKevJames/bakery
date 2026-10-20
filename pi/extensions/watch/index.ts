/**
 * Watch Extension
 *
 * A `watch` tool that waits in the background: `exit` mode runs a command
 * until it exits, `poll` mode re-runs a probe until it exits 0. When a watch
 * completes, its result arrives as a `watch` message: queued as a follow-up
 * while the agent is working, or starting a turn when the TUI is idle.
 *
 * Outside the TUI (subagents, claws, `pi -p`), nothing is around to start the
 * next turn, so a run about to settle with watches pending is held open until
 * the next one completes, which is then delivered as a continuation.
 *
 * A bash guard blocks the foreground waits `watch` replaces.
 *
 * Before spawning, the environment is emitted on `pi.events` channel
 * `watch:env` as `{ env }`; listeners may edit it in place (eg. the claws'
 * policy strips secrets). Each change in the number of active watches is
 * emitted on `pi.events` (see ./api.ts).
 */
import type { ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";
import { statSync } from "node:fs";
import { resolve } from "node:path";
import { Type } from "typebox";
import { WATCH_ACTIVE_CHANNEL, type WatchActiveEvent } from "./api.ts";
import { registerGuard } from "./guard.ts";
import { completionMessage, duration, registerRenderer } from "./render.ts";
import { type Completion, type Spec, Watch } from "./watcher.ts";

const ENV_EVENT = "watch:env";

const MAX_ACTIVE = 5;
const DEFAULT_INTERVAL_SECONDS = 30;
const DEFAULT_TIMEOUT_SECONDS = 60 * 60;
const STATUS_KEY = "watch";

type CancelledBy = "agent" | "operator" | "shutdown";

function text(value: string) {
  return { content: [{ type: "text" as const, text: value }], details: undefined };
}

function describe(watch: Watch): string {
  const { mode, label, command, cwd } = watch.spec;
  return `${watch.id} "${label}" (${mode}, ${duration(Date.now() - watch.startedAt)}): ${command} [in ${cwd}]`;
}

function directory(cwd: string, path: string | undefined): string {
  const dir = resolve(cwd, path ?? ".");
  if (!statSync(dir, { throwIfNoEntry: false })?.isDirectory()) throw new Error(`not a directory: ${dir}`);
  return dir;
}

export default function watchExtension(pi: ExtensionAPI) {
  const watches = new Map<string, Watch>();
  const cancelledBy = new Map<string, CancelledBy>();
  let nextId = 1;
  let mode: ExtensionContext["mode"] = "print";
  let ctx: ExtensionContext | undefined;
  let settle: ((completion: Completion | undefined) => void) | undefined;

  function updateStatus() {
    if (mode !== "tui" || !ctx) return;
    ctx.ui.setStatus(STATUS_KEY, watches.size ? `⏱ ${watches.size}` : undefined);
  }

  function watchesChanged() {
    const event: WatchActiveEvent = { count: watches.size };
    pi.events.emit(WATCH_ACTIVE_CHANNEL, event);
    updateStatus();
  }

  function deliver(completion: Completion) {
    if (settle) {
      settle(completion);
      settle = undefined;
      return;
    }
    const message = completionMessage(completion);
    if (mode === "tui" || !ctx?.isIdle()) pi.sendMessage(message, { triggerTurn: true, deliverAs: "followUp" });
    else pi.sendMessage(message);
  }

  function onDone(watch: Watch, completion: Completion) {
    if (watches.get(watch.id) !== watch) return;
    watches.delete(watch.id);
    watchesChanged();
    const by = cancelledBy.get(watch.id);
    cancelledBy.delete(watch.id);
    if (by === "agent" || by === "shutdown") return;
    if (by === "operator") pi.sendMessage(completionMessage(completion), { deliverAs: "nextTurn" });
    else deliver(completion);
  }

  function cancel(watch: Watch, by: CancelledBy): Promise<Completion> {
    cancelledBy.set(watch.id, by);
    watch.cancel();
    return watch.done;
  }

  async function cancelAll() {
    const pending = [...watches.values()].map((watch) => cancel(watch, "shutdown"));
    settle?.(undefined);
    settle = undefined;
    await Promise.all(pending);
  }

  function start(spec: Spec): string {
    for (const watch of watches.values()) {
      const s = watch.spec;
      if (s.mode === spec.mode && s.command === spec.command && s.cwd === spec.cwd) {
        return `Already watching this as ${describe(watch)}. Its result will arrive as a \`watch\` message.`;
      }
    }
    if (watches.size >= MAX_ACTIVE) {
      throw new Error(`at most ${MAX_ACTIVE} watches may be active; cancel one first:\n${list()}`);
    }
    const id = `w${nextId++}`;
    const settings = pi.getSettings();
    const watch = new Watch(id, spec, {
      path: settings.shellPath,
      prefix: settings.shellCommandPrefix,
      env: () => {
        const env = { ...process.env };
        // Listeners run synchronously inside emit(), so their edits land before the spawn.
        pi.events.emit(ENV_EVENT, { env });
        return env;
      },
    });
    watches.set(id, watch);
    watchesChanged();
    void watch.done.then((completion) => onDone(watch, completion));
    return (
      `Started ${describe(watch)}; full output goes to ${watch.logPath}. ` +
      "Its result will arrive as a `watch` message, so don't wait or poll for it: carry on, or end your turn."
    );
  }

  function list(): string {
    return [...watches.values()].map(describe).join("\n") || "No active watches.";
  }

  registerGuard(pi);
  registerRenderer(pi);

  pi.registerTool({
    name: "watch",
    label: "Watch",
    description:
      "Wait on a long-running command or condition in the background. `start` begins a watch (`exit` or `poll` " +
      "mode); `list` shows active watches; `cancel` stops one by id. Each completed watch is reported back as a " +
      "`watch` message with its outcome and last output.",
    promptSnippet: "Wait in the background for long-running commands or conditions (CI, merges, deploys)",
    promptGuidelines: [
      "Use `watch` instead of a blocking bash wait (`sleep`, `--watch` flags, polling loops) for anything " +
        "taking longer than ~10s: CI runs, PR merges, deploys, long test suites.",
      "`watch` `exit` mode runs a command in the background and reports its exit code, eg. " +
        "`gh run watch 123 --exit-status`. `poll` mode re-runs a probe every `interval` seconds: exit 0 " +
        "means the condition is met, 1 means not yet, anything else means the probe failed and stops the " +
        "watch. Commands run with `set -o pipefail`.",
      "After starting a `watch`, don't check on it: its result arrives as a `watch` message, which wakes you " +
        "if you are idle. Carry on with other work, or end your turn.",
    ],
    parameters: Type.Object({
      action: Type.Union([Type.Literal("start"), Type.Literal("list"), Type.Literal("cancel")]),
      mode: Type.Optional(Type.Union([Type.Literal("exit"), Type.Literal("poll")])),
      command: Type.Optional(Type.String({ minLength: 1, description: "Shell command, or probe for `poll`" })),
      label: Type.Optional(
        Type.String({ minLength: 1, maxLength: 60, description: "Short description, eg. PR #87 checks" }),
      ),
      cwd: Type.Optional(Type.String({ description: "Working directory; defaults to the session's" })),
      interval: Type.Optional(
        Type.Integer({
          minimum: 10,
          maximum: 3600,
          description: `\`poll\` only: seconds between probes (default ${DEFAULT_INTERVAL_SECONDS})`,
        }),
      ),
      timeout: Type.Optional(
        Type.Integer({
          minimum: 1,
          maximum: 24 * 60 * 60,
          description: `Seconds before the watch gives up (default ${DEFAULT_TIMEOUT_SECONDS})`,
        }),
      ),
      id: Type.Optional(Type.String({ description: "`cancel` only: the watch id, eg. w1" })),
    }),
    async execute(_toolCallId, params, _signal, _onUpdate, toolCtx) {
      if (params.action === "list") return text(list());
      if (params.action === "cancel") {
        const watch = params.id ? watches.get(params.id) : undefined;
        if (!watch) throw new Error(`no active watch ${params.id ?? "(missing id)"}:\n${list()}`);
        const completion = await cancel(watch, "agent");
        return text(`Cancelled ${watch.id} "${watch.spec.label}" after ${duration(completion.durationMs)}.`);
      }
      if (!params.mode || !params.command || !params.label) throw new Error("start needs mode, command and label");
      if (params.mode === "exit" && params.interval !== undefined) throw new Error("interval only applies to poll");
      return text(
        start({
          mode: params.mode,
          command: params.command,
          label: params.label,
          cwd: directory(toolCtx.cwd, params.cwd),
          intervalSeconds: params.interval ?? DEFAULT_INTERVAL_SECONDS,
          timeoutSeconds: params.timeout ?? DEFAULT_TIMEOUT_SECONDS,
        }),
      );
    },
  });

  pi.registerCommand("watch", {
    description: "List active watches and cancel one",
    handler: async (_args, commandCtx) => {
      if (watches.size === 0) {
        commandCtx.ui.notify("No active watches.", "info");
        return;
      }
      const options = [...watches.values()].map(describe);
      const choice = await commandCtx.ui.select("Active watches", options);
      const watch = [...watches.values()][options.indexOf(choice ?? "")];
      if (!watch) return;
      if (await commandCtx.ui.confirm(`Cancel ${watch.id}?`, watch.spec.label)) await cancel(watch, "operator");
    },
  });

  pi.on("session_start", async (_event, startCtx) => {
    await cancelAll();
    nextId = 1;
    mode = startCtx.mode;
    ctx = startCtx;
    updateStatus();
  });

  pi.on("session_shutdown", async () => {
    await cancelAll();
    ctx = undefined;
  });

  // Held at the run's last turn_end rather than agent_before_settle: only while the run is active
  // does ctx.signal exist, and so let an abort end the hold.
  pi.on("turn_end", async (event, turnCtx) => {
    if (mode === "tui" || watches.size === 0) return undefined;
    if (event.toolResults.length > 0 || event.context.pendingMessages.length > 0) return undefined;
    if (event.outcome === "error") return undefined;
    if (event.outcome === "aborted" || turnCtx.signal?.aborted) {
      await cancelAll();
      return undefined;
    }
    const completion = await new Promise<Completion | undefined>((resolveSettle) => {
      settle = resolveSettle;
      turnCtx.signal?.addEventListener("abort", () => void cancelAll(), { once: true });
    });
    if (!completion) return undefined;
    return { entries: [{ type: "custom_message" as const, ...completionMessage(completion) }], continue: true };
  });
}
