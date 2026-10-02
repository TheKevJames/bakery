/**
 * A claw's memory files, under its state dir (BAKERY_MEMORY_DIR):
 * `MEMORY.md` (curated) and `memory/YYYY-MM-DD.md` (daily notes), plus the
 * shared `USER.md` (BAKERY_SHARED_DIR). Every path is resolved here so tools
 * cannot be pointed elsewhere.
 */
import * as fs from "node:fs";
import * as path from "node:path";

export const SOURCES = ["owner", "self", "external"] as const;
export type Source = (typeof SOURCES)[number];

export interface Edit {
  oldText: string;
  newText: string;
}

function required(name: string): string {
  const value = process.env[name];
  if (!value) throw new Error(`${name} is not set`);
  return value;
}

export function memoryDir(): string {
  return required("BAKERY_MEMORY_DIR");
}

export function userFile(): string {
  return path.join(required("BAKERY_SHARED_DIR"), "USER.md");
}

export function curatedFile(): string {
  return path.join(memoryDir(), "MEMORY.md");
}

function pad(n: number): string {
  return String(n).padStart(2, "0");
}

export function today(now = new Date()): string {
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
}

/** Append a timestamped, source-tagged entry to today's daily note. */
export function append(text: string, source: Source, now = new Date()): string {
  const file = path.join(memoryDir(), "memory", `${today(now)}.md`);
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const header = fs.existsSync(file) ? "" : `# ${today(now)}\n\n`;
  const time = `${pad(now.getHours())}:${pad(now.getMinutes())}`;
  fs.appendFileSync(file, `${header}## ${time} · ${source}\n\n${text.trim()}\n\n`);
  return path.relative(memoryDir(), file);
}

/** A memory file named relative to the memory dir: MEMORY.md or memory/*.md. */
export function resolve(name: string): string {
  const normal = path.posix.normalize(name);
  if (normal !== "MEMORY.md" && !/^memory\/[^/]+\.md$/.test(normal)) {
    throw new Error(`not a memory file: ${name} (use MEMORY.md or memory/<file>.md)`);
  }
  return path.join(memoryDir(), normal);
}

export function readOrEmpty(file: string): string {
  return fs.existsSync(file) ? fs.readFileSync(file, "utf-8") : "";
}

/** Apply exact, unique replacements; throws without writing on any mismatch. */
export function applyEdits(text: string, edits: Edit[]): string {
  let result = text;
  for (const { oldText, newText } of edits) {
    const at = result.indexOf(oldText);
    if (!oldText || at < 0) throw new Error(`text not found: ${JSON.stringify(oldText.slice(0, 80))}`);
    if (result.indexOf(oldText, at + 1) >= 0) {
      throw new Error(`text is not unique: ${JSON.stringify(oldText.slice(0, 80))}`);
    }
    result = result.slice(0, at) + newText + result.slice(at + oldText.length);
  }
  return result;
}

export function writeCapped(file: string, text: string, maxChars: number): void {
  if (text.length > maxChars) {
    throw new Error(`${path.basename(file)} would be ${text.length} characters; the limit is ${maxChars}`);
  }
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, text.endsWith("\n") ? text : `${text}\n`);
}

export function describeEdits(edits: Edit[]): string {
  return edits
    .map(({ oldText, newText }) =>
      [...oldText.split("\n").map((l) => `- ${l}`), ...newText.split("\n").map((l) => `+ ${l}`)].join("\n"),
    )
    .join("\n\n");
}
