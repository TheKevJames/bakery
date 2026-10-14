/**
 * Makes `read` wait for in-flight `edit`/`write` calls on the same file.
 *
 * Tool calls from one assistant message run in parallel. `edit` and `write`
 * serialize on `withFileMutationQueue`, but write with a truncating
 * `writeFile`; pi's own `read` takes no part in the queue, so a `read`
 * batched with an `edit` of the same file can see it empty ("Offset N is
 * beyond end of file (1 lines total)", or silently empty content).
 * Upstream: https://github.com/earendil-works/pi/issues/8318 — delete this
 * extension once pi's `read` takes the queue itself.
 *
 * Batches start their calls' `execute()` in message order, and `edit` joins
 * the queue synchronously on entry. Joining it synchronously here too keeps
 * the model's order: a `read` listed before an `edit` sees the old content,
 * one listed after sees the new. So the queue must be joined before any
 * `await`, and the key must be computed exactly as `edit` computes it
 * (pi's unexported `resolveToCwd`), not with `read`'s async path lookup.
 */
import { homedir } from "node:os";
import { isAbsolute, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import {
  createReadToolDefinition,
  type ExtensionAPI,
  withFileMutationQueue,
} from "@earendil-works/pi-coding-agent";

// Mirrors pi's dist/utils/paths.js, which `edit` uses to key the queue.
const UNICODE_SPACES = /[\u00A0\u2000-\u200A\u202F\u205F\u3000]/g;

function resolveToCwd(filePath: string, cwd: string): string {
  let normalized = filePath.replace(UNICODE_SPACES, " ");
  if (normalized.startsWith("@")) normalized = normalized.slice(1);
  if (normalized === "~") normalized = homedir();
  else if (normalized.startsWith("~/")) normalized = join(homedir(), normalized.slice(2));
  else if (normalized.startsWith("file://")) normalized = fileURLToPath(normalized);
  return isAbsolute(normalized) ? resolve(normalized) : resolve(cwd, normalized);
}

export default function (pi: ExtensionAPI) {
  const read = createReadToolDefinition(process.cwd());

  pi.registerTool({
    ...read,
    execute(toolCallId, params, signal, onUpdate, ctx) {
      const absolutePath = resolveToCwd(params.path, ctx?.cwd || process.cwd());
      return withFileMutationQueue(absolutePath, async () => {
        if (signal?.aborted) throw new Error("Operation aborted");
        return read.execute(toolCallId, params, signal, onUpdate, ctx);
      });
    },
  });
}
