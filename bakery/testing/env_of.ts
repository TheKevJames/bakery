/** Test-only: an `env_of` tool reporting pi's own environment variable. */
import { defineTool, type ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";

export default function (pi: ExtensionAPI) {
  pi.registerTool(
    defineTool({
      name: "env_of",
      label: "env_of",
      description: "Report one of pi's environment variables",
      parameters: Type.Object({ name: Type.String() }),
      async execute(_id, params) {
        const value = process.env[params.name] ?? "<unset>";
        return { content: [{ type: "text" as const, text: `${params.name}=${value}` }], details: {} };
      },
    }),
  );
}
