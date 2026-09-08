import assert from "node:assert/strict";
import http from "node:http";
import { describe, it } from "node:test";
import { connect } from "../dist/index.js";

function server(handler) {
  const s = http.createServer(handler);
  return new Promise((res) => s.listen(0, "127.0.0.1", () => res(s)));
}

describe("ergonomics", () => {
  it("generate string shorthand", async () => {
    const s = await server((req, res) => {
      let body = "";
      req.on("data", (c) => (body += c));
      req.on("end", () => {
        const v = JSON.parse(body);
        assert.equal(v.messages[0].content, "Hello");
        assert.equal(v.model, "m");
        res.writeHead(200, { "content-type": "application/json" });
        res.end(JSON.stringify({ id: "id", object: "chat.completion", created: 1, model: "m", choices: [{ index: 0, message: { role: "assistant", content: "Hi" }, finish_reason: "stop" }] }));
      });
    });
    const { port } = s.address();
    const client = connect({ driver: "openai-compatible", endpoint: `http://127.0.0.1:${port}/v1`, model: "m" });
    // client is a Model when model is provided
    const res = await client.generate("Hello");
    assert.equal(res.text, "Hi");
    await new Promise((r) => s.close(r));
  });

  it("Ollama default endpoint", async () => {
    const c = connect({ driver: "ollama", model: "qwen3:8b" });
    // should not throw, and model should be usable (endpoint defaults to http://localhost:11434)
    assert.ok(c);
    // explicit endpoint overrides
    const c2 = connect({ driver: "ollama", endpoint: "http://example.com:11434", model: "qwen3:8b" });
    assert.ok(c2);
  });

  it("openai-compatible still requires endpoint", () => {
    assert.throws(() => connect({ driver: "openai-compatible", model: "x" }), /endpoint/);
  });

  it("old GenerationRequest still works", async () => {
    const s = await server((req, res) => {
      let body = "";
      req.on("data", (c) => (body += c));
      req.on("end", () => {
        res.writeHead(200, { "content-type": "application/json" });
        res.end(JSON.stringify({ id: "id", object: "chat.completion", created: 1, model: "m", choices: [{ index: 0, message: { role: "assistant", content: "Hi" }, finish_reason: "stop" }] }));
      });
    });
    const { port } = s.address();
    const client = connect({ driver: "openai-compatible", endpoint: `http://127.0.0.1:${port}/v1` });
    const model = client.model("m");
    const res = await model.generate({ messages: [{ role: "user", content: "Hello" }] });
    assert.equal(res.text, "Hi");
    await new Promise((r) => s.close(r));
  });
});
