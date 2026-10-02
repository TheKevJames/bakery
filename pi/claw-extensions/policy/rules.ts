/**
 * A claw's policy, as passed by the gateway in BAKERY_POLICY (JSON), and the
 * path rules the in-process tools are checked against. Bash enforces the
 * same rules at the OS level through the sandbox (see index.ts).
 */
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";

export interface Policy {
  /** Tool names, or patterns where `*` matches any characters. */
  tools: string[];
  writePaths: string[];
  /** Absolute paths, or globs (eg. `~/.config/zsh/dropins/nobackup-*`). */
  denyRead: string[];
  /** Exceptions to denyRead (eg. the claw's own state dir). */
  allowRead: string[];
  /** Domains bash may reach; empty means no network. */
  network: string[];
  /** Regexes; a matching tool name or bash command needs my approval. */
  confirm: RegExp[];
  /** Environment variables (secrets) bash must never see. */
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
    writePaths: strings(raw, "write_paths").map(canonical),
    denyRead: strings(raw, "deny_read").map((p) => (isGlob(p) ? p : canonical(p))),
    allowRead: strings(raw, "allow_read").map(canonical),
    network: strings(raw, "network"),
    confirm: strings(raw, "confirm").map((p) => new RegExp(p)),
    secretNames: strings(raw, "secret_names"),
  };
}

function isGlob(pattern: string): boolean {
  return /[*?[\]{}]/.test(pattern);
}

function expandHome(p: string): string {
  return p === "~" || p.startsWith("~/") ? path.join(os.homedir(), p.slice(1)) : p;
}

/**
 * The real path of `p`, following symlinks as far as the path exists, so a
 * link cannot smuggle a denied (or non-writable) location past a prefix check.
 */
export function canonical(p: string, cwd = process.cwd()): string {
  let head = path.resolve(cwd, expandHome(p));
  const tail: string[] = [];
  for (;;) {
    try {
      return path.join(fs.realpathSync(head), ...tail);
    } catch {
      const parent = path.dirname(head);
      if (parent === head) return path.join(head, ...tail);
      tail.unshift(path.basename(head));
      head = parent;
    }
  }
}

function within(p: string, dir: string): boolean {
  return p === dir || p.startsWith(dir.endsWith("/") ? dir : `${dir}/`);
}

/** The literal directory a glob starts from, eg. `/a/b` for `/a/b/c-*`. */
function globRoot(pattern: string): string {
  const parts = pattern.split("/");
  const literal = parts.slice(0, parts.findIndex((part) => isGlob(part)));
  return canonical(literal.join("/") || "/");
}

function reallowed(p: string, policy: Policy): boolean {
  return policy.allowRead.some((dir) => within(p, dir));
}

export function isDenied(p: string, policy: Policy): boolean {
  if (reallowed(p, policy)) return false;
  return policy.denyRead.some((rule) =>
    isGlob(rule)
      ? path.matchesGlob(p, expandHome(rule)) || path.matchesGlob(p, `${expandHome(rule)}/**`)
      : within(p, rule),
  );
}

/**
 * Whether a recursive search from `dir` would descend into a denied path.
 * pi's grep and find run in-process, outside the sandbox.
 */
export function coversDenied(dir: string, policy: Policy): boolean {
  return policy.denyRead.some((rule) => {
    const root = isGlob(rule) ? globRoot(expandHome(rule)) : rule;
    if (within(dir, root)) return !reallowed(dir, policy);
    return within(root, dir) && !reallowed(root, policy);
  });
}

export function isWritable(p: string, policy: Policy): boolean {
  return policy.writePaths.some((dir) => within(p, dir)) && !isDenied(p, policy);
}

export function toolAllowed(name: string, policy: Policy): boolean {
  return policy.tools.some((pattern) =>
    new RegExp(`^${pattern.split("*").map((s) => s.replace(/[.+?^${}()|[\]\\]/g, "\\$&")).join(".*")}$`).test(name),
  );
}
