import type { ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";

import { STATUS_CHANNEL, type Status, type StatusEvent } from "./api.ts";

/**
 * Tracks whether the session is idle, working, or blocked on a dialog, and
 * publishes each change on {@link STATUS_CHANNEL}.
 */
export class StatusTracker {
  private running = false;
  private compacting = false;
  // Depth, not a flag: dialogs can nest, and the outer one still blocks.
  private promptDepth = 0;
  private published: Status | undefined;
  private active = false;

  constructor(private readonly pi: ExtensionAPI) {
    pi.on("agent_start", () => this.update(() => { this.running = true; }));
    pi.on("agent_settled", () => this.update(() => { this.running = false; }));
    pi.on("session_before_compact", () => this.update(() => { this.compacting = true; }));
    pi.on("session_compact", () => this.update(() => { this.compacting = false; }));
    pi.on("session_compact_failed", () => this.update(() => { this.compacting = false; }));
    pi.on("ui_prompt_start", () => this.update(() => { this.promptDepth += 1; }));
    pi.on("ui_prompt_end", () => this.update(() => { this.promptDepth = Math.max(0, this.promptDepth - 1); }));
  }

  start(): void {
    this.active = true;
    this.published = undefined;
    this.publish();
  }

  stop(): void {
    this.active = false;
  }

  /**
   * The status right now. Unlike what is published, this also asks pi, which
   * covers busy spells no event brackets (eg. a run that is still starting).
   * Publishing can't: inside the handlers that end compaction, pi still
   * reports itself busy, and nothing would fire to publish idle afterwards.
   */
  current(ctx: ExtensionContext): Status {
    const status = this.fromEvents();
    if (status === "idle" && !ctx.isIdle()) return "working";
    return status;
  }

  private fromEvents(): Status {
    if (this.promptDepth > 0) return "blocked";
    if (this.running || this.compacting) return "working";
    return "idle";
  }

  private update(change: () => void): void {
    change();
    this.publish();
  }

  private publish(): void {
    if (!this.active) return;
    const status = this.fromEvents();
    if (status === this.published) return;
    this.published = status;
    const event: StatusEvent = { status };
    this.pi.events.emit(STATUS_CHANNEL, event);
  }
}
