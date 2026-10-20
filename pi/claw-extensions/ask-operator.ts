/**
 * Ask Operator Extension (claws only)
 *
 * An `ask_operator` tool which puts questions to the operator on the task
 * list: it asks them as the task's questions, releases the task, and ends the
 * run (`terminate`). Nothing waits on the operator: the task stays where it
 * is, skipped by its section's collector, until they answer every question.
 *
 * It runs `task` like any other command the claw runs, so the gateway's task
 * server applies the claw's rules to it; the server also refuses any question
 * without its context and default as this tool writes them
 * (bakery/bakery/task_list/hooks.py).
 *
 * The result's `details.reply` is what the gateway posts as the run's reply,
 * since a terminated run has no final message of its own.
 */
import { defineTool, type ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";

type Question = { question: string; context: string; default: string };
type Asked = { number: number; body: string };

function namespace(): string {
  const ns = process.env.BAKERY_TASK_NAMESPACE;
  if (!ns) throw new Error("BAKERY_TASK_NAMESPACE is not set");
  return ns;
}

async function task(pi: ExtensionAPI, args: string[], signal?: AbortSignal): Promise<string> {
  const result = await pi.exec("task", args, { signal });
  if (result.code !== 0) throw new Error(result.stderr.trim() || `task exited with ${result.code}`);
  return result.stdout.trim();
}

/** One question as the task records it, so it reads cold. */
function body(q: Question): string {
  return `${q.question.trim()}\n**Context:** ${q.context.trim()}\n**Default:** ${q.default.trim()}`;
}

function indent(text: string): string {
  return text.replaceAll("\n", "\n   ");
}

export default function (pi: ExtensionAPI) {
  pi.registerTool(
    defineTool({
      name: "ask_operator",
      label: "Ask Operator",
      description:
        `Put questions to the operator on a task in \`${process.env.BAKERY_TASK_NAMESPACE ?? "your namespace"}\`: ` +
        "asks them as the task's questions, releases it, and ends your run. The task stays where it is, " +
        "untouched by any claw until the operator answers every question. " +
        "Call it last, after recording everything else on the task: you get no further turn. The operator " +
        'answers every question, by its number (`Q1`, `Q2`, ...); "default" means they accept its default.',
      parameters: Type.Object({
        id: Type.Integer({ minimum: 1, description: "The task's id: the number in `<namespace>:<id>`" }),
        questions: Type.Array(
          Type.Object({
            question: Type.String({ description: "One question, answerable cold" }),
            context: Type.String({ description: "What you found, and why you cannot decide it yourself" }),
            default: Type.String({ description: 'What you would do if told "your call"' }),
          }),
          { minItems: 1 },
        ),
      }),

      async execute(_toolCallId, params, signal) {
        const id = `${namespace()}:${params.id}`;
        const asking = params.questions.flatMap((q) => ["--question", body(q)]);
        // Asking alone takes no claim, so claim first (a no-op if the run holds it) to have one to release.
        await task(pi, [id, "claim", "--json"], signal);
        const stored = await task(pi, [id, "set", ...asking, "--json"], signal);
        await task(pi, [id, "release", "--json"], signal);
        const count = params.questions.length;
        const asked: Asked[] = JSON.parse(stored).questions.slice(-count);
        const list = asked.map((q) => `Q${q.number}. ${indent(q.body)}`).join("\n");
        const noun = count === 1 ? "question" : "questions";
        return {
          content: [{ type: "text" as const, text: `Asked ${count} ${noun} on ${id}. Stop here.` }],
          details: { task: id, reply: `→ ${id} waits on the operator.\n\n${list}\n` },
          terminate: true,
        };
      },
    }),
  );
}
