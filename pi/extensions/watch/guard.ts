import { type ExtensionAPI, isToolCallEventType } from "@earendil-works/pi-coding-agent";

const MAX_SLEEP_SECONDS = 10;
const SLEEP = /\bsleep\s+(\d+(?:\.\d+)?)([smhd]?)\b/g;
const UNIT_SECONDS: Record<string, number> = { "": 1, s: 1, m: 60, h: 3600, d: 86400 };
const BLOCKING_WATCH = /\bgh\s+pr\s+checks\b[^\n;&|]*\s--watch\b|\bgh\s+run\s+watch\b/;

function longestSleep(command: string): number {
  let longest = 0;
  for (const [, amount, unit] of command.matchAll(SLEEP)) {
    longest = Math.max(longest, Number(amount) * UNIT_SECONDS[unit]);
  }
  return longest;
}

function reason(command: string): string | undefined {
  const sleep = longestSleep(command);
  if (sleep > MAX_SLEEP_SECONDS) return `\`sleep ${sleep}\` blocks for more than ${MAX_SLEEP_SECONDS}s`;
  if (BLOCKING_WATCH.test(command)) return "`gh … --watch` / `gh run watch` blocks until CI finishes";
  return undefined;
}

export function registerGuard(pi: ExtensionAPI): void {
  pi.on("tool_call", (event) => {
    if (!isToolCallEventType("bash", event)) return undefined;
    const why = reason(event.input.command);
    if (!why) return undefined;
    return {
      block: true,
      reason:
        `${why}. Use the \`watch\` tool instead: \`exit\` mode runs the command in the background, ` +
        "`poll` mode re-runs a probe until it exits 0, and either wakes you when done.",
    };
  });
}
