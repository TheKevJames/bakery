/**
 * Rate Limits Extension
 *
 * Holds model requests against per-model provider rate limits, eg. 15
 * requests/minute or 500 requests/day. Limits live in `settings.json` next to
 * this file, keyed by `<provider>/<model-id>`; a model with no entry is
 * unlimited, and a malformed config fails this extension at load.
 *
 * Every pi process on this machine (the session and any subagents it spawns)
 * shares one append-only event log in the state dir, so the limits hold across
 * parallel processes and restarts. A request is logged when the gate lets it
 * through; a response's tokens are logged when it completes.
 */
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { fileURLToPath } from "node:url";
import type { ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";

/** Waits longer than this fail the request instead of holding it. */
const MAX_WAIT_MS = 10 * 60_000;
// Longer than the longest possible calendar day (25h on a fall-back DST
// transition), so calendar windows always see a full day of events.
const BASE_RETENTION_MS = 26 * 3_600_000;
/** Rewrite the event log once it grows past this. */
const PRUNE_AT_BYTES = 256 * 1024;
/** Random extra sleep after a wait, so concurrent processes don't race one freed slot. */
const WAKE_JITTER_MS = 2_000;
const STATUS_KEY = "rate-limits";

type Window =
  | { kind: "rolling"; durationMs: number }
  | { kind: "calendar"; timezone: string };

interface Limit {
  metric: "requests" | "tokens";
  max: number;
  window: Window;
}

interface Event {
  ts: number;
  model: string;
  kind: "request" | "response";
  tokens?: number;
}

const ROLLING_WINDOW = /^rolling:(\d+)([smhd])$/;
const CALENDAR_WINDOW = /^calendar:(\S+)$/;
const UNIT_MS: Record<string, number> = { s: 1_000, m: 60_000, h: 3_600_000, d: 86_400_000 };

function parseWindow(spec: unknown, where: string): Window {
  if (typeof spec !== "string") throw new Error(`${where}: window must be a string`);
  const rolling = ROLLING_WINDOW.exec(spec);
  if (rolling) return { kind: "rolling", durationMs: Number(rolling[1]) * UNIT_MS[rolling[2]] };
  const calendar = CALENDAR_WINDOW.exec(spec);
  if (calendar) {
    try {
      new Intl.DateTimeFormat("en-US", { timeZone: calendar[1] });
    } catch {
      throw new Error(`${where}: unknown timezone "${calendar[1]}"`);
    }
    return { kind: "calendar", timezone: calendar[1] };
  }
  throw new Error(`${where}: window must be "rolling:<n>s|m|h|d" or "calendar:<timezone>", got "${spec}"`);
}

function loadLimits(settingsPath: string): Map<string, Limit[]> {
  let parsed: unknown;
  try {
    parsed = JSON.parse(fs.readFileSync(settingsPath, "utf8"));
  } catch (err) {
    throw new Error(`cannot read ${settingsPath}: ${err instanceof Error ? err.message : String(err)}`);
  }
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
    throw new Error(`settings must be an object mapping "<provider>/<model-id>" to a list of limits`);
  }
  const problems: string[] = [];
  const limits = new Map<string, Limit[]>();
  for (const [model, entries] of Object.entries(parsed as Record<string, unknown>)) {
    if (!Array.isArray(entries) || entries.length === 0) {
      problems.push(`${model}: limits must be a non-empty array`);
      continue;
    }
    const modelLimits: Limit[] = [];
    for (const [i, raw] of entries.entries()) {
      const where = `${model}[${i}]`;
      if (typeof raw !== "object" || raw === null) {
        problems.push(`${where}: limit must be an object`);
        continue;
      }
      const entry = raw as Record<string, unknown>;
      if (entry.metric !== "requests" && entry.metric !== "tokens") {
        problems.push(`${where}: metric must be "requests" or "tokens"`);
      }
      if (!Number.isInteger(entry.max) || (entry.max as number) <= 0) {
        problems.push(`${where}: max must be a positive integer`);
      }
      let window: Window;
      try {
        window = parseWindow(entry.window, where);
      } catch (err) {
        problems.push(err instanceof Error ? err.message : String(err));
        continue;
      }
      modelLimits.push({
        metric: entry.metric as "requests" | "tokens",
        max: entry.max as number,
        window,
      });
    }
    limits.set(model, modelLimits);
  }
  if (problems.length > 0) throw new Error(`invalid settings in ${settingsPath}: ${problems.join("; ")}`);
  return limits;
}

function statePath(): string {
  const dir = process.env.PI_CODING_AGENT_STATE_DIR ?? path.join(os.homedir(), ".local", "state", "pi");
  return path.join(dir, "rate-limits.jsonl");
}

function isValidEvent(value: unknown): value is Event {
  if (typeof value !== "object" || value === null) return false;
  const e = value as Record<string, unknown>;
  if (typeof e.ts !== "number" || typeof e.model !== "string") return false;
  if (e.kind === "request") return true;
  if (e.kind === "response") return typeof e.tokens === "number" && e.tokens >= 0;
  return false;
}

function readEvents(file: string, now: number, retentionMs: number): Event[] {
  let text: string;
  try {
    text = fs.readFileSync(file, "utf8");
  } catch {
    return [];
  }
  const cutoff = now - retentionMs;
  const events: Event[] = [];
  for (const line of text.split("\n")) {
    if (!line.trim()) continue;
    let parsed: unknown;
    try {
      parsed = JSON.parse(line);
    } catch {
      continue; // torn line from an interrupted write
    }
    if (isValidEvent(parsed) && parsed.ts >= cutoff) events.push(parsed);
  }
  return events;
}

function appendEvent(file: string, event: Event, now: number, retentionMs: number): void {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  try {
    if (fs.statSync(file).size > PRUNE_AT_BYTES) {
      // Rewriting can drop events another process appended between the read
      // and the rename; bounded to a few milliseconds, and only ever
      // under-throttles.
      const kept = readEvents(file, now, retentionMs).map((e) => JSON.stringify(e));
      const tmp = `${file}.tmp-${process.pid}`;
      fs.writeFileSync(tmp, kept.length > 0 ? kept.join("\n") + "\n" : "");
      fs.renameSync(tmp, file);
    }
  } catch {
    // Pruning is best-effort.
  }
  fs.appendFileSync(file, JSON.stringify(event) + "\n");
}

function dayKeyInTz(timezone: string, ts: number): string {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: timezone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date(ts));
}

// The instant at which the local date in `timezone` rolls over to the date
// containing `ts`. Binary search because "midnight ± 24h" arithmetic is off by
// an hour on DST-transition days.
function startOfDayInTz(timezone: string, ts: number): number {
  const target = dayKeyInTz(timezone, ts);
  let lo = ts - 30 * 3_600_000;
  let hi = ts;
  while (hi - lo > 1_000) {
    // No bitwise ops: timestamps don't fit in 32 bits.
    const mid = lo + Math.floor((hi - lo) / 2);
    if (dayKeyInTz(timezone, mid) < target) lo = mid;
    else hi = mid;
  }
  return hi;
}

function nextDayStartInTz(timezone: string, ts: number): number {
  const target = dayKeyInTz(timezone, ts);
  let lo = ts;
  let hi = ts + 26 * 3_600_000; // a local day is at most 25h, so this is later
  while (hi - lo > 1_000) {
    const mid = lo + Math.floor((hi - lo) / 2);
    if (dayKeyInTz(timezone, mid) <= target) lo = mid;
    else hi = mid;
  }
  return hi;
}

/**
 * The earliest instant at which the limit has room for one more request (the
 * gate assumes the outgoing request adds 0 tokens), or undefined when there is
 * room now.
 */
function waitUntilForLimit(limit: Limit, events: Event[], now: number): number | undefined {
  if (limit.window.kind === "rolling") return rollingWaitUntil(limit, limit.window.durationMs, events, now);
  return calendarWaitUntil(limit, limit.window.timezone, events, now);
}

function rollingWaitUntil(limit: Limit, windowMs: number, events: Event[], now: number): number | undefined {
  const kind = limit.metric === "requests" ? "request" : "response";
  const inWindow = events
    .filter((e) => e.kind === kind && e.ts > now - windowMs && e.ts <= now)
    .sort((a, b) => a.ts - b.ts);
  if (limit.metric === "requests") {
    // The outgoing request counts as one.
    if (inWindow.length < limit.max) return undefined;
    // Oldest events leave the window first; wait for the one that drops the
    // count to max - 1.
    return inWindow[inWindow.length - limit.max].ts + windowMs;
  }
  let total = inWindow.reduce((sum, e) => sum + (e.tokens ?? 0), 0);
  if (total <= limit.max) return undefined;
  for (const e of inWindow) {
    total -= e.tokens ?? 0;
    if (total <= limit.max) return e.ts + windowMs;
  }
  return undefined;
}

function calendarWaitUntil(limit: Limit, timezone: string, events: Event[], now: number): number | undefined {
  const dayStart = startOfDayInTz(timezone, now);
  const kind = limit.metric === "requests" ? "request" : "response";
  const today = events.filter((e) => e.kind === kind && e.ts >= dayStart && e.ts <= now);
  if (limit.metric === "requests") {
    // The outgoing request counts as one.
    return today.length >= limit.max ? nextDayStartInTz(timezone, now) : undefined;
  }
  const total = today.reduce((sum, e) => sum + (e.tokens ?? 0), 0);
  return total > limit.max ? nextDayStartInTz(timezone, now) : undefined;
}

function describeLimit(limit: Limit): string {
  const what = limit.metric === "requests" ? "requests" : "tokens";
  const window =
    limit.window.kind === "rolling"
      ? `${Math.round(limit.window.durationMs / 1000)}s window`
      : `day in ${limit.window.timezone}`;
  return `${limit.max} ${what} per ${window}`;
}

function sleepUntil(target: number, signal: AbortSignal | undefined): Promise<void> {
  const ms = Math.max(0, target - Date.now());
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(new Error("aborted"));
      return;
    }
    const timer = setTimeout(resolve, ms);
    signal?.addEventListener(
      "abort",
      () => {
        clearTimeout(timer);
        reject(new Error("aborted"));
      },
      { once: true },
    );
  });
}

/** Waits until every limit for the model has room, then logs the request. */
async function gate(
  modelKey: string,
  limits: Limit[],
  stateFile: string,
  retentionMs: number,
  ctx: ExtensionContext,
): Promise<void> {
  let now = Date.now();
  for (;;) {
    const events = readEvents(stateFile, now, retentionMs).filter((e) => e.model === modelKey);
    let worst: { waitUntil: number; limit: Limit } | undefined;
    for (const limit of limits) {
      const waitUntil = waitUntilForLimit(limit, events, now);
      if (waitUntil !== undefined && (worst === undefined || waitUntil > worst.waitUntil)) {
        worst = { waitUntil, limit };
      }
    }
    if (worst === undefined) break;
    const waitMs = worst.waitUntil - now;
    if (waitMs > MAX_WAIT_MS) {
      const message = `rate limit: ${modelKey}: ${describeLimit(worst.limit)} reached; retry after ${new Date(
        worst.waitUntil,
      ).toLocaleString()}`;
      ctx.ui.notify(message, "error");
      // Throwing alone only logs the error and lets the request through; the
      // abort is what actually stops it.
      ctx.abort();
      throw new Error(message);
    }
    // Re-check after the sleep: another process may have taken the slot.
    const target = now + waitMs + Math.floor(Math.random() * (waitMs > WAKE_JITTER_MS ? WAKE_JITTER_MS : waitMs));
    let tick: NodeJS.Timeout | undefined;
    if (ctx.mode === "tui") {
      const update = () =>
        ctx.ui.setStatus(
          STATUS_KEY,
          `rate limit: ${modelKey} — waiting ${Math.max(0, Math.ceil((target - Date.now()) / 1000))}s`,
        );
      update();
      tick = setInterval(update, 1_000);
    }
    try {
      await sleepUntil(target, ctx.signal);
    } catch {
      return; // the user aborted; the request dies with the abort
    } finally {
      if (tick) clearInterval(tick);
      ctx.ui.setStatus(STATUS_KEY, undefined);
    }
    now = Date.now();
  }
  if (limits.some((limit) => limit.metric === "requests")) {
    const ts = Date.now();
    appendEvent(stateFile, { ts, model: modelKey, kind: "request" }, ts, retentionMs);
  }
}

export default function rateLimits(pi: ExtensionAPI) {
  const limits = loadLimits(fileURLToPath(new URL("./settings.json", import.meta.url)));
  if (limits.size === 0) return;
  const stateFile = statePath();
  const retentionMs = Math.max(
    BASE_RETENTION_MS,
    ...[...limits.values()].flat().map((limit) => (limit.window.kind === "rolling" ? limit.window.durationMs : 0)),
  );

  pi.on("before_provider_request", async (_event, ctx) => {
    const model = ctx.model;
    if (!model) return;
    const modelKey = `${model.provider}/${model.id}`;
    const limitsForModel = limits.get(modelKey);
    if (limitsForModel === undefined) return;
    await gate(modelKey, limitsForModel, stateFile, retentionMs, ctx);
  });

  pi.on("message_end", (event) => {
    if (event.message.role !== "assistant") return;
    const modelKey = `${event.message.provider}/${event.message.model}`;
    const limitsForModel = limits.get(modelKey);
    if (limitsForModel === undefined || !limitsForModel.some((limit) => limit.metric === "tokens")) return;
    const usage = event.message.usage;
    const tokens = usage.input + usage.output + usage.cacheRead + usage.cacheWrite;
    if (tokens <= 0) return;
    const now = Date.now();
    appendEvent(stateFile, { ts: now, model: modelKey, kind: "response", tokens }, now, retentionMs);
  });
}
