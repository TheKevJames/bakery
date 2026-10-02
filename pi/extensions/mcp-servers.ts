/**
 * MCP Servers Extension
 *
 * Remote MCP servers shared by every profile. Registered from an extension
 * rather than mcp.json because mcp.json is per agent dir (profile).
 */
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

export default function mcpServers(pi: ExtensionAPI) {
  pi.registerMcpServer("context7", {
    url: "https://mcp.context7.com/mcp",
    exposure: "direct",
    description: "Up-to-date documentation and code examples for programming libraries.",
  });
}
