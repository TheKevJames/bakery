/**
 * Task Extension (claws only)
 *
 * Tools over the shared `task` list (`task` CLI, $TASK_FOLDER), which I use
 * too. A claw's `policy.tools` decides which tools it gets. Claims are always
 * made as the claw itself (BAKERY_CLAW), and `task_add` files into the claw's
 * policy `task_tag` (BAKERY_TASK_TAG); `task` serializes concurrent writers.
 */
import { defineTool, type ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";

function claw(): string {
  const name = process.env.BAKERY_CLAW;
  if (!name) throw new Error("BAKERY_CLAW is not set");
  return name;
}

function taskTag(): string {
  const tag = process.env.BAKERY_TASK_TAG;
  if (!tag) throw new Error("BAKERY_TASK_TAG is not set");
  return tag;
}

function text(message: string, details: Record<string, unknown> = {}) {
  return { content: [{ type: "text" as const, text: message }], details };
}

async function run(pi: ExtensionAPI, command: string, args: string[], signal?: AbortSignal): Promise<string> {
  const result = await pi.exec(command, args, { signal });
  if (result.code !== 0) throw new Error(result.stderr.trim() || `${command} exited with ${result.code}`);
  return result.stdout.trim();
}

async function task(pi: ExtensionAPI, args: string[], signal?: AbortSignal): Promise<string> {
  return run(pi, "task", args, signal);
}

// Blocked tasks must say what they wait on, in a form triage's collectors can
// check (bakery/bakery/collectors/blocked.py).
const BLOCKED = "bakery/blocked";

const Id = Type.Integer({ minimum: 1, description: "Task id" });
const Priority = Type.Union([Type.Literal("low"), Type.Literal("medium"), Type.Literal("high")]);
const Size = Type.Union([Type.Literal("small"), Type.Literal("medium"), Type.Literal("large")]);

export default function tasks(pi: ExtensionAPI) {
  pi.registerTool(
    defineTool({
      name: "task_list",
      label: "List Tasks",
      description:
        "List tasks as JSON. Filters are comma-separated `field<op>value` on summary, tag, owner, " +
        "link, priority, or size, with = (equals), != , ~ (contains), !~; eg. " +
        "`tag=bakery/build,owner=` (unowned). Done tasks are hidden unless `done` is set, which lists only " +
        "them; done tasks in `bakery/wontfix` are permanent rejections.",
      parameters: Type.Object({
        filter: Type.Optional(Type.String()),
        done: Type.Optional(Type.Boolean({ description: "List only done tasks" })),
      }),
      async execute(_id, params, signal) {
        const args = ["list", "--json"];
        if (params.filter) args.push("-f", params.filter);
        if (params.done) args.push("--done");
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
        `Add a task${process.env.BAKERY_TASK_TAG ? ` in \`${process.env.BAKERY_TASK_TAG}\`` : ""}. ` +
        "`link` is its lineage: the exact source URL, which is also how duplicates are recognized, so copy it " +
        "verbatim.",
      parameters: Type.Object({
        summary: Type.String({ maxLength: 120, description: "One line, at most ~80 characters" }),
        description: Type.String({ description: "Source, link, and enough context to triage it cold" }),
        link: Type.String({ description: "The source URL, verbatim" }),
        priority: Type.Optional(Priority),
        size: Type.Optional(Size),
      }),
      async execute(_id, params, signal) {
        const args = ["add", "--tag", taskTag(), "--description", params.description, "--link", params.link];
        if (params.priority) args.push("--priority", params.priority);
        if (params.size) args.push("--size", params.size);
        args.push("--", params.summary);
        await task(pi, args, signal);
        const added = JSON.parse(await task(pi, ["list", "--json", "-s", "id", "-f", `link=${params.link}`], signal));
        return text(`Added: ${JSON.stringify(added.at(-1) ?? {})}`);
      },
    }),
  );

  pi.registerTool(
    defineTool({
      name: "task_set",
      label: "Update Task",
      description:
        "Update a task: move it to a section (`tag`, eg. `bakery/build`), set its priority or size, " +
        "claim it for yourself (fails if someone else owns it), release your claim, or append notes " +
        `to its description. Moving it to \`${BLOCKED}\` fails unless its notes, with any appended now, ` +
        "hold a valid `**Blocked on:**` list (the last one counts) of blockers which have not cleared.",
      parameters: Type.Object({
        id: Id,
        tag: Type.Optional(Type.String({ description: "Section path, eg. bakery/human" })),
        claim: Type.Optional(Type.Boolean()),
        release: Type.Optional(Type.Boolean()),
        priority: Type.Optional(Priority),
        size: Type.Optional(Size),
        description_append: Type.Optional(Type.String()),
      }),
      async execute(_id, params, signal) {
        if (params.claim && params.release) throw new Error("claim and release are exclusive");
        const id = String(params.id);
        if (params.release) {
          const current = JSON.parse(await task(pi, ["show", id, "--json"], signal)) as { owner: string | null };
          if (current.owner !== claw()) throw new Error(`task ${id} is not claimed by ${claw()}`);
        }
        if (params.tag === BLOCKED) {
          const check = ["blocked", "check", id];
          if (params.description_append) check.push("--append", params.description_append);
          await run(pi, "bakery", check, signal);
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
      description: "Mark a task done (one-off tasks are hidden; recurring ones advance).",
      parameters: Type.Object({ id: Id }),
      async execute(_id, params, signal) {
        return text(await task(pi, ["done", String(params.id)], signal));
      },
    }),
  );
}
