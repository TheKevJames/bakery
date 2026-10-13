/**
 * A claw's policy, as passed by the gateway in BAKERY_POLICY (JSON): the
 * parts the OS sandbox cannot enforce. Paths are the sandbox's (see
 * pi/claw-sandbox/launch.mts).
 */

export interface Policy {
  /** Tool names, or patterns where `*` matches any characters. */
  tools: string[];
  /** Regexes; a matching tool name or bash command needs the operator's approval. */
  confirm: RegExp[];
  /** Environment variables (secrets) bash is not handed. */
  secretNames: string[];
}

function strings(raw: Record<string, unknown>, key: string): string[] {
  const value = raw[key];
  if (!Array.isArray(value) || !value.every((x) => typeof x === "string")) {
    throw new Error(`BAKERY_POLICY.${key} must be a list of strings`);
  }
  return value;
}

export function parsePolicy(json: string | undefined): Policy {
  if (!json) throw new Error("BAKERY_POLICY is not set; refusing to run without a policy");
  const raw = JSON.parse(json) as Record<string, unknown>;
  return {
    tools: strings(raw, "tools"),
    confirm: strings(raw, "confirm").map((p) => new RegExp(p)),
    secretNames: strings(raw, "secret_names"),
  };
}

export function toolAllowed(name: string, policy: Policy): boolean {
  return policy.tools.some((pattern) =>
    new RegExp(`^${pattern.split("*").map((s) => s.replace(/[.+?^${}()|[\]\\]/g, "\\$&")).join(".*")}$`).test(name),
  );
}
