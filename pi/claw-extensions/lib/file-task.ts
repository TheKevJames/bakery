/**
 * Filing a task through `bakery task add` (bakery/bakery/task_list/filing.py),
 * which adds it unless an open task or a rejection already has its link's
 * lineage. Every claw tool that adds a task files through this.
 */
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

export type Task = { namespace: string; id: number; summary: string };
export type Rejection = { link: string; summary: string; reason: string; date: string };
export type Filing = { filed: Task } | { open: Task } | { rejected: Rejection };

export function ref(task: Task): string {
  return `${task.namespace}:${task.id}`;
}

/** `args` as `task add` takes them; any `-- <summary>` must come last. */
export async function fileTask(pi: ExtensionAPI, args: string[], signal?: AbortSignal): Promise<Filing> {
  const result = await pi.exec("bakery", ["task", "add", "--json", ...args], { signal });
  if (result.code !== 0) throw new Error(result.stderr.trim() || `bakery task add exited with ${result.code}`);
  return JSON.parse(result.stdout) as Filing;
}

/** Why a filing added nothing, for the model; it is not an error to retry. */
export function notFiled(filing: Exclude<Filing, { filed: Task }>): string {
  if ("open" in filing) {
    return `Not added: open task #${ref(filing.open)} already has this link's lineage ("${filing.open.summary}").`;
  }
  const { date, reason } = filing.rejected;
  return `Not added: the operator rejected this link's lineage on ${date}, so it is never filed again: ${reason}`;
}
