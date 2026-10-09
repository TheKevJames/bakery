/**
 * Policy Extension (claws only)
 *
 * Enforces the claw's policy (BAKERY_POLICY, set by the gateway):
 *
 * - only allowlisted tools are declared to the model, and any other call is
 *   blocked;
 * - pi's in-process file tools are checked against the read and write path
 *   rules;
 * - bash runs inside Anthropic's sandbox-runtime (sandbox-exec on macOS),
 *   which enforces the same paths plus a network domain allowlist at the OS
 *   level, and never sees the claw's secrets; what it blocks is appended to
 *   the bash result (and its `details.sandboxViolations`), so neither the
 *   model nor a later reader mistakes it for eg. missing auth;
 * - calls matching a `confirm` pattern need my approval (a pi dialog, which
 *   the gateway turns into Discord buttons).
 *
 * Without a valid policy every tool call is blocked.
 */
import { SandboxManager } from "@anthropic-ai/sandbox-runtime";
import {
  type BashOperations,
  createBashToolDefinition,
  createLocalBashOperations,
  type ExtensionAPI,
} from "@earendil-works/pi-coding-agent";
import * as os from "node:os";
import {
  canonical,
  coversDenied,
  isDenied,
  isWritable,
  type Policy,
  parsePolicy,
  toolAllowed,
} from "./rules.ts";

type Verdict = { block: true; reason: string } | undefined;

// macOS reports seatbelt denials through its log stream, a few tens of
// milliseconds after the command fails; a command that succeeded is not
// waited for.
const VIOLATION_WAIT_MS = 1_000;
const VIOLATION_POLL_MS = 50;
const VIOLATION_NOTE =
  "This claw's sandbox policy blocked the operations above (bash's network and paths are restricted). " +
  "This is not a missing credential, permission, or file; use a tool instead, or report the gap.";

const READ_TOOLS = new Set(["read", "ls"]);
const SEARCH_TOOLS = new Set(["grep", "find"]);
const WRITE_TOOLS = new Set(["edit", "write"]);

function block(reason: string): Verdict {
  return { block: true, reason };
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function violationsFor(commandId: string, failed: boolean): Promise<string[]> {
  const store = SandboxManager.getSandboxViolationStore();
  const deadline = Date.now() + (failed ? VIOLATION_WAIT_MS : 0);
  for (;;) {
    const lines = store.getViolationsForCommand(commandId).map((v) => v.line);
    if (lines.length > 0 || Date.now() >= deadline) return lines;
    await sleep(VIOLATION_POLL_MS);
  }
}

/** Bash operations for one tool call; its sandbox violations land in `violations`. */
function sandboxedOperations(policy: Policy, toolCallId: string, violations: Map<string, string[]>): BashOperations {
  const local = createLocalBashOperations();
  return {
    async exec(command, cwd, options) {
      const env = { ...(options.env ?? process.env) };
      for (const name of policy.secretNames) delete env[name];
      const wrapped = await SandboxManager.wrapWithSandbox(command, undefined, undefined, options.signal, {
        commandId: toolCallId,
        commandText: command,
      });
      try {
        const result = await local.exec(wrapped, cwd, { ...options, env });
        const found = await violationsFor(toolCallId, result.exitCode !== 0);
        if (found.length > 0) violations.set(toolCallId, found);
        return result;
      } finally {
        SandboxManager.cleanupAfterCommand();
      }
    },
  };
}

function checkPath(toolName: string, input: Record<string, unknown>, cwd: string, policy: Policy): Verdict {
  const raw = typeof input.path === "string" && input.path ? input.path : ".";
  const target = canonical(raw, cwd);
  if (WRITE_TOOLS.has(toolName)) {
    return isWritable(target, policy) ? undefined : block(`${raw} is not writable under this claw's policy`);
  }
  if (isDenied(target, policy)) return block(`${raw} is protected and cannot be read`);
  if (SEARCH_TOOLS.has(toolName) && coversDenied(target, policy)) {
    return block(`${raw} contains protected paths; search a narrower directory, or use bash`);
  }
  return undefined;
}

export default function policyExtension(pi: ExtensionAPI) {
  let policy: Policy | undefined;
  let error = "no policy loaded";

  try {
    policy = parsePolicy(process.env.BAKERY_POLICY);
  } catch (e) {
    error = e instanceof Error ? e.message : String(e);
  }

  const violations = new Map<string, string[]>();

  if (policy) {
    const allowed = policy;
    pi.registerTool({
      ...createBashToolDefinition(process.cwd()),
      label: "bash (sandboxed)",
      async execute(toolCallId, params, signal, onUpdate, ctx) {
        const operations = sandboxedOperations(allowed, toolCallId, violations);
        const bash = createBashToolDefinition(process.cwd(), { operations });
        return bash.execute(toolCallId, params, signal, onUpdate, ctx);
      },
    });
  }

  pi.on("tool_result", async (event) => {
    if (event.toolName !== "bash") return undefined;
    const found = violations.get(event.toolCallId);
    if (!found) return undefined;
    violations.delete(event.toolCallId);
    const note = `<sandbox_violations>\n${found.join("\n")}\n</sandbox_violations>\n${VIOLATION_NOTE}`;
    return {
      content: [...event.content, { type: "text" as const, text: note }],
      details: { ...event.details, sandboxViolations: found },
    };
  });

  pi.on("session_start", async () => {
    if (!policy) throw new Error(error);
    await SandboxManager.initialize(
      {
        network: { allowedDomains: policy.network, deniedDomains: [] },
        filesystem: {
          denyRead: policy.denyRead,
          allowRead: policy.allowRead,
          allowWrite: [...policy.writePaths, canonical(os.tmpdir())],
          denyWrite: [],
        },
      },
      undefined,
      /* enableLogMonitor */ true,
    );
  });

  pi.on("session_shutdown", async () => {
    await SandboxManager.reset();
  });

  pi.on("before_agent_start", async () => {
    if (!policy) throw new Error(error);
    const allowed = policy;
    pi.setActiveTools(
      pi
        .getAllTools()
        .map((tool) => tool.name)
        .filter((name) => toolAllowed(name, allowed)),
    );
  });

  pi.on("tool_call", async (event, ctx) => {
    if (!policy) return block(error);
    if (!toolAllowed(event.toolName, policy)) {
      return block(`${event.toolName} is not allowed by this claw's policy`);
    }
    const input = event.input as Record<string, unknown>;
    if (READ_TOOLS.has(event.toolName) || SEARCH_TOOLS.has(event.toolName) || WRITE_TOOLS.has(event.toolName)) {
      const verdict = checkPath(event.toolName, input, ctx.cwd, policy);
      if (verdict) return verdict;
    }
    const command = event.toolName === "bash" ? String(input.command ?? "") : "";
    if (policy.confirm.some((re) => re.test(event.toolName) || (command && re.test(command)))) {
      const summary = command || JSON.stringify(input).slice(0, 500);
      const approved = await ctx.ui.confirm(`Allow ${event.toolName}?`, summary);
      if (!approved) return block("denied by the user");
    }
    return undefined;
  });
}
