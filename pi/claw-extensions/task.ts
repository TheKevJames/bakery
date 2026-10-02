/**
 * Task Extension (claws only)
 *
 * Tools over the shared `task` list (`task` CLI, $TASK_FOLDER), which I use
 * too. A claw's `policy.tools` decides which tools it gets. Claims are always
 * made as the claw itself (BAKERY_CLAW); `task` serializes concurrent writers.
 */
import { defineTool, type ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";

function claw(): string {
  const name = process.env.BAKERY_CLAW;
  if (!name) throw new Error("BAKERY_CLAW is not set");
  return name;
}

function text(message: string, details: Record<string, unknown> = {}) {
  return { content: [{ type: "text" as const, text: message }], details };
}

async function task(pi: ExtensionAPI, args: string[], signal?: AbortSignal): Promise<string> {
  const result = await pi.exec("task", args, { signal });
  if (result.code !== 0) throw new Error(result.stderr.trim() || `task exited with ${result.code}`);
  return result.stdout.trim();
}

const Id = Type.Integer({ minimum: 1, description: "Task id" });

export default function tasks(pi: ExtensionAPI) {
  pi.registerTool(
    defineTool({
      name: "task_list",
      label: "List Tasks",
      description:
        "List tasks as JSON. Filters are comma-separated `field<op>value` on summary, tag, owner, " +
        "link, priority, or size, with = (equals), != , ~ (contains), !~; eg. " +
        "`tag=bakery/build,owner=` (unowned).",
      parameters: Type.Object({
        filter: Type.Optional(Type.String()),
      }),
      async execute(_id, params, signal) {
        const args = ["list", "--json"];
        if (params.filter) args.push("-f", params.filter);
        return text(await task(pi, args, signal));
      },
    }),
  );

  pi.registerTool(
    defineTool({
      name: "task_show",
      label: "Show Task",
      description: "Show one task as JSON, including its description.",
      parameters: Type.Object({ id: Id }),
      async execute(_id, params, signal) {
        return text(await task(pi, ["show", String(params.id), "--json"], signal));
      },
    }),
  );

  pi.registerTool(
    defineTool({
      name: "task_add",
      label: "Add Task",
      description:
        "Add a task for triage. `link` is its lineage: the exact source URL, which is also how " +
        "duplicates are recognized, so copy it verbatim.",
      parameters: Type.Object({
        summary: Type.String({ maxLength: 120, description: "One line, at most ~80 characters" }),
        description: Type.String({ description: "Source, link, and enough context to triage it cold" }),
        link: Type.String({ description: "The source URL, verbatim" }),
      }),
      async execute(_id, params, signal) {
        const args = ["add", "--description", params.description, "--link", params.link, "--", params.summary];
        await task(pi, args, signal);
        const added = JSON.parse(await task(pi, ["list", "--json", "-f", `link=${params.link}`], signal));
        return text(`Added: ${JSON.stringify(added.at(-1) ?? {})}`);
      },
    }),
  );

  pi.registerTool(
    defineTool({
      name: "task_set",
      label: "Update Task",
      description:
        "Update a task: move it to a section (`tag`, eg. `Bakery/build`), set its priority or size, " +
        "claim it for yourself (fails if someone else owns it), release your claim, or append notes " +
        "to its description.",
      parameters: Type.Object({
        id: Id,
        tag: Type.Optional(Type.String({ description: "Section path, eg. Bakery/human" })),
        claim: Type.Optional(Type.Boolean()),
        release: Type.Optional(Type.Boolean()),
        priority: Type.Optional(Type.Union([Type.Literal("low"), Type.Literal("medium"), Type.Literal("high")])),
        size: Type.Optional(Type.Union([Type.Literal("small"), Type.Literal("medium"), Type.Literal("large")])),
        description_append: Type.Optional(Type.String()),
      }),
      async execute(_id, params, signal) {
        if (params.claim && params.release) throw new Error("claim and release are exclusive");
        const id = String(params.id);
        if (params.release) {
          const current = JSON.parse(await task(pi, ["show", id, "--json"], signal)) as { owner: string | null };
          if (current.owner !== claw()) throw new Error(`task ${id} is not claimed by ${claw()}`);
        }
        const args = ["set", id];
        if (params.tag) args.push("--tag", params.tag);
        if (params.claim) args.push("--owner", claw());
        if (params.priority) args.push("--priority", params.priority);
        if (params.size) args.push("--size", params.size);
        if (params.description_append) args.push("--description-append", params.description_append);
        if (args.length > 2) await task(pi, args, signal);
        if (params.release) await task(pi, ["unset", id, "owner"], signal);
        return text(await task(pi, ["show", id, "--json"], signal));
      },
    }),
  );

  pi.registerTool(
    defineTool({
      name: "task_link",
      label: "Link Task",
      description: "Set a task's link (eg. to the pull request implementing it).",
      parameters: Type.Object({ id: Id, url: Type.String() }),
      async execute(_id, params, signal) {
        await task(pi, ["set", String(params.id), "--link", params.url], signal);
        return text(await task(pi, ["show", String(params.id), "--json"], signal));
      },
    }),
  );

  pi.registerTool(
    defineTool({
      name: "task_done",
      label: "Complete Task",
      description: "Mark a task done (one-off tasks are removed; recurring ones advance).",
      parameters: Type.Object({ id: Id }),
      async execute(_id, params, signal) {
        return text(await task(pi, ["done", String(params.id)], signal));
      },
    }),
  );
}
