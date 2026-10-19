/**
 * Ask Operator Extension (claws only)
 *
 * An `ask_operator` tool which puts questions to the operator on the task
 * list: it appends them to a task the claw has claimed, moves the task to
 * `human`, releases it, and ends the run (`terminate`). Nothing waits on the
 * operator: they answer on the task and send it back through triage.
 *
 * The result's `details.reply` is what the gateway posts as the run's reply,
 * since a terminated run has no final message of its own.
 */
import { defineTool, type ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";
import { checkClaimed, Id, ref, task } from "./lib/task-list.ts";

const HUMAN = "human";

type Question = { question: string; context: string; default: string };

function indent(text: string): string {
  return text.trim().replaceAll("\n", "\n   ");
}

function today(): string {
  const now = new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
}

/** Numbered, so the operator can answer each under `## Answers` by number. */
function questionsSection(questions: Question[]): string {
  const items = questions.map(
    (q, i) =>
      `${i + 1}. ${indent(q.question)}\n` +
      `   **Context:** ${indent(q.context)}\n` +
      `   **Default:** ${indent(q.default)}`,
  );
  return `## Questions (${today()})\n\n${items.join("\n")}\n`;
}

export default function (pi: ExtensionAPI) {
  pi.registerTool(
    defineTool({
      name: "ask_operator",
      label: "Ask Operator",
      description:
        "Put questions to the operator on a task you have claimed: appends them under `## Questions`, moves " +
        `the task to \`${HUMAN}\`, releases it, and ends your run. Call it last, after recording everything ` +
        "else on the task: you get no further turn. The operator answers under `## Answers`, by number; an " +
        "unanswered question means they accept its default.",
      parameters: Type.Object({
        id: Id,
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
        await checkClaimed(pi, params.id, signal);
        const id = ref(params.id);
        const section = questionsSection(params.questions);
        await task(pi, ["set", id, "--tag", HUMAN, "--description-append", section, "--json"], signal);
        await task(pi, ["unset", id, "owner", "--json"], signal);
        const count = params.questions.length;
        const noun = count === 1 ? "question" : "questions";
        return {
          content: [{ type: "text" as const, text: `Moved ${id} to ${HUMAN} with ${count} ${noun}. Stop here.` }],
          details: { task: id, reply: `→ ${id} is in \`${HUMAN}\`.\n\n${section}` },
          terminate: true,
        };
      },
    }),
  );
}
