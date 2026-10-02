/** Test-only: fails while pi starts, like a broken context.toml would. */
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

export default function (pi: ExtensionAPI) {
  pi.on("session_start", async () => {
    throw new Error("broken at startup");
  });
}
