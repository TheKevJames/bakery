/**
 * Bakery Extension
 *
 * Lets the external `bakery` CLI drive pi sessions through the control socket
 * session-registry gives every session.
 *
 * Features:
 * - Receive user messages from the `bakery` CLI (steer or follow-up delivery)
 * - Retrieve the last assistant messages from a session
 * - Gracefully shut down a session (only idle by default; force aborts non-idle)
 * - Run built-in control commands (new, clone, reload)
 *
 * Requests (session-registry also answers `get_status`):
 *   - { type: "send", message: "...", mode?: "steer"|"follow_up" }
 *   - { type: "get_message" }
 *   - { type: "shutdown", force?: boolean }
 *   - { type: "command", name: "new"|"clone"|"reload" }
 *
 *   `command` runs a built-in pi control command that has no headless
 *   equivalent unless routed through a real command handler. It is dispatched
 *   as the internal `/bakery-cmd` command so it runs in an
 *   ExtensionCommandContext, then replies with the resulting session id.
 */

import type {
  ExtensionAPI,
  ExtensionCommandContext,
  ExtensionContext,
} from "@earendil-works/pi-coding-agent";

import {
  type CommandContext,
  READY_CHANNEL,
  type ReadyEvent,
  type Request,
  type Respond,
} from "./session-registry/api.ts";

// Built-in pi commands routed through `/bakery-cmd`. Each has an
// ExtensionCommandContext method but no headless dispatch of its own.
const BUILTIN_COMMANDS = new Set(["new", "clone", "reload"]);

interface State {
  // Reply owed to the `command` request, sent by the deferred `/bakery-cmd`
  // handler once the resulting session id is known.
  pendingCommand: Respond | null;
}

// ============================================================================
// Message Extraction
// ============================================================================

interface ExtractedMessage {
  role: "user" | "assistant";
  content: string;
  timestamp: number;
}

function getTextMessages(ctx: ExtensionContext): ExtractedMessage[] {
  const messages: ExtractedMessage[] = [];
  for (const entry of ctx.sessionManager.getBranch()) {
    if (entry.type !== "message") continue;
    const msg = entry.message;
    if (!("role" in msg) || (msg.role !== "user" && msg.role !== "assistant")) continue;
    const content = Array.isArray(msg.content) ? msg.content : [{ type: "text", text: msg.content }];
    const textParts = content
      .filter((c): c is { type: "text"; text: string } => c.type === "text")
      .map((c) => c.text);
    if (textParts.length > 0) {
      messages.push({ role: msg.role, content: textParts.join("\n"), timestamp: msg.timestamp });
    }
  }
  return messages;
}

// ============================================================================
// Command Handlers
// ============================================================================

// Reply to the connection that issued the `command` request. The `new`/`clone`
// replies fire from a `withSession` callback that runs after the old session
// has been torn down, so this replies through the captured connection.
function settlePendingCommand(
  state: State,
  success: boolean,
  data?: unknown,
  error?: string,
): void {
  const respond = state.pendingCommand;
  state.pendingCommand = null;
  respond?.(success, data, error);
}

async function runBuiltinCommand(
  state: State,
  ctx: ExtensionCommandContext,
  name: string,
): Promise<void> {
  const withSession = async (replaced: ExtensionCommandContext) => {
    settlePendingCommand(state, true, { sessionId: replaced.sessionManager.getSessionId() });
  };

  if (name === "reload") {
    // Reply before reload tears this runtime down; ctx.reload() is terminal.
    settlePendingCommand(state, true, { sessionId: ctx.sessionManager.getSessionId() });
    await ctx.reload();
    return;
  }
  if (name === "new") {
    const result = await ctx.newSession({ withSession });
    if (result.cancelled) settlePendingCommand(state, false, undefined, "new session cancelled");
    return;
  }
  if (name === "clone") {
    const leafId = ctx.sessionManager.getLeafId();
    // Forking a session with no message history at its header entry tears
    // down the headless process instead of resolving, so refuse it.
    const hasHistory = ctx.sessionManager.getBranch().some((e) => e.type === "message");
    if (!leafId || !hasHistory) {
      settlePendingCommand(state, false, undefined, "nothing to clone yet");
      return;
    }
    const result = await ctx.fork(leafId, { position: "at", withSession });
    if (result.cancelled) settlePendingCommand(state, false, undefined, "clone cancelled");
    return;
  }
  settlePendingCommand(state, false, undefined, `unsupported command: ${name}`);
}

function handleShutdown(request: Request, { ctx, status, respond }: CommandContext): void {
  const current = status();
  if (current !== "idle") {
    if (!request.force) {
      respond(false, undefined, `Session is ${current}, not idle (use --force)`);
      return;
    }
    // Interrupt the running turn / prompt so the deferred shutdown can fire.
    ctx.abort();
  }
  respond(true, { shuttingDown: true });
  // Defer so the response flushes before the process tears down.
  setTimeout(() => ctx.shutdown(), 0);
}

// Get the recent conversation slice plus current status.
function handleGetMessage(_request: Request, { ctx, status, respond }: CommandContext): void {
  const messages = getTextMessages(ctx);

  let lastUserIndex = -1;
  let message: ExtractedMessage | null = null;
  for (let i = messages.length - 1; i >= 0; i--) {
    if (message === null && messages[i].role === "assistant") message = messages[i];
    if (lastUserIndex === -1 && messages[i].role === "user") lastUserIndex = i;
  }

  const prompt = lastUserIndex >= 0 ? messages[lastUserIndex] : null;
  let priorAgent: ExtractedMessage | null = null;
  for (let i = lastUserIndex - 1; i >= 0; i--) {
    if (messages[i].role === "assistant") { priorAgent = messages[i]; break; }
  }
  const agentsSince = lastUserIndex >= 0
    ? messages.slice(lastUserIndex + 1).filter((m) => m.role === "assistant")
    : [];

  respond(true, { status: status(), message, prompt, priorAgent, agentsSince });
}

function handleSend(pi: ExtensionAPI, request: Request, { ctx, respond }: CommandContext): void {
  const message = request.message;
  if (typeof message !== "string" || message.trim().length === 0) {
    respond(false, undefined, "Missing message");
    return;
  }

  const mode = request.mode === "follow_up" ? "follow_up" : "steer";
  const isIdle = ctx.isIdle();

  // Deliver as a real user message so it behaves exactly as if typed.
  if (isIdle) {
    pi.sendUserMessage(message, { expandPromptTemplates: true });
  } else {
    pi.sendUserMessage(message, {
      deliverAs: mode === "follow_up" ? "followUp" : "steer",
      expandPromptTemplates: true,
    });
  }

  respond(true, { delivered: true, mode: isIdle ? "direct" : mode });
}

// Run a built-in control command via the `/bakery-cmd` handler, which gets
// the ExtensionCommandContext a socket request lacks.
function handleCommand(
  pi: ExtensionAPI,
  state: State,
  request: Request,
  { status, respond }: CommandContext,
): void {
  const name = request.name;
  if (typeof name !== "string" || !BUILTIN_COMMANDS.has(name)) {
    respond(false, undefined, `unsupported command: ${String(name)}`);
    return;
  }
  const current = status();
  if (current !== "idle") {
    respond(false, undefined, `session is ${current}, not idle`);
    return;
  }
  state.pendingCommand = respond;
  pi.sendUserMessage(`/bakery-cmd ${name}`, { expandPromptTemplates: true });
}

// ============================================================================
// Extension Export
// ============================================================================

export default function (pi: ExtensionAPI) {
  const state: State = { pendingCommand: null };

  pi.registerCommand("bakery-cmd", {
    description: "internal: bakery control-plane command dispatch",
    handler: async (args, ctx) => {
      await runBuiltinCommand(state, ctx, args.trim());
    },
  });

  pi.events.on(READY_CHANNEL, (data) => {
    const { registerCommand } = data as ReadyEvent;
    registerCommand("shutdown", handleShutdown);
    registerCommand("get_message", handleGetMessage);
    registerCommand("send", (request, context) => handleSend(pi, request, context));
    registerCommand("command", (request, context) => handleCommand(pi, state, request, context));
  });
}
