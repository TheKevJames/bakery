/**
 * Task Extension (claws only)
 *
 * Tools that change the shared `task` list (`task` CLI, $TASK_FOLDER), which
 * the operator uses too; claws read it with `task list` and `task show` in
 * bash. Claws read every namespace but the sandbox lets them write only their
 * own (BAKERY_TASK_NAMESPACE), so these tools address tasks there by id alone.
 * Claims are always made as the claw itself (BAKERY_CLAW), and `task_add`
 * files into the claw's `[tasks] tag` (BAKERY_TASK_TAG), through its link's
 * lineage (lib/file-task.ts); `task` serializes concurrent writers. Only
 * `ask_operator` (ask-operator.ts) moves a task to `human`, so every task
 * moved there says what the operator is asked.
 *
 * Within the namespace, the remaining checks are guard rails on the easy path,
 * not a boundary: bash shares pi's sandbox, so those rules hold because the
 * claws follow them.
 */
import { defineTool, type ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";
import { fileTask, notFiled } from "./lib/file-task.ts";
import { checkClaimed, claw, Id, namespace, NS, ref, run, task } from "./lib/task-list.ts";

function taskTag(): string {
  const tag = process.env.BAKERY_TASK_TAG;
  if (!tag) throw new Error("BAKERY_TASK_TAG is not set");
  return tag;
}

function text(message: string, details: Record<string, unknown> = {}) {
  return { content: [{ type: "text" as const, text: message }], details };
}

// Blocked tasks must say what they wait on, in a form triage's collectors can
// check (bakery/bakery/task_list/blocked.py).
const BLOCKED = "blocked";
const HUMAN = "human";

const Priority = Type.Union([Type.Literal("low"), Type.Literal("medium"), Type.Literal("high")]);
const Size = Type.Union([Type.Literal("small"), Type.Literal("medium"), Type.Literal("large")]);

export default function tasks(pi: ExtensionAPI) {
  pi.registerTool(
    defineTool({
      name: "task_add",
      label: "Add Task",
      description:
        `Add a task to ${NS}${process.env.BAKERY_TASK_TAG ? `, tagged \`${process.env.BAKERY_TASK_TAG}\`` : ""}. ` +
        "`link` is the exact source URL, so copy it verbatim: its lineage (the link, ignoring line anchors and " +
        "case) is how a source is recognized, so nothing is added if an open task, in any namespace, or a " +
        "rejection by the operator already has it.",
      parameters: Type.Object({
        summary: Type.String({ maxLength: 120, description: "One line, at most ~80 characters" }),
        description: Type.String({ description: "Source, link, and enough context to triage it cold" }),
        link: Type.String({ description: "The source URL, verbatim" }),
        priority: Type.Optional(Priority),
        size: Type.Optional(Size),
      }),
      async execute(_id, params, signal) {
        const args = ["-n", namespace(), "--tag", taskTag(), "--description", params.description];
        args.push("--link", params.link);
        if (params.priority) args.push("--priority", params.priority);
        if (params.size) args.push("--size", params.size);
        args.push("--", params.summary);
        const filing = await fileTask(pi, args, signal);
        if ("filed" in filing) return text(`Added: ${JSON.stringify(filing.filed)}`);
        return text(notFiled(filing), filing);
      },
    }),
  );

  pi.registerTool(
    defineTool({
      name: "task_set",
      label: "Update Task",
      description:
        `Update a task in ${NS}: move it to a section (\`tag\`, eg. \`build\`), set its priority or size, ` +
        "claim it for yourself (fails if someone else owns it), release your claim, or append notes " +
        `to its description. Moving it to \`${BLOCKED}\` fails unless its notes, with any appended now, ` +
        "hold a valid `**Blocked on:**` list (the last one counts) of blockers which have not cleared. " +
        `Never moves it to \`${HUMAN}\`: use \`ask_operator\` for that.`,
      parameters: Type.Object({
        id: Id,
        tag: Type.Optional(Type.String({ description: "Section, eg. build" })),
        claim: Type.Optional(Type.Boolean()),
        release: Type.Optional(Type.Boolean()),
        priority: Type.Optional(Priority),
        size: Type.Optional(Size),
        description_append: Type.Optional(Type.String()),
      }),
      async execute(_id, params, signal) {
        if (params.claim && params.release) throw new Error("claim and release are exclusive");
        if (params.tag === HUMAN) throw new Error(`use ask_operator to move a task to ${HUMAN}`);
        const id = ref(params.id);
        if (params.release) await checkClaimed(pi, params.id, signal);
        if (params.tag === BLOCKED) {
          const check = ["blocked", "check", String(params.id)];
          if (params.description_append) check.push("--append", params.description_append);
          await run(pi, "bakery", check, signal);
        }
        const args = ["set", id];
        if (params.tag) args.push("--tag", params.tag);
        if (params.claim) args.push("--owner", claw());
        if (params.priority) args.push("--priority", params.priority);
        if (params.size) args.push("--size", params.size);
        if (params.description_append) args.push("--description-append", params.description_append);
        let result: string | undefined;
        if (args.length > 2) result = await task(pi, [...args, "--json"], signal);
        if (params.release) result = await task(pi, ["unset", id, "owner", "--json"], signal);
        return text(result ?? (await task(pi, ["show", id, "--json"], signal)));
      },
    }),
  );
}
