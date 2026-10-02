/**
 * Context Extension
 *
 * Injects the files listed in the profile's context.toml (see config.ts):
 *
 * - `every_turn` files are re-read before each agent run and added as context
 *   files, rendered like AGENTS.md, so edits take effect on the next prompt.
 * - `session_start` files (eg. memory) are sent once as a hidden custom
 *   message. They are re-sent whenever that message is no longer part of the
 *   model's context, which covers resumed sessions and compaction.
 */
import { type ExtensionAPI, getAgentDir } from "@earendil-works/pi-coding-agent";
import { type ContextConfig, type LoadedFile, loadConfig, readFiles } from "./config.ts";

const SESSION_START_TYPE = "bakery-context";

function renderSessionStart(files: LoadedFile[]): string {
  return [
    "Files loaded at session start. They may have changed since; re-read them before relying on details.",
    ...files.map((f) => `<context_file path="${f.path}">\n${f.content}\n</context_file>`),
  ].join("\n\n");
}

export default function context(pi: ExtensionAPI) {
  let config: ContextConfig | undefined;

  pi.on("session_start", async () => {
    config = loadConfig(getAgentDir());
    if (config) {
      // Fail at startup rather than on the first prompt.
      readFiles(config, "every_turn");
      readFiles(config, "session_start");
    }
  });

  pi.on("before_agent_start", async (event, ctx) => {
    if (!config) return;

    event.systemPromptOptions.contextFiles = [
      ...readFiles(config, "every_turn").map(({ path, content }) => ({ path, content })),
      ...event.systemPromptOptions.contextFiles,
    ];

    const injected = ctx.sessionManager
      .buildContextEntries()
      .some((entry) => entry.type === "custom_message" && entry.customType === SESSION_START_TYPE);
    if (injected) return;

    const files = readFiles(config, "session_start");
    if (files.length === 0) return;
    return {
      message: {
        customType: SESSION_START_TYPE,
        content: renderSessionStart(files),
        display: false,
        details: { paths: files.map((f) => f.path) },
      },
    };
  });
}
