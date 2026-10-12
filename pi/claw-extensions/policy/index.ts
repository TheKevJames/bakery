/**
 * Policy Extension (claws only)
 *
 * The claw's whole pi runs in an OS sandbox (pi/claw-sandbox/launch.mts),
 * which enforces its paths and network. This enforces the rest of its policy
 * (BAKERY_POLICY, set by the gateway):
 *
 * - only allowlisted tools are declared to the model, and any other call is
 *   blocked;
 * - calls matching a `confirm` pattern need my approval (a pi dialog, which
 *   the gateway turns into Discord buttons);
 * - results showing a sandbox refusal get a note saying so, so neither the
 *   model nor a later reader mistakes it for eg. missing auth or files.
 *
 * Bash is not handed the claw's secrets. That is hygiene, not a boundary:
 * bash shares pi's sandbox, so it could read them from pi's environment.
 *
 * Without a valid policy every tool call is blocked.
 */
import {
  type BashOperations,
  createBashToolDefinition,
  createLocalBashOperations,
  type ExtensionAPI,
} from "@earendil-works/pi-coding-agent";
import { type Policy, parsePolicy, toolAllowed } from "./rules.ts";

type Verdict = { block: true; reason: string } | undefined;

// What sandbox refusals look like: seatbelt's EPERM, and the network proxy's
// 403 (its body, or curl's report of it).
const SANDBOX_REFUSAL =
  /Operation not permitted|\bEPERM\b|Connection blocked by network allowlist|blocked-by-allowlist|CONNECT tunnel failed, response 403/;
const SANDBOX_NOTE =
  "If the output above shows an operation refused (Operation not permitted, EPERM, or a proxy 403), " +
  "this claw's sandbox policy blocked it: it restricts which paths and network hosts the claw may use. " +
  "This is not a missing credential, permission, or file; use a tool instead, or report the gap.";

function block(reason: string): Verdict {
  return { block: true, reason };
}

function withoutSecrets(policy: Policy): BashOperations {
  const local = createLocalBashOperations();
  return {
    async exec(command, cwd, options) {
      const env = { ...(options.env ?? process.env) };
      for (const name of policy.secretNames) delete env[name];
      return local.exec(command, cwd, { ...options, env });
    },
  };
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
    pi.registerTool(createBashToolDefinition(process.cwd(), { operations: withoutSecrets(policy) }));
  }

  pi.on("tool_result", async (event) => {
    const refused = event.content.some((part) => part.type === "text" && SANDBOX_REFUSAL.test(part.text));
    if (!refused) return undefined;
    return { content: [...event.content, { type: "text" as const, text: SANDBOX_NOTE }] };
  });

  pi.on("session_start", async () => {
    if (!policy) throw new Error(error);
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
    const command = event.toolName === "bash" ? String(input.command ?? "") : "";
    if (policy.confirm.some((re) => re.test(event.toolName) || (command && re.test(command)))) {
      const summary = command || JSON.stringify(input).slice(0, 500);
      const approved = await ctx.ui.confirm(`Allow ${event.toolName}?`, summary);
      if (!approved) return block("denied by the user");
    }
    return undefined;
  });
}
