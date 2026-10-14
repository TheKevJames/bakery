/**
 * Policy Extension (claws only)
 *
 * The claw's whole pi runs in an OS sandbox (pi/claw-sandbox/launch.mts),
 * which enforces its policy: its paths (and refuses unix sockets but its own,
 * and names other than localhost resolving to this machine). Which tools a
 * claw uses is not policy: it gets pi's default tools and every extension
 * tool its profile loads, since bash shares that one sandbox and so could do
 * whatever a withheld tool would. Results showing a sandbox refusal get a note saying so, so
 * neither the model nor a later reader mistakes it for eg. missing auth or
 * files.
 *
 * Bash, and the commands of pi/extensions/watch, are not handed the claw's
 * secrets, except those the gateway leaves out of BAKERY_POLICY's
 * secret_names (its `bash_secrets`). That is hygiene, not a boundary: they
 * share pi's sandbox, so they could read them from pi's environment.
 *
 * Without a valid policy (BAKERY_POLICY, set by the gateway) pi fails to
 * start, and every tool call is blocked.
 */
import {
  type BashOperations,
  createBashToolDefinition,
  createLocalBashOperations,
  type ExtensionAPI,
} from "@earendil-works/pi-coding-agent";
import { type Policy, parsePolicy } from "./rules.ts";

// What sandbox refusals look like: seatbelt's EPERM, and the network proxy's
// 403 (its body, or curl's, Node's fetch's, or Go's report of it).
const SANDBOX_REFUSAL =
  /Operation not permitted|\bEPERM\b|Connection blocked by network allowlist|blocked-by-allowlist|Connection to \S+ blocked: resolved to|CONNECT tunnel failed, response 403|Proxy response \(403\)|Get "[^"]*": Forbidden/;
const SANDBOX_NOTE =
  "If the output above shows an operation refused (Operation not permitted, EPERM, or a proxy 403), " +
  "this claw's sandbox policy blocked it: it restricts which paths the claw may use, and refuses unix sockets and names other than localhost resolving to this machine. " +
  "This is not a missing credential, permission, or file; use a tool instead, or report the gap.";

function stripSecrets(policy: Policy, env: NodeJS.ProcessEnv): void {
  for (const name of policy.secretNames) delete env[name];
}

function withoutSecrets(policy: Policy): BashOperations {
  const local = createLocalBashOperations();
  return {
    async exec(command, cwd, options) {
      const env = { ...(options.env ?? process.env) };
      stripSecrets(policy, env);
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
    const loaded = policy;
    pi.events.on("watch:env", (data) => stripSecrets(loaded, (data as { env: NodeJS.ProcessEnv }).env));
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
  });

  pi.on("tool_call", async () => {
    if (!policy) return { block: true, reason: error };
    return undefined;
  });
}
