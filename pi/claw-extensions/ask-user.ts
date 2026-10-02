/**
 * Ask User Extension (claws only)
 *
 * An `ask_user` tool whose behaviour follows the claw's ask policy, passed by
 * the gateway as BAKERY_ASK_POLICY (ask | assume | park),
 * BAKERY_ASK_TIMEOUT_HOURS, and BAKERY_ASK_ON_TIMEOUT (assume | park).
 *
 * "Parking" ends the run (`terminate`) with `details.park` on the result; the
 * gateway marks the run parked and resumes it when the question is answered.
 */
import { defineTool, type ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";

type Policy = "ask" | "assume" | "park";

function env(name: string, allowed: string[], fallback: string): string {
  const value = process.env[name] ?? fallback;
  if (!allowed.includes(value)) throw new Error(`${name} must be one of ${allowed.join(", ")}`);
  return value;
}

function text(message: string, details: Record<string, unknown>) {
  return { content: [{ type: "text" as const, text: message }], details };
}

function assume(reason: string, assumption: string) {
  return text(
    `${reason} Proceed with your assumption (${assumption}) and state it clearly in your final report.`,
    { answered: false },
  );
}

function park(question: string) {
  // terminate ends the run without another model call (unless sibling tool
  // calls in the same batch continue it; the gateway then aborts the run).
  return {
    ...text("Stopping until the user answers. Do not continue working on this task.", {
      park: true,
      question,
    }),
    terminate: true,
  };
}

const askUser = defineTool({
  name: "ask_user",
  label: "Ask User",
  description:
    "Ask the user a question when you cannot proceed responsibly without their input. " +
    "Always state the assumption you would otherwise make.",
  parameters: Type.Object({
    question: Type.String({ description: "The question, with enough context to answer it cold" }),
    assumption: Type.String({ description: "What you will assume if no answer arrives" }),
  }),

  async execute(_toolCallId, params, signal, _onUpdate, ctx) {
    const policy = env("BAKERY_ASK_POLICY", ["ask", "assume", "park"], "ask") as Policy;
    const onTimeout = env("BAKERY_ASK_ON_TIMEOUT", ["assume", "park"], "park");
    const hours = Number(process.env.BAKERY_ASK_TIMEOUT_HOURS ?? "4");

    if (policy === "assume") return assume("Policy is to not ask.", params.assumption);
    if (policy === "park" || !ctx.hasUI) return park(params.question);

    const answer = await ctx.ui.input(params.question, `if unanswered: ${params.assumption}`, {
      signal,
      timeout: hours * 3_600_000,
    });
    if (answer !== undefined && answer.trim()) {
      return text(`The user answered: ${answer.trim()}`, { answered: true });
    }
    if (onTimeout === "assume") return assume(`No answer within ${hours}h.`, params.assumption);
    return park(params.question);
  },
});

export default function (pi: ExtensionAPI) {
  pi.registerTool(askUser);
}
