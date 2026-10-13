/**
 * What pi's process needs from a claw's policy, as passed by the gateway in
 * BAKERY_POLICY (JSON). Paths are the sandbox's (see
 * pi/claw-sandbox/launch.mts).
 */

export interface Policy {
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
  return { secretNames: strings(raw, "secret_names") };
}
