/**
 * session-registry
 *
 * Registers each running pi session so other processes and extensions can
 * find, name, and talk to it:
 *
 * - a control socket at `<registry>/<session-id>.sock`, answering
 *   newline-delimited JSON requests ({ type, id?, ... }) with
 *   { type: "response", command, success, data?, error?, id? };
 * - a `<registry>/<name>.alias` symlink to the socket while the session has a
 *   name;
 * - a session without a name is named after its directory, suffixed `-N` with
 *   the lowest N no other running session uses (eg. `foo`, `foo-1`, `foo-2`);
 * - its status (idle, working, or blocked on a dialog), answered to
 *   `get_status` and published on `pi.events` (see ./api.ts).
 *
 * Other extensions add request types by subscribing to READY_CHANNEL and
 * calling `registerCommand` (eg. bakery.ts).
 *
 * The registry is `$PI_CODING_AGENT_STATE_DIR/registry`, else
 * `$XDG_STATE_HOME/pi/registry`, else `~/.local/state/pi/registry`.
 *
 * Scope: TUI and RPC sessions only. Print and JSON runs, such as subagents,
 * are short-lived children of a session that is already registered.
 */
import { promises as fs } from "node:fs";
import type * as net from "node:net";
import * as path from "node:path";

import type { ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";

import { type CommandHandler, READY_CHANNEL, type ReadyEvent, type Request, type Respond } from "./api.ts";
import * as registry from "./registry.ts";
import { listen } from "./server.ts";
import { StatusTracker } from "./status.ts";

const UI_STATUS_KEY = "session-registry";

interface Registration {
  dir: string;
  server: net.Server;
  socket: string;
  ctx: ExtensionContext;
  alias: string | null;
}

export default function (pi: ExtensionAPI) {
  const status = new StatusTracker(pi);
  const handlers = new Map<string, CommandHandler>();
  let current: Registration | null = null;
  // Renames arrive as unawaited events, so alias updates are serialized.
  let aliasSync: Promise<void> = Promise.resolve();
  const queueAliasSync = (registration: Registration): Promise<void> => {
    // A failed sync is reported to its own caller; it must not stall later ones.
    aliasSync = aliasSync.catch(() => {}).then(() => syncAlias(pi, registration));
    return aliasSync;
  };

  const handle = async (request: Request, respond: Respond): Promise<void> => {
    if (!current) {
      respond(false, undefined, "Session not ready");
      return;
    }
    const ctx = current.ctx;
    if (request.type === "get_status") {
      respond(true, { status: status.current(ctx) });
      return;
    }
    const handler = handlers.get(request.type);
    if (!handler) {
      respond(false, undefined, `Unsupported command: ${request.type}`);
      return;
    }
    await handler(request, { ctx, status: () => status.current(ctx), respond });
  };

  pi.on("session_start", async (_event, ctx) => {
    if (ctx.mode !== "tui" && ctx.mode !== "rpc") return;
    await unregister();
    current = await register(ctx, handle);
    if (!pi.getSessionName()) await nameAfterDirectory(pi, current);
    await queueAliasSync(current);
    if (ctx.hasUI) ctx.ui.setStatus(UI_STATUS_KEY, ctx.ui.theme.fg("dim", ctx.sessionManager.getSessionId()));

    handlers.clear();
    const ready: ReadyEvent = { registerCommand: (type, handler) => handlers.set(type, handler) };
    pi.events.emit(READY_CHANNEL, ready);
    status.start();
  });

  pi.on("session_info_changed", async () => {
    if (current) await queueAliasSync(current);
  });

  pi.on("session_shutdown", async () => {
    if (current?.ctx.hasUI) current.ctx.ui.setStatus(UI_STATUS_KEY, undefined);
    await unregister();
  });

  async function unregister(): Promise<void> {
    if (!current) return;
    const { dir, server, socket } = current;
    current = null;
    status.stop();
    // Not awaited: close() waits for every open connection, and a client may
    // still be owed a reply sent after this session is replaced (bakery's
    // `new`/`clone`).
    server.close();
    await aliasSync.catch(() => {});
    await registry.removeAliasesFor(dir, socket);
    await registry.unlinkIfPresent(socket);
  }
}

async function register(
  ctx: ExtensionContext,
  handle: (request: Request, respond: Respond) => Promise<void>,
): Promise<Registration> {
  const dir = registry.registryDir();
  await fs.mkdir(dir, { recursive: true });
  const socket = registry.socketPath(dir, ctx.sessionManager.getSessionId());
  await registry.unlinkIfPresent(socket);
  const server = await listen(socket, handle);
  return { dir, server, socket, ctx, alias: null };
}

async function nameAfterDirectory(pi: ExtensionAPI, registration: Registration): Promise<void> {
  const base = path.basename(registration.ctx.cwd);
  if (!registry.isSafeName(base)) return;
  pi.setSessionName(await registry.freeName(registration.dir, base));
}

async function syncAlias(pi: ExtensionAPI, registration: Registration): Promise<void> {
  const name = pi.getSessionName()?.trim() ?? "";
  const alias = registry.isSafeName(name) ? name : null;
  if (alias === registration.alias) return;
  await registry.removeAliasesFor(registration.dir, registration.socket);
  if (alias) await registry.createAlias(registration.dir, alias, registration.socket);
  registration.alias = alias;
}
