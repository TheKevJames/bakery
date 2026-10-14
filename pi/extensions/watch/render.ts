import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { Box, Text } from "@earendil-works/pi-tui";
import type { Completion } from "./watcher.ts";

export const MESSAGE_TYPE = "watch";

export function duration(ms: number): string {
  const total = Math.round(ms / 1000);
  const [h, m, s] = [Math.floor(total / 3600), Math.floor((total % 3600) / 60), total % 60];
  if (h) return `${h}h${String(m).padStart(2, "0")}m`;
  if (m) return `${m}m${String(s).padStart(2, "0")}s`;
  return `${s}s`;
}

function result(c: Completion): string {
  switch (c.outcome) {
    case "exited":
      return `exited ${c.exitCode}`;
    case "matched":
      return "matched";
    case "error":
      return c.exitCode === undefined ? "failed to run" : `probe failed with exit ${c.exitCode}`;
    case "timed-out":
      return "timed out";
    case "cancelled":
      return "was cancelled by the operator";
  }
}

export function summary(c: Completion): string {
  return `watch ${c.id} "${c.label}" (${c.mode}) ${result(c)} after ${duration(c.durationMs)}`;
}

export function completionMessage(c: Completion) {
  const content = [
    `${summary(c)}.`,
    `Command: ${c.command}`,
    `Full log: ${c.logPath}`,
    c.tail ? `Last output:\n\`\`\`\n${c.tail}\n\`\`\`` : "No output.",
  ].join("\n");
  return { customType: MESSAGE_TYPE, content, display: true, details: c };
}

export function registerRenderer(pi: ExtensionAPI): void {
  pi.registerMessageRenderer<Completion>(MESSAGE_TYPE, (message, { expanded, outputPad }, theme) => {
    const c = message.details;
    if (!c) return undefined;
    const ok = c.outcome === "matched" || (c.outcome === "exited" && c.exitCode === 0);
    const color = c.outcome === "cancelled" ? "dim" : ok ? "success" : "error";
    let text = theme.fg(color, `⏱ ${summary(c)}`);
    if (expanded) {
      text += `\n${theme.fg("dim", `$ ${c.command}\nlog: ${c.logPath}`)}`;
      if (c.tail) text += `\n${c.tail}`;
    }
    const box = new Box(outputPad, 1, (t) => theme.bg("customMessageBg", t));
    box.addChild(new Text(text, 0, 0));
    return box;
  });
}
