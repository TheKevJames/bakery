/**
 * context.toml: the ordered list of files a profile injects into the model's
 * context, with character budgets. Validated strictly at load time so a typo
 * fails loudly instead of silently dropping instructions.
 */
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { parse } from "smol-toml";

export type Inject = "every_turn" | "session_start";

export interface FileSpec {
  /** Absolute path, or a glob when `latest` is set. */
  pattern: string;
  inject: Inject;
  maxChars: number;
  optional: boolean;
  /** Take the N lexicographically-last glob matches (eg. dated notes). */
  latest?: number;
}

export interface ContextConfig {
  totalMaxChars: number;
  files: FileSpec[];
}

export interface LoadedFile {
  path: string;
  inject: Inject;
  content: string;
}

export const CONFIG_NAME = "context.toml";
const DEFAULT_FILE_MAX_CHARS = 20_000;
const DEFAULT_TOTAL_MAX_CHARS = 60_000;
const TOP_LEVEL_KEYS = new Set(["file_max_chars", "total_max_chars", "file"]);
const FILE_KEYS = new Set(["path", "inject", "max_chars", "optional", "latest"]);
const INJECT_MODES = new Set<string>(["every_turn", "session_start"]);

class ConfigError extends Error {
  constructor(configPath: string, message: string) {
    super(`${configPath}: ${message}`);
  }
}

function positiveInt(value: unknown, where: string, configPath: string): number {
  if (typeof value !== "number" || !Number.isInteger(value) || value <= 0) {
    throw new ConfigError(configPath, `${where} must be a positive integer`);
  }
  return value;
}

/** Expand `~` and `${VAR}`; relative paths resolve against the profile dir. */
function expandPath(raw: string, profileDir: string, configPath: string): string {
  const expanded = raw
    .replace(/^~(?=\/|$)/, os.homedir())
    .replace(/\$\{([A-Za-z_][A-Za-z0-9_]*)\}/g, (_, name: string) => {
      const value = process.env[name];
      if (!value) throw new ConfigError(configPath, `\${${name}} is not set (in ${raw})`);
      return value;
    });
  return path.resolve(profileDir, expanded);
}

function parseFile(
  raw: unknown,
  index: number,
  defaults: { maxChars: number },
  profileDir: string,
  configPath: string,
): FileSpec {
  const where = `file[${index}]`;
  if (typeof raw !== "object" || raw === null || Array.isArray(raw)) {
    throw new ConfigError(configPath, `${where} must be a table`);
  }
  const entry = raw as Record<string, unknown>;
  for (const key of Object.keys(entry)) {
    if (!FILE_KEYS.has(key)) throw new ConfigError(configPath, `${where}: unknown key ${key}`);
  }
  if (typeof entry.path !== "string" || !entry.path) {
    throw new ConfigError(configPath, `${where}.path must be a non-empty string`);
  }
  const inject = entry.inject ?? "every_turn";
  if (typeof inject !== "string" || !INJECT_MODES.has(inject)) {
    throw new ConfigError(configPath, `${where}.inject must be every_turn or session_start`);
  }
  if (entry.optional !== undefined && typeof entry.optional !== "boolean") {
    throw new ConfigError(configPath, `${where}.optional must be a boolean`);
  }
  return {
    pattern: expandPath(entry.path, profileDir, configPath),
    inject: inject as Inject,
    maxChars:
      entry.max_chars === undefined
        ? defaults.maxChars
        : positiveInt(entry.max_chars, `${where}.max_chars`, configPath),
    optional: entry.optional ?? false,
    latest: entry.latest === undefined ? undefined : positiveInt(entry.latest, `${where}.latest`, configPath),
  };
}

/** Returns undefined when the profile has no context.toml. */
export function loadConfig(profileDir: string): ContextConfig | undefined {
  const configPath = path.join(profileDir, CONFIG_NAME);
  if (!fs.existsSync(configPath)) return undefined;

  const doc = parse(fs.readFileSync(configPath, "utf-8")) as Record<string, unknown>;
  for (const key of Object.keys(doc)) {
    if (!TOP_LEVEL_KEYS.has(key)) throw new ConfigError(configPath, `unknown key ${key}`);
  }
  const fileMaxChars =
    doc.file_max_chars === undefined
      ? DEFAULT_FILE_MAX_CHARS
      : positiveInt(doc.file_max_chars, "file_max_chars", configPath);
  const totalMaxChars =
    doc.total_max_chars === undefined
      ? DEFAULT_TOTAL_MAX_CHARS
      : positiveInt(doc.total_max_chars, "total_max_chars", configPath);
  const rawFiles = doc.file ?? [];
  if (!Array.isArray(rawFiles)) throw new ConfigError(configPath, "file must be an array of tables");

  return {
    totalMaxChars,
    files: rawFiles.map((raw, i) => parseFile(raw, i, { maxChars: fileMaxChars }, profileDir, configPath)),
  };
}

function matches(spec: FileSpec): string[] {
  if (spec.latest === undefined) return fs.existsSync(spec.pattern) ? [spec.pattern] : [];
  // Glob output order is unspecified; dated names sort chronologically.
  return fs.globSync(spec.pattern).sort().slice(-spec.latest);
}

function truncate(text: string, limit: number, filePath: string): string {
  if (text.length <= limit) return text;
  return (
    `${text.slice(0, limit)}\n\n[truncated: showing ${limit} of ${text.length} ` +
    `characters; read ${filePath} directly for the rest]`
  );
}

/**
 * Read every file in `specs`, applying per-file then total budgets in config
 * order. Budgets are allocated over all specs even when only one inject mode
 * is being read, so a file's share never depends on the turn.
 */
export function readFiles(config: ContextConfig, inject: Inject): LoadedFile[] {
  const loaded: LoadedFile[] = [];
  let remaining = config.totalMaxChars;
  for (const spec of config.files) {
    const found = matches(spec);
    if (found.length === 0 && !spec.optional) {
      throw new Error(`context file not found: ${spec.pattern}`);
    }
    for (const filePath of found) {
      const text = fs.readFileSync(filePath, "utf-8").trim();
      const content = truncate(text, Math.min(spec.maxChars, remaining), filePath);
      remaining = Math.max(0, remaining - Math.min(text.length, spec.maxChars));
      if (spec.inject === inject && text) loaded.push({ path: filePath, inject: spec.inject, content });
    }
  }
  return loaded;
}
