/**
 * notify-on-done
 *
 * Posts a native macOS notification when a pi session needs you: when a run
 * settles and pi is waiting for input, or when a dialog blocks on an answer.
 * The body states why, eg. `Idle`, `Aborted (1 watch)`, `Errored`, or
 * `Blocked: Confirm push? (2 watches)`; the watch suffix counts watches still
 * running, which will wake the session again on completion.
 *
 * Scope: macOS + Terminal.app inside tmux (one pi session per tmux pane).
 * The notification is delivered via `osascript display notification`, which
 * shows under the "Script Editor" identity. Set that app to "Banners" in
 * System Settings > Notifications for a transient, button-free banner.
 *
 * Suppression: a notification is skipped only when you are already looking at
 * this pi session, i.e. Terminal.app is frontmost AND this pi process's tmux
 * pane is the active pane of the active window. Any weaker focus (different
 * app, different tmux window/pane) still notifies.
 */
import type { ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";

import { WATCH_ACTIVE_CHANNEL, type WatchActiveEvent } from "./watch/api.ts";

const SOUND = "Ping";

// Captured at session_start; the pane that owns this pi process. tmux keeps
// $TMUX_PANE stable for the process lifetime, so reading it once is correct.
let tmuxPane: string | undefined;

let activeWatches = 0;
// Set by agent_end, which precedes agent_settled for the same run.
let lastRunErrored = false;

// AppleScript string literals only need backslash and double-quote escaped.
function asString(value: string): string {
  return value.replace(/\\/g, "\\\\").replace(/"/g, '\\"');
}

function withWatches(status: string): string {
  if (activeWatches === 0) return status;
  return `${status} (${activeWatches} ${activeWatches === 1 ? "watch" : "watches"})`;
}

export default function (pi: ExtensionAPI) {
  pi.events.on(WATCH_ACTIVE_CHANNEL, (data) => {
    activeWatches = (data as WatchActiveEvent).count;
  });

  pi.on("session_start", async () => {
    tmuxPane = process.env.TMUX_PANE;
    // Watches never outlive their session, so any count left from the
    // previous one is stale.
    activeWatches = 0;
  });

  pi.on("agent_end", async (event) => {
    const last = event.messages.at(-1);
    lastRunErrored = last?.role === "assistant" && last.stopReason === "error";
  });

  pi.on("agent_settled", async (event, ctx) => {
    const status = event.aborted ? "Aborted" : lastRunErrored ? "Errored" : "Idle";
    lastRunErrored = false;
    await notify(ctx, status);
  });

  pi.on("ui_prompt_start", async (event, ctx) => {
    await notify(ctx, event.title ? `Blocked: ${event.title}` : `Blocked on ${event.kind}`);
  });

  async function notify(ctx: ExtensionContext, status: string): Promise<void> {
    if (ctx.mode !== "tui" || process.platform !== "darwin") return;

    const title = pi.getSessionName() || "Pi";
    const notifyCmd = `display notification "${asString(withWatches(status))}" with title "${asString(title)}" sound name "${SOUND}"`;

    try {
      if (await isThisPaneFocused()) {
        // Pane is focused: only notify if the user has tabbed away from
        // Terminal.app. Combined into one osascript call to avoid a second
        // subprocess for the frontmost-app check.
        const script = [
          "tell application \"System Events\"",
          "if (name of first application process whose frontmost is true) is not \"Terminal\" then",
          notifyCmd,
          "end if",
          "end tell",
        ].join("\n");
        await pi.exec("osascript", ["-e", script]);
      } else {
        // Not looking at this pane (different tmux window/pane): always notify.
        await pi.exec("osascript", ["-e", notifyCmd]);
      }
    } catch {
      // Notifications are best-effort; never disrupt the session on failure.
    }
  }

  async function isThisPaneFocused(): Promise<boolean> {
    if (!tmuxPane) return false;
    const result = await pi.exec("tmux", [
      "display-message",
      "-p",
      "-t",
      tmuxPane,
      "#{pane_active}#{window_active}",
    ]);
    return result.stdout.trim() === "11";
  }
}
