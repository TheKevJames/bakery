/** Test-only: fails on every turn, like a broken extension would. */
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

export default function (pi: ExtensionAPI) {
  pi.on("turn_end", async () => {
    throw new Error("faulty extension");
  });
}
