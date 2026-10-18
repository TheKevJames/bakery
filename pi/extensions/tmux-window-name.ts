/**
 * tmux-window-name
 *
 * Mirrors the pi session's status and name onto the tmux window it lives in,
 * eg. `● b-build-12` while working, `○ b-build-12` once idle, and
 * `? b-build-12` while a dialog is waiting on the user.
 *
 * The name is rendered from the session name: known prefixes are abbreviated
 * (`bakery-` becomes `b-`), the rest is normalized to lowercase hyphen-case,
 * and anything longer than MAX_NAME_LENGTH is cut and ends in `...`.
 *
 * `rename-window` implicitly disables tmux's `automatic-rename` for that
 * window, so when the session has no name, or ends, pi re-enables it to
 * restore tmux's default behavior.
 *
 * pi only renames windows it owns: a window is owned when tmux's
 * `automatic-rename` is still on (tmux is auto-naming it, so no manual name is
 * present) or when the window's current name matches the last name pi applied.
 * If the user has manually renamed the window, pi backs off and leaves it alone.
 *
 * Scope: TUI sessions inside a tmux pane (TMUX_PANE is set). Assumes one pi
 * session per tmux window; several would overwrite each other's status.
 */
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

import { STATUS_CHANNEL, type Status, type StatusEvent } from "./session-registry/api.ts";

const MAX_NAME_LENGTH = 20;
const ELLIPSIS = "...";
// Matched against the raw session name, case-sensitively.
const ABBREVIATIONS: ReadonlyArray<readonly [string, string]> = [["bakery-", "b-"]];
const GLYPHS: Record<Status, string> = { idle: "○", working: "●", blocked: "?" };

// Captured at session_start; the pane that owns this pi process. tmux keeps
// $TMUX_PANE stable for the process lifetime, so reading it once is correct. A
// pane target resolves to its window for window-scoped tmux commands.
let tmuxPane: string | undefined;

// The last window name pi applied. Used to detect manual renames:
// `rename-window` turns tmux's `automatic-rename` off, so that option alone
// can't tell a pi-set name from a user-set one once pi has renamed once.
let lastSetName: string | undefined;

let status: Status = "idle";

// Status and name changes arrive concurrently; unserialized, two renames can
// land out of order, leaving a window name that no longer matches lastSetName
// and so looks like a manual rename pi must back off from.
let pending: Promise<void> = Promise.resolve();
function serialized(update: () => Promise<void>): Promise<void> {
  pending = pending.then(update, update);
  return pending;
}

export default function (pi: ExtensionAPI) {
  pi.events.on(STATUS_CHANNEL, async (data) => {
    status = (data as StatusEvent).status;
    await serialized(() => setWindowName(pi));
  });

  pi.on("session_start", async (_event, ctx) => {
    if (ctx.mode !== "tui") return;
    await serialized(() => adoptWindow(pi));
  });

  pi.on("session_info_changed", async () => {
    await serialized(() => setWindowName(pi));
  });

  pi.on("session_shutdown", async () => {
    await serialized(async () => {
      await restoreAutomaticRename(pi);
      tmuxPane = undefined;
    });
  });
}

async function adoptWindow(pi: ExtensionAPI): Promise<void> {
  tmuxPane = process.env.TMUX_PANE;
  // A name left on the window by a prior run (eg. one that crashed before
  // handing the window back) is adopted as pi-owned, whatever status it
  // showed, so later manual renames are still detected instead of being
  // treated as a clobberable auto name.
  const restored = pi.getSessionName();
  if (restored) {
    const state = await getWindowState(pi);
    if (state && !state.automaticRename && withoutGlyph(state.name) === formatName(restored)) {
      lastSetName = state.name;
    }
  }
  await setWindowName(pi);
}

async function setWindowName(pi: ExtensionAPI): Promise<void> {
  if (!tmuxPane) return;
  const name = pi.getSessionName();
  if (!name) {
    await restoreAutomaticRename(pi);
    return;
  }
  const windowName = `${GLYPHS[status]} ${formatName(name)}`;
  if (windowName === lastSetName) return;
  // Respect a name the user set manually: only touch windows pi owns.
  if (!(await weOwnWindow(pi))) return;
  try {
    await pi.exec("tmux", ["rename-window", "-t", tmuxPane, windowName]);
    lastSetName = windowName;
  } catch {
    // Window naming is best-effort; never disrupt the session on failure.
  }
}

// Hand control back to tmux's own window naming.
async function restoreAutomaticRename(pi: ExtensionAPI): Promise<void> {
  if (!tmuxPane || lastSetName === undefined) return;
  if (!(await weOwnWindow(pi))) return;
  try {
    await pi.exec("tmux", ["set-window-option", "-t", tmuxPane, "automatic-rename", "on"]);
    lastSetName = undefined;
  } catch {
    // Window naming is best-effort; never disrupt the session on failure.
  }
}

// pi owns the window when tmux is still auto-naming it, or when its current name
// is the one pi last applied. Any other name means the user renamed it by hand.
async function weOwnWindow(pi: ExtensionAPI): Promise<boolean> {
  const state = await getWindowState(pi);
  if (!state) return true; // Can't read state; fall back to prior best-effort behavior.
  if (state.automaticRename) return true;
  return state.name === lastSetName;
}

async function getWindowState(
  pi: ExtensionAPI,
): Promise<{ automaticRename: boolean; name: string } | undefined> {
  if (!tmuxPane) return undefined;
  try {
    // `#{automatic-rename}` renders as "1"/"0"; split on the first tab so window
    // names containing tabs (unusual, but possible) don't corrupt the flag.
    const result = await pi.exec("tmux", [
      "display-message",
      "-p",
      "-t",
      tmuxPane,
      "#{automatic-rename}\t#{window_name}",
    ]);
    if (result.code !== 0) return undefined;
    const [flag, ...rest] = result.stdout.replace(/\n$/, "").split("\t");
    return { automaticRename: flag === "1", name: rest.join("\t") };
  } catch {
    return undefined;
  }
}

function withoutGlyph(windowName: string): string {
  for (const glyph of Object.values(GLYPHS)) {
    if (windowName.startsWith(`${glyph} `)) return windowName.slice(glyph.length + 1);
  }
  return windowName;
}

function formatName(name: string): string {
  const abbreviation = ABBREVIATIONS.find(([prefix]) => name.startsWith(prefix));
  const formatted = abbreviation
    ? `${abbreviation[1]}${toHyphenCase(name.slice(abbreviation[0].length))}`
    : toHyphenCase(name);
  if (formatted.length <= MAX_NAME_LENGTH) return formatted;
  return `${formatted.slice(0, MAX_NAME_LENGTH - ELLIPSIS.length).replace(/-+$/, "")}${ELLIPSIS}`;
}

function toHyphenCase(name: string): string {
  return name
    .replace(/([a-z0-9])([A-Z])/g, "$1-$2") // split camelCase boundaries
    .replace(/[^a-zA-Z0-9]+/g, "-") // non-alphanumeric runs become one hyphen
    .replace(/^-+|-+$/g, "")
    .toLowerCase();
}
