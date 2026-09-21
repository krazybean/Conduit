import { connect } from "../../typescript/dist/index.js";

const apiKey = process.env.GROQ_API_KEY;
if (!apiKey) throw new Error("GROQ_API_KEY is required");

const model = connect({
  driver: "openai-compatible",
  endpoint: "https://api.groq.com/openai/v1",
  credentials: apiKey,
  model: "qwen/qwen3.8-27b",
});

const prompt = "Conduit is a lightweight, provider-neutral AI driver for OpenAI-compatible, Ollama, Anthropic, and Gemini APIs. Rephrase that in one short sentence without adding facts.";

for await (const event of model.stream({
  messages: [{ role: "user", content: prompt }],
  maxOutputTokens: 40,
  providerOptions: { reasoning_effort: "none" },
})) {
  if (event.type === "text_delta") process.stdout.write(event.text);
}

process.stdout.write("\n");
