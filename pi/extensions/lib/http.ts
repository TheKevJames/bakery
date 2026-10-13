/**
 * HTTP helpers shared by the extensions that call web APIs.
 *
 * This lives in a subdirectory without an `index.ts` so pi's extension
 * discovery skips it: every top-level file in `pi/extensions/` is loaded as
 * an extension, and one without a default factory fails pi's startup.
 */
import { DEFAULT_MAX_BYTES, DEFAULT_MAX_LINES, truncateHead } from "@earendil-works/pi-coding-agent";

const TIMEOUT_MS = 30_000;

/**
 * fetch, but a failure names its root cause: fetch's own message is only
 * "fetch failed", eg. for a DNS failure or the sandbox proxy's refusal.
 */
export async function get(url: string, headers: Record<string, string>, signal?: AbortSignal): Promise<Response> {
  const timeout = AbortSignal.timeout(TIMEOUT_MS);
  try {
    return await fetch(url, { headers, signal: signal ? AbortSignal.any([signal, timeout]) : timeout });
  } catch (e) {
    let cause: unknown = e;
    while (cause instanceof Error && cause.cause instanceof Error) cause = cause.cause;
    if (cause === e || !(cause instanceof Error)) throw e;
    throw new Error(`${(e as Error).message}: ${cause.message}`, { cause: e });
  }
}

export function text(body: string, details: Record<string, unknown> = {}) {
  const truncated = truncateHead(body, { maxBytes: DEFAULT_MAX_BYTES, maxLines: DEFAULT_MAX_LINES });
  const note = truncated.truncated ? "\n\n[output truncated]" : "";
  return { content: [{ type: "text" as const, text: truncated.content + note }], details };
}
