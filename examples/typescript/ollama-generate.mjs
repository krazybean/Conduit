import { connect } from "../../typescript/dist/index.js";

const client = connect({ driver: "ollama", endpoint: process.env.CONDUIT_ENDPOINT ?? "http://localhost:11434" });
console.log(await client.listModels());
const model = client.model(process.env.CONDUIT_MODEL ?? "llama3");
const res = await model.generate({ messages: [{ role: "user", content: "Hello" }] });
console.log(res.text);
for await (const e of model.stream({ messages: [{ role: "user", content: "Hello" }] })) {
  if (e.type === "text_delta") process.stdout.write(e.text);
}
console.log();
