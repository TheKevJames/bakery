/**
 * The registry directory: one `<session-id>.sock` per running session, plus a
 * `<name>.alias` symlink to it for each named one. The `bakery` CLI reads the
 * same layout (bakery/bakery/cli.py).
 */
import { promises as fs } from "node:fs";
import * as net from "node:net";
import * as os from "node:os";
import * as path from "node:path";

const SOCKET_SUFFIX = ".sock";
const ALIAS_SUFFIX = ".alias";

export function registryDir(): string {
  const stateDir = process.env.PI_CODING_AGENT_STATE_DIR?.trim()
    || path.join(process.env.XDG_STATE_HOME?.trim() || path.join(os.homedir(), ".local", "state"), "pi");
  return path.join(stateDir, "registry");
}

export function socketPath(dir: string, sessionId: string): string {
  return path.join(dir, `${sessionId}${SOCKET_SUFFIX}`);
}

export function isSafeName(name: string): boolean {
  return name.length > 0 && !name.includes("/") && !name.includes("\\") && !name.includes("..");
}

function aliasPath(dir: string, name: string): string {
  return path.join(dir, `${name}${ALIAS_SUFFIX}`);
}

function isErrnoException(error: unknown): error is NodeJS.ErrnoException {
  return typeof error === "object" && error !== null && "code" in error;
}

export async function unlinkIfPresent(target: string): Promise<void> {
  try {
    await fs.unlink(target);
  } catch (error) {
    if (!isErrnoException(error) || error.code !== "ENOENT") throw error;
  }
}

// TODO: add GC for stale sockets/aliases older than 7 days.
export async function removeAliasesFor(dir: string, socket: string): Promise<void> {
  let entries;
  try {
    entries = await fs.readdir(dir, { withFileTypes: true });
  } catch (error) {
    if (isErrnoException(error) && error.code === "ENOENT") return;
    throw error;
  }
  for (const entry of entries) {
    if (!entry.isSymbolicLink()) continue;
    const alias = path.join(dir, entry.name);
    let target: string;
    try {
      target = await fs.readlink(alias);
    } catch {
      continue;
    }
    if (path.resolve(dir, target) === socket) await unlinkIfPresent(alias);
  }
}

/** Points `name`'s alias at `socket`, taking it over from any other session. */
export async function createAlias(dir: string, name: string, socket: string): Promise<void> {
  const alias = aliasPath(dir, name);
  await unlinkIfPresent(alias);
  try {
    await fs.symlink(path.basename(socket), alias);
  } catch (error) {
    if (!isErrnoException(error) || error.code !== "EEXIST") throw error;
  }
}

// Sockets and aliases outlive a crashed pi, so a name only counts as taken
// while something still accepts connections behind it.
function isLive(socket: string): Promise<boolean> {
  return new Promise((resolve) => {
    const connection = net.connect(socket);
    connection.once("connect", () => {
      connection.destroy();
      resolve(true);
    });
    connection.once("error", () => resolve(false));
  });
}

/** `base`, or `base-N` with the lowest N no running session is named. */
export async function freeName(dir: string, base: string): Promise<string> {
  for (let index = 0; ; index++) {
    const name = index === 0 ? base : `${base}-${index}`;
    if (!(await isLive(aliasPath(dir, name)))) return name;
  }
}
