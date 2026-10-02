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
 *   level, and never sees the claw's secrets;
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

const READ_TOOLS = new Set(["read", "ls"]);
const SEARCH_TOOLS = new Set(["grep", "find"]);
const WRITE_TOOLS = new Set(["edit", "write"]);

function block(reason: string): Verdict {
  return { block: true, reason };
}

function sandboxedOperations(policy: Policy): BashOperations {
  const local = createLocalBashOperations();
  return {
    async exec(command, cwd, options) {
      const env = { ...(options.env ?? process.env) };
      for (const name of policy.secretNames) delete env[name];
      const wrapped = await SandboxManager.wrapWithSandbox(command);
      try {
        return await local.exec(wrapped, cwd, { ...options, env });
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

  if (policy) {
    pi.registerTool({
      ...createBashToolDefinition(process.cwd(), { operations: sandboxedOperations(policy) }),
      label: "bash (sandboxed)",
    });
  }

  pi.on("session_start", async () => {
    if (!policy) throw new Error(error);
    await SandboxManager.initialize({
      network: { allowedDomains: policy.network, deniedDomains: [] },
      filesystem: {
        denyRead: policy.denyRead,
        allowRead: policy.allowRead,
        allowWrite: [...policy.writePaths, canonical(os.tmpdir())],
        denyWrite: [],
      },
    });
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
