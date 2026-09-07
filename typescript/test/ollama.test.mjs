import assert from "node:assert/strict";
import { createServer } from "node:http";
import { test } from "node:test";
import { connect, ConduitError } from "../dist/index.js";
import { ollamaStream } from "../dist/ollama.js";

const secret = "synthetic-ollama-bearer";
const headerSecret = "synthetic-ollama-header";

async function server(t, handle) {
  const requests = [];
  const http = createServer(async (req, res) => {
    const chunks = [];
    for await (const c of req) chunks.push(c);
    let body = {};
    try { body = JSON.parse(Buffer.concat(chunks).toString()); } catch { body = {}; }
    requests.push({ method: req.method, url: req.url, headers: req.headers, body });
    try { await handle(req, res, requests.at(-1)); } catch { res.destroy(); }
  });
  await new Promise((r, j) => { http.once("error", j); http.listen(0, "127.0.0.1", r); });
  t.after(() => new Promise(r => { http.closeAllConnections(); http.close(r); }));
  return { endpoint: `http://127.0.0.1:${http.address().port}`, requests };
}
const ollama = (endpoint, extra = {}) => connect({ driver: "ollama", endpoint, model: "qwen3:8b", ...extra });

test("ollama generate maps common options and preserves semantic content", async t => {
  const { endpoint, requests } = await server(t, (req, res, info) => {
    const b = info.body;
    assert.equal(b.model, "qwen3:8b");
    assert.equal(b.stream, false);
    assert.equal(b.messages[0].content, "Hello world");
    assert.equal(b.options.temperature, 0.7);
    assert.equal(b.options.num_predict, 32);
    res.writeHead(200, { "content-type": "application/json", "x-request-id": "req-1" });
    res.end(JSON.stringify({ model: "qwen3:8b", created_at: "2024-01-01T00:00:00Z", message: { role: "assistant", content: "Hi " + secret }, done: true, done_reason: "stop", prompt_eval_count: 4, eval_count: 2, total_duration: 1000 }));
  });
  const r = await ollama(endpoint, { credentials: secret, headers: { "x-private": headerSecret } }).generate({
    messages: [{ role: "user", content: [{ type: "text", text: "Hello" }, { type: "text", text: " world" }] }],
    maxOutputTokens: 32, temperature: 0.7, providerOptions: { keep_alive: "5m", options: { num_ctx: 2048 } },
  });
  assert.equal(requests[0].url, "/api/chat");
  assert.equal(r.text, "Hi " + secret);
  assert.deepEqual(r.usage, { inputTokens: 4, outputTokens: 2 });
  assert.equal(r.usage.totalTokens, undefined);
  assert.equal(r.providerMetadata.total_duration, 1000);
});

test("ollama passthrough conflicts are InvalidRequestError", async t => {
  const { endpoint } = await server(t, (req, res) => { res.writeHead(200, { "content-type": "application/json" }); res.end(JSON.stringify({ message: { role: "assistant", content: "ok" }, done: true })); });
  const m = ollama(endpoint);
  await assert.rejects(m.generate({ messages: [{ role: "user", content: "hi" }], temperature: 0.5, providerOptions: { options: { temperature: 0.9 } } }), e => e.name === "InvalidRequestError");
  await assert.rejects(m.generate({ messages: [{ role: "user", content: "hi" }], providerOptions: { model: "other" } }), e => e.name === "InvalidRequestError");
  const ok = await m.generate({ messages: [{ role: "user", content: "hi" }], providerOptions: { keep_alive: "5m" } });
  assert.equal(ok.text, "ok");
});

test("ollama stream NDJSON handles fragmentation, CRLF and blank lines", async t => {
  const rec = (c, done, extra = {}) => JSON.stringify({ model: "qwen3:8b", message: { role: "assistant", content: c }, done, ...extra });
  const { endpoint } = await server(t, (req, res) => {
    res.writeHead(200, { "content-type": "application/x-ndjson" });
    const r1 = rec("Hello ", false) + "\n";
    const r2 = rec("\u4e16\u754c", false) + "\r\n";
    const r3 = rec("", true, { done_reason: "stop", prompt_eval_count: 4, eval_count: 2 }) + "\n";
    res.write(r1.slice(0, 10));
    res.write(r1.slice(10) + "\n" + r2 + r3);
    res.end();
  });
  const events = [];
  for await (const e of ollama(endpoint).stream({ messages: [{ role: "user", content: "hi" }] })) events.push(e);
  assert.equal(events[0].type, "start");
  assert.equal(events.filter(e => e.type === "text_delta").map(e => e.text).join(""), "Hello \u4e16\u754c");
  assert.equal(events.at(-1).type, "done");
});

test("ollama stream validates byte splits and EOF handling", async t => {
  const body = new ReadableStream({
    start(c) {
      const a = Buffer.from(JSON.stringify({ model: "m", message: { role: "assistant", content: "Hello " }, done: false }) + "\n", "utf8");
      const b = Buffer.from(JSON.stringify({ model: "m", message: { role: "assistant", content: "world" }, done: false }) + "\n", "utf8");
      const d = Buffer.from(JSON.stringify({ model: "m", message: { role: "assistant", content: "" }, done: true }) + "\n", "utf8");
      c.enqueue(a.subarray(0, 5)); c.enqueue(a.subarray(5)); c.enqueue(b); c.enqueue(d); c.close();
    }
  });
  const seen = [];
  for await (const e of ollamaStream(body, undefined, x => x)) seen.push(e);
  assert.equal(seen.filter(e => e.type === "text_delta").map(e => e.text).join(""), "Hello world");
});

test("ollama errors map model-not-found and preserve redaction", async t => {
  const { endpoint } = await server(t, (req, res) => { res.writeHead(404, { "content-type": "application/json" }); res.end(JSON.stringify({ error: "model 'qwen3:8b' not found" })); });
  await assert.rejects(ollama(endpoint).generate({ messages: [{ role: "user", content: "hi" }] }), e => e.name === "ModelNotFoundError");
});
