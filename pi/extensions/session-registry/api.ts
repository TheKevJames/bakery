/**
 * The session-registry contract other extensions build on. Extensions can't
 * share live module state (pi loads each one with its own module cache), so
 * this file only holds types and channel names; everything live travels over
 * `pi.events`.
 */
import type { ExtensionContext } from "@earendil-works/pi-coding-agent";

/** Emitted on every session_start with a {@link ReadyEvent}. */
export const READY_CHANNEL = "session-registry:ready";
/** Emitted with a {@link StatusEvent} whenever the session's status changes. */
export const STATUS_CHANNEL = "session-registry:status";

export type Status = "idle" | "working" | "blocked";

export interface StatusEvent {
  status: Status;
}

/** One newline-delimited JSON request read from the session's socket. */
export interface Request {
  type: string;
  id?: string;
  [key: string]: unknown;
}

/** Replies to the request on the connection it arrived on. */
export type Respond = (success: boolean, data?: unknown, error?: string) => void;

export interface CommandContext {
  ctx: ExtensionContext;
  status: () => Status;
  respond: Respond;
}

export type CommandHandler = (request: Request, context: CommandContext) => void | Promise<void>;

export interface ReadyEvent {
  /** Handles socket requests of `type`; register again on every ready. */
  registerCommand(type: string, handler: CommandHandler): void;
}
