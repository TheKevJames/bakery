/**
 * The `task` CLI as the claws' task tools use it: in the claws' namespace
 * (BAKERY_TASK_NAMESPACE), as the claw itself (BAKERY_CLAW).
 */
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";

export function claw(): string {
  const name = process.env.BAKERY_CLAW;
  if (!name) throw new Error("BAKERY_CLAW is not set");
  return name;
}

export function namespace(): string {
  const ns = process.env.BAKERY_TASK_NAMESPACE;
  if (!ns) throw new Error("BAKERY_TASK_NAMESPACE is not set");
  return ns;
}

export function ref(id: number): string {
  return `${namespace()}:${id}`;
}

export async function run(pi: ExtensionAPI, command: string, args: string[], signal?: AbortSignal): Promise<string> {
  const result = await pi.exec(command, args, { signal });
  if (result.code !== 0) throw new Error(result.stderr.trim() || `${command} exited with ${result.code}`);
  return result.stdout.trim();
}

export async function task(pi: ExtensionAPI, args: string[], signal?: AbortSignal): Promise<string> {
  return run(pi, "task", args, signal);
}

type Shown = { owner: string | null };

export async function show(pi: ExtensionAPI, id: number, signal?: AbortSignal): Promise<Shown> {
  return JSON.parse(await task(pi, ["show", ref(id), "--json"], signal)) as Shown;
}

/** Fails unless the claw owns task `id`, as it must to release it. */
export async function checkClaimed(pi: ExtensionAPI, id: number, signal?: AbortSignal): Promise<void> {
  if ((await show(pi, id, signal)).owner !== claw()) {
    throw new Error(`task ${ref(id)} is not claimed by ${claw()}`);
  }
}

export const NS = process.env.BAKERY_TASK_NAMESPACE ? `\`${process.env.BAKERY_TASK_NAMESPACE}\`` : "your namespace";

export const Id = Type.Integer({ minimum: 1, description: `Id of a task in ${NS}: the number in \`<namespace>:<id>\`` });
