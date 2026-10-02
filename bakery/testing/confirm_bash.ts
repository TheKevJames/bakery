/** Test-only: every bash call needs a confirm dialog answered by the client. */
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

export default function (pi: ExtensionAPI) {
  pi.on("tool_call", async (event, ctx) => {
    if (event.toolName !== "bash") return undefined;
    const allowed = await ctx.ui.confirm("Run bash?", String(event.input.command));
    return allowed ? undefined : { block: true, reason: "denied by user" };
  });
}
