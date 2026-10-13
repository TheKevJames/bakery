/**
 * Claw sandbox launcher
 *
 * The gateway runs each claw's pi through this script (with node, outside
 * the sandbox): `node launch.ts pi --mode rpc ...`. It runs the command inside
 * Anthropic's sandbox-runtime, so pi and everything it starts (bash, gh, rg)
 * share one OS sandbox; macOS cannot nest sandboxes, so nothing
 * inside can get a stricter one of its own.
 *
 * Configured by the gateway through the environment, none of which reaches
 * the command:
 * - BAKERY_SANDBOX: the sandbox-runtime config (JSON);
 * - BAKERY_SANDBOX_VIOLATIONS: a JSONL file every blocked operation is
 *   appended to, outside anything the sandbox can write;
 * - BAKERY_SANDBOX_TMP: the directory this run's $TMPDIR is created in, and
 *   removed from when the command exits.
 *
 * stdio is inherited, so the gateway speaks pi's RPC protocol through it.
 */
import { SandboxManager, type SandboxRuntimeConfig } from "@anthropic-ai/sandbox-runtime";
import { spawn } from "node:child_process";
import * as fs from "node:fs";
import * as path from "node:path";

const LAUNCHER_ENV = ["BAKERY_SANDBOX", "BAKERY_SANDBOX_VIOLATIONS", "BAKERY_SANDBOX_TMP"];
const FORWARDED_SIGNALS: NodeJS.Signals[] = ["SIGTERM", "SIGINT", "SIGHUP"];
// macOS reports seatbelt denials through its log stream, shortly after the
// operation fails.
const VIOLATION_GRACE_MS = 100;

function required(name: string): string {
  const value = process.env[name];
  if (!value) throw new Error(`${name} is not set`);
  return value;
}

function quote(arg: string): string {
  return `'${arg.replaceAll("'", "'\\''")}'`;
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/** Append each new violation to `file`; returns the unsubscribe function. */
function recordViolations(file: string): () => void {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const store = SandboxManager.getSandboxViolationStore();
  let recorded = store.getTotalCount();
  return store.subscribe((violations) => {
    const fresh = store.getTotalCount() - recorded;
    if (fresh <= 0) return;
    recorded = store.getTotalCount();
    const lines = violations
      .slice(-fresh)
      .map((v) => `${JSON.stringify({ time: v.timestamp.toISOString(), line: v.line })}\n`);
    fs.appendFileSync(file, lines.join(""));
  });
}

/**
 * Every host the (empty) allowlist does not match, ie. every host; the
 * allowlist cannot express that itself (sandbox-runtime rejects `*`). The
 * proxy then refuses names resolving to loopback, link-local, this Mac's own
 * addresses, or cloud metadata, except the RFC 6761 loopback names: it takes
 * approving those as asking for loopback, so they are refused here. IP
 * literals are dialed as given.
 */
async function allowHost({ host }: { host: string }): Promise<boolean> {
  const name = host.toLowerCase().replace(/\.$/, "");
  return name !== "localhost" && !name.endsWith(".localhost");
}

async function main(argv: string[]): Promise<number> {
  if (argv.length === 0) throw new Error("usage: launch.ts COMMAND [ARG...]");
  const config = JSON.parse(required("BAKERY_SANDBOX")) as SandboxRuntimeConfig;
  const violations = required("BAKERY_SANDBOX_VIOLATIONS");
  const tmpParent = required("BAKERY_SANDBOX_TMP");
  fs.mkdirSync(tmpParent, { recursive: true });
  const tmpdir = fs.mkdtempSync(path.join(tmpParent, "pi-"));
  // sandbox-runtime hands this to the command as $TMPDIR.
  process.env.CLAUDE_CODE_TMPDIR = tmpdir;
  config.filesystem.allowWrite.push(tmpdir);
  const env = { ...process.env };
  for (const name of LAUNCHER_ENV) delete env[name];

  await SandboxManager.initialize(config, allowHost, /* enableLogMonitor */ true);
  const unsubscribe = recordViolations(violations);
  try {
    // sandbox-runtime sends loopback and private addresses around its proxy
    // (NO_PROXY), straight into the sandbox's refusal; through the proxy they
    // get the same treatment as any other host.
    const command = `unset NO_PROXY no_proxy; exec ${argv.map(quote).join(" ")}`;
    const wrapped = await SandboxManager.wrapWithSandbox(command);
    const child = spawn("bash", ["-c", wrapped], { stdio: "inherit", env });
    for (const signal of FORWARDED_SIGNALS) {
      process.on(signal, () => child.kill(signal));
    }
    const code = await new Promise<number>((resolve, reject) => {
      child.on("error", reject);
      child.on("exit", (status, signal) => resolve(status ?? (signal ? 128 : 1)));
    });
    await sleep(VIOLATION_GRACE_MS);
    return code;
  } finally {
    unsubscribe();
    await SandboxManager.reset();
    fs.rmSync(tmpdir, { recursive: true, force: true });
  }
}

main(process.argv.slice(2)).then(
  (code) => process.exit(code),
  (error: unknown) => {
    console.error(`claw sandbox: ${error instanceof Error ? error.message : String(error)}`);
    process.exit(1);
  },
);
