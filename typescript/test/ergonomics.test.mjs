import assert from "node:assert/strict";
import http from "node:http";
import { describe, it } from "node:test";
import { connect, providerManifest, providerPresets } from "../dist/index.js";
import { readFile } from "node:fs/promises";

function server(handler) {
  const s = http.createServer(handler);
  return new Promise((res) => s.listen(0, "127.0.0.1", () => res(s)));
}

describe("ergonomics", () => {
  it("generate string shorthand", async () => {
    const captures = [];
    const s = await server((req, res) => {
      let body = "";
      req.on("data", (c) => (body += c));
      req.on("end", () => {
        captures.push(JSON.parse(body));
        res.writeHead(200, { "content-type": "application/json" });
        res.end(JSON.stringify({ id: "id", object: "chat.completion", created: 1, model: "m", choices: [{ index: 0, message: { role: "assistant", content: "Hi" }, finish_reason: "stop" }] }));
      });
    });
    const { port } = s.address();
    const client = connect({ driver: "openai-compatible", endpoint: `http://127.0.0.1:${port}/v1`, model: "m" });
    const res = await client.generate("Hello");
    assert.equal(res.text, "Hi");
    // differential: shorthand vs explicit must be wire-identical (frozen array form)
    await client.generate({ messages: [{ role: "user", content: "Hello" }] });
    assert.deepEqual(captures[0], captures[1]);
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

  it("provider presets select a protocol and allow endpoint overrides", () => {
    assert.equal(providerPresets.muse.driver, "openai-compatible");
    assert.doesNotThrow(() => connect({ provider: "muse", credentials: "token", model: "muse-wonder-1.0" }));
    assert.doesNotThrow(() => connect({ provider: "groq", endpoint: "http://127.0.0.1:1/v1", model: "test" }));
    assert.throws(() => connect({ provider: "muse", driver: "ollama", model: "test" }), /same protocol/);
  });

  it("matches the canonical provider manifest and exposes every required capability", async () => {
    const canonical = JSON.parse(await readFile(new URL("../../conformance/providers.json", import.meta.url), "utf8"));
    assert.deepEqual(providerManifest, canonical);
    assert.deepEqual(Object.keys(providerPresets), Object.keys(canonical.providers));
    assert.ok(canonical.languages.includes("typescript"));
    assert.deepEqual(canonical.capabilities, ["provider_preset", "endpoint_override"]);
  });

  it("stream string shorthand", async () => {
    const captures = [];
    const s = await server((req, res) => {
      let body = "";
      req.on("data", (c) => (body += c));
      req.on("end", () => {
        captures.push(JSON.parse(body));
        res.writeHead(200, { "content-type": "text/event-stream" });
        res.end(`data: ${JSON.stringify({ choices: [{ index: 0, delta: { content: "Hi" }, finish_reason: null }] })}\n\ndata: ${JSON.stringify({ choices: [{ index: 0, delta: {}, finish_reason: "stop" }] })}\n\ndata: [DONE]\n\n`);
      });
    });
    const { port } = s.address();
    const client2 = connect({ driver: "openai-compatible", endpoint: `http://127.0.0.1:${port}/v1` });
    const m = client2.model("m");
    for await (const _ of m.stream("Hello")) {}
    for await (const _ of m.stream({ messages: [{ role: "user", content: "Hello" }] })) {}
    assert.deepEqual(captures[0], captures[1]);
    await new Promise((r) => s.close(r));
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
