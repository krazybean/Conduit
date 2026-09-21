import { connect } from "../../typescript/dist/index.js";

const model = connect({ driver: "ollama", model: "qwen3.5:9b" });

for await (const event of model.stream({
  messages: [{ role: "user", content: "Describe Conduit in one sentence." }],
  maxOutputTokens: 48,
  providerOptions: { think: false },
})) {
  if (event.type === "text_delta") process.stdout.write(event.text);
}

process.stdout.write("\n");
