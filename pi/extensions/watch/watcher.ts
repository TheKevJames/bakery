import { getShellConfig } from "@earendil-works/pi-coding-agent";
import { type ChildProcess, spawn } from "node:child_process";
import { createWriteStream, mkdtempSync, type WriteStream } from "node:fs";
import { constants, tmpdir } from "node:os";
import { join } from "node:path";
import { setTimeout as delay } from "node:timers/promises";

export type Mode = "exit" | "poll";
export type Outcome = "exited" | "matched" | "error" | "timed-out" | "cancelled";

export interface Spec {
  mode: Mode;
  command: string;
  label: string;
  cwd: string;
  intervalSeconds: number;
  timeoutSeconds: number;
}

export interface Shell {
  path?: string;
  prefix?: string;
  env: () => NodeJS.ProcessEnv;
}

export interface Completion {
  id: string;
  label: string;
  mode: Mode;
  command: string;
  outcome: Outcome;
  exitCode?: number;
  durationMs: number;
  tail: string;
  logPath: string;
}

const KILL_GRACE_MS = 5_000;
// After the shell exits, detached descendants can hold its pipes open forever;
// stop waiting for trailing output after this long.
const STDIO_DRAIN_MS = 250;
const TAIL_LINES = 50;
const TAIL_BYTES = 4_096;

let logDir: string | undefined;

function logPath(id: string): string {
  logDir ??= mkdtempSync(join(tmpdir(), "pi-watch-"));
  return join(logDir, `${id}-${Date.now()}.log`);
}

class Tail {
  private text = "";

  push(chunk: string): void {
    this.text = (this.text + chunk).slice(-TAIL_BYTES * 2);
  }

  reset(): void {
    this.text = "";
  }

  toString(): string {
    const lines = this.text.replace(/\n$/, "").split("\n").slice(-TAIL_LINES).join("\n");
    return Buffer.byteLength(lines) <= TAIL_BYTES ? lines : Buffer.from(lines).subarray(-TAIL_BYTES).toString();
  }
}

function signalGroup(child: ChildProcess, signal: NodeJS.Signals): void {
  if (!child.pid) return;
  try {
    process.kill(-child.pid, signal);
  } catch {
    child.kill(signal);
  }
}

/** Runs `script` in its own process group; on `stop`, SIGTERMs the group, then SIGKILLs it. */
function run(script: string, cwd: string, shell: Shell, onData: (chunk: string) => void, stop: AbortSignal) {
  const config = getShellConfig(shell.path);
  const fromStdin = config.commandTransport === "stdin";
  const child = spawn(config.shell, fromStdin ? config.args : [...config.args, script], {
    cwd,
    detached: true,
    env: shell.env(),
    stdio: [fromStdin ? "pipe" : "ignore", "pipe", "pipe"],
  });
  if (fromStdin) child.stdin?.end(script);
  child.stdout?.setEncoding("utf8").on("data", onData);
  child.stderr?.setEncoding("utf8").on("data", onData);

  return new Promise<number>((resolve, reject) => {
    let killTimer: NodeJS.Timeout | undefined;
    const terminate = () => {
      signalGroup(child, "SIGTERM");
      killTimer = setTimeout(() => signalGroup(child, "SIGKILL"), KILL_GRACE_MS);
    };
    if (stop.aborted) terminate();
    else stop.addEventListener("abort", terminate, { once: true });

    child.once("error", (err) => {
      stop.removeEventListener("abort", terminate);
      reject(err);
    });
    child.once("exit", (code, signal) => {
      clearTimeout(killTimer);
      stop.removeEventListener("abort", terminate);
      const exitCode = code ?? 128 + (signal ? constants.signals[signal] : 0);
      const drained = setTimeout(() => {
        child.stdout?.destroy();
        child.stderr?.destroy();
        resolve(exitCode);
      }, STDIO_DRAIN_MS);
      child.once("close", () => {
        clearTimeout(drained);
        resolve(exitCode);
      });
    });
  });
}

export class Watch {
  readonly startedAt = Date.now();
  readonly logPath: string;
  readonly done: Promise<Completion>;
  private readonly cancelled = new AbortController();

  constructor(
    readonly id: string,
    readonly spec: Spec,
    shell: Shell,
  ) {
    this.logPath = logPath(id);
    this.done = this.watch(shell);
  }

  cancel(): void {
    this.cancelled.abort();
  }

  private async watch(shell: Shell): Promise<Completion> {
    const timedOut = AbortSignal.timeout(this.spec.timeoutSeconds * 1000);
    const stop = AbortSignal.any([this.cancelled.signal, timedOut]);
    const log = createWriteStream(this.logPath);
    const tail = new Tail();
    const onData = (chunk: string) => {
      log.write(chunk);
      tail.push(chunk);
    };
    const script = [shell.prefix, "set -o pipefail", this.spec.command].filter(Boolean).join("\n");

    let outcome: Outcome;
    let exitCode: number | undefined;
    try {
      ({ outcome, exitCode } = await this.loop(script, shell, onData, tail, log, stop));
    } catch (err) {
      outcome = "error";
      tail.push(`\n${err instanceof Error ? err.message : String(err)}\n`);
    }
    if (stop.aborted) {
      outcome = this.cancelled.signal.aborted ? "cancelled" : "timed-out";
      exitCode = undefined;
    }
    await new Promise((resolve) => log.end(resolve));

    const { id, spec, logPath } = this;
    return {
      id,
      label: spec.label,
      mode: spec.mode,
      command: spec.command,
      outcome,
      exitCode,
      durationMs: Date.now() - this.startedAt,
      tail: tail.toString(),
      logPath,
    };
  }

  private async loop(
    script: string,
    shell: Shell,
    onData: (chunk: string) => void,
    tail: Tail,
    log: WriteStream,
    stop: AbortSignal,
  ): Promise<{ outcome: Outcome; exitCode?: number }> {
    const { mode, cwd, intervalSeconds } = this.spec;
    if (mode === "exit") {
      return { outcome: "exited", exitCode: await run(script, cwd, shell, onData, stop) };
    }
    for (let probe = 1; ; probe++) {
      tail.reset();
      log.write(`--- probe ${probe} at ${new Date().toISOString()}\n`);
      const exitCode = await run(script, cwd, shell, onData, stop);
      if (stop.aborted) return { outcome: "cancelled" };
      if (exitCode === 0) return { outcome: "matched", exitCode };
      if (exitCode !== 1) return { outcome: "error", exitCode };
      try {
        await delay(intervalSeconds * 1000, undefined, { signal: stop });
      } catch {
        return { outcome: "cancelled" };
      }
    }
  }
}
