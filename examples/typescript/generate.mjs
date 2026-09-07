import { connect } from "../../typescript/dist/index.js";

const model = connect({
  driver: "openai-compatible",
  endpoint: process.env.CONDUIT_ENDPOINT,
  model: process.env.CONDUIT_MODEL ?? "my-model",
  credentials: process.env.CONDUIT_API_KEY,
});

const response = await model.generate({
  messages: [
    { role: "system", content: "Be concise." },
    { role: "user", content: "Hello." },
  ],
});

console.log(response.text);
