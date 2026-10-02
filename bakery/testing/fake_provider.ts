/**
 * Test-only provider: an OpenAI-compatible endpoint at $BAKERY_FAKE_LLM_URL.
 * Pi's built-in openai-completions client does the talking, so tests exercise
 * the real request path with a canned model on the other end.
 */
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

export default function (pi: ExtensionAPI) {
  pi.registerProvider("fake", {
    baseUrl: process.env.BAKERY_FAKE_LLM_URL,
    api: "openai-completions",
    apiKey: "fake",
    models: [
      {
        id: "echo",
        name: "Echo",
        reasoning: false,
        input: ["text"],
        // $ per million tokens: $1 per prompt token, so tests can buy
        // exact costs via the reply's prompt_tokens.
        cost: { input: 1_000_000, output: 0, cacheRead: 0, cacheWrite: 0 },
        contextWindow: 200_000,
        maxTokens: 8_192,
      },
    ],
  });
}
