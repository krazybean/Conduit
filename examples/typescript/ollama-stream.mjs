import { connect } from "../../typescript/dist/index.js";

const model = connect({ driver: "ollama", model: "qwen3.5:9b" });
const prompt = "Conduit is a lightweight, provider-neutral AI driver for OpenAI-compatible, Ollama, Anthropic, and Gemini APIs. Rephrase that in one short sentence without adding facts.";

for await (const event of model.stream({
  messages: [{ role: "user", content: prompt }],
  maxOutputTokens: 40,
  providerOptions: { think: false },
})) {
  if (event.type === "text_delta") process.stdout.write(event.text);
}

process.stdout.write("\n");
