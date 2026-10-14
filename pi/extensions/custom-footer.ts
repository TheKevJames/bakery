/**
 * Custom Footer Extension — Enhanced status bar
 *
 * Displays:
 *   Left: model, in/out tokens, cost, context%, cwd, git branch
 *   Right: dynamically registered extension statuses (ctx.ui.setStatus)
 * Color-coded context usage: green <50%, yellow 50-75%, red >75%
 */

import type { AssistantMessage } from "@earendil-works/pi-ai";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { truncateToWidth, visibleWidth } from "@earendil-works/pi-tui";

// Status keys pinned to the right edge, in this order; any other status sorts alphabetically to their left.
const RIGHTMOST_STATUSES = ["watch", "session-control"];

function statusOrder([a]: [string, string], [b]: [string, string]): number {
  return RIGHTMOST_STATUSES.indexOf(a) - RIGHTMOST_STATUSES.indexOf(b) || a.localeCompare(b);
}

export default function (pi: ExtensionAPI) {
  function fmt(n: number): string {
    if (n < 1000) return `${n}`;
    return `${(n / 1000).toFixed(1)}k`;
  }

  pi.on("session_start", async (_event, ctx) => {
    ctx.ui.setFooter((tui, theme, footerData) => {
      const unsub = footerData.onBranchChange(() => tui.requestRender());

      return {
        dispose: unsub,
        invalidate() {},
        render(width: number): string[] {
          let input = 0, output = 0, cost = 0;
          for (const e of ctx.sessionManager.getBranch()) {
            if (e.type === "message" && e.message.role === "assistant") {
              const m = e.message as AssistantMessage;
              input += m.usage.input;
              output += m.usage.output;
              cost += m.usage.cost.total;
            }
          }

          const modelStr = theme.fg("accent", ctx.model?.id || "no-model");

          const pct = ctx.getContextUsage()?.percent ?? 0;

          const pctColor = pct > 75 ? "error" : pct > 50 ? "warning" : "success";

          const tokenStats = [
            theme.fg("accent", `${fmt(input)}/${fmt(output)}`),
            theme.fg("warning", `$${cost.toFixed(2)}`),
            theme.fg(pctColor, `${pct.toFixed(0)}%`),
          ].join(" ");

          const parts = process.cwd().split("/");
          const short = parts.length > 2 ? parts.slice(-2).join("/") : process.cwd();
          const cwdStr = theme.fg("muted", `⌂ ${short}`);

          const branch = footerData.getGitBranch();
          const branchStr = branch ? theme.fg("accent", `⎇ ${branch}`) : "";

          const sep = theme.fg("dim", " | ");
          const leftParts = [modelStr, tokenStats, cwdStr];
          if (branchStr) leftParts.push(branchStr);
          const left = truncateToWidth(leftParts.join(sep), width);

          const statuses = [...footerData.getExtensionStatuses().entries()]
            .sort(statusOrder)
            .map(([, text]) => text.replace(/[\r\n\t]/g, " ").replace(/ +/g, " ").trim())
            .join(" ");
          const rightWidth = width - visibleWidth(left) - 1;
          if (!statuses || rightWidth <= 0) return [left];
          const right = truncateToWidth(statuses, rightWidth, theme.fg("dim", "..."));
          return [left + " ".repeat(width - visibleWidth(left) - visibleWidth(right)) + right];
        },
      };
    });
  });
}
