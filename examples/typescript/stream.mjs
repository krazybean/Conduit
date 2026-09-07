import { connect } from "../../typescript/dist/index.js";

const model = connect({
  driver: "openai-compatible",
  endpoint: process.env.CONDUIT_ENDPOINT,
  model: process.env.CONDUIT_MODEL ?? "my-model",
  credentials: process.env.CONDUIT_API_KEY,
});

for await (const event of model.stream({
  messages: [{ role: "user", content: "Tell me something interesting." }],
})) {
  if (event.type === "text_delta") process.stdout.write(event.text);
}
