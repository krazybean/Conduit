import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { getEventListeners } from "node:events";
import { inspect, promisify } from "node:util";
import { execFile } from "node:child_process";
import { test } from "node:test";
import { setTimeout as delay } from "node:timers/promises";
import { connect, ConduitError } from "../dist/index.js";
import { openaiStream } from "../dist/openai-stream.js";
import { server } from "./http.mjs";

const fixtures = JSON.parse(await readFile(new URL("../../conformance/streams/openai-text.json", import.meta.url), "utf8"));
const requestFixture = JSON.parse(await readFile(new URL("../../conformance/requests/openai-text.json", import.meta.url), "utf8"));
const errorFixtures = JSON.parse(await readFile(new URL("../../conformance/errors/openai-http.json", import.meta.url), "utf8"));
const input = { messages: [{ role: "user", content: "Hello" }] };
const secret = "synthetic-stream-bearer";
const headerSecret = "synthetic-stream-header";
const selected = (endpoint, extra = {}) => connect({ driver: "openai-compatible", endpoint, model: "test-model", ...extra });
const chunk = (content, finish = null, extra = {}) => ({ choices: [{ index: 0, delta: content === undefined ? {} : { content }, finish_reason: finish }], ...extra });
const sse = value => `data: ${JSON.stringify(value)}\n\n`;
const ending = sse(chunk(undefined, "stop")) + "data: [DONE]\n\n";
const headers = { "content-type": "text/event-stream" };
const category = name => error => error instanceof ConduitError && error.name === name;
const noSecrets = value => {
  for (const text of [JSON.stringify(value), String(value), inspect(value, { showHidden: true, depth: 12 })]) {
    assert.ok(!text?.includes(secret), "Credential leaked");
    assert.ok(!text?.includes(headerSecret), "Custom header leaked");
  }
};
async function collect(iterator) { const events = []; for await (const event of iterator) events.push(event); return events; }

function semantic(event) {
  if (event.type === "usage") return { type: "usage", usage: semanticUsage(event.usage) };
  if (event.type !== "done") return event;
  const r = event.response;
  return { type: "done", response: {
    ...(r.id !== undefined && { id: r.id }), ...(r.model !== undefined && { model: r.model }),
    content: r.content, finish_reason: r.finishReason,
    ...(r.usage !== undefined && { usage: semanticUsage(r.usage) }),
    provider_metadata: { finish_reason: r.providerMetadata.finishReason, ...(r.providerMetadata.requestId !== undefined && { request_id: r.providerMetadata.requestId }) },
  } };
}
function semanticUsage(usage) {
  return Object.fromEntries([["input_tokens", usage.inputTokens], ["output_tokens", usage.outputTokens], ["total_tokens", usage.totalTokens]].filter(([, value]) => value !== undefined));
}

async function verify(iterator, expected) {
  const events = [];
  let failure;
  try { for await (const event of iterator) events.push(event); } catch (error) { failure = error; }
  if (expected.error) assert.ok(category(expected.error.category)(failure));
  else assert.equal(failure, undefined);
  assert.deepEqual(events.map(semantic), expected.events);
  const done = events.find(event => event.type === "done");
  if (done) assert.equal(done.response.text, events.filter(event => event.type === "text_delta").map(event => event.text).join(""));
}

for (const f of fixtures) test(`stream fixture: ${f.id} (HTTP and exact read boundaries)`, async t => {
  const wire = f.input.wire;
  const fragments = wire.fragments_base64.map(part => Buffer.from(part, "base64"));
  const { endpoint } = await server(t, async (_request, response) => {
    response.writeHead(wire.status, wire.headers);
    for (const fragment of fragments) {
      if (response.destroyed) return;
      response.write(fragment);
      await delay(1);
    }
    response.end();
  });
  await verify(selected(endpoint).stream(input), f.expected);
  // TCP can coalesce writes; a native ReadableStream proves the exact fixture read boundaries too.
  let index = 0;
  let cancelled = false;
  const body = new ReadableStream({
    pull(controller) {
      if (index < fragments.length) controller.enqueue(fragments[index++]);
      else if (f.expected.error) controller.close(); // Successful providers may keep the socket open after DONE.
    },
    cancel() { cancelled = true; },
  });
  await verify(openaiStream(body, "stream-request", text => text), f.expected);
  assert.equal(body.locked, false);
  if (!f.expected.error) assert.ok(cancelled, "[DONE] should cancel the remaining body");
});

test("every byte split reconstructs SSE, JSON, CRLF, UTF-8, and DONE", async () => {
  const bytes = Buffer.concat(fixtures.find(f => f.id === "crlf").input.wire.fragments_base64.map(part => Buffer.from(part, "base64")));
  let index = 0;
  const body = new ReadableStream({ pull(controller) { if (index < bytes.length) controller.enqueue(bytes.subarray(index, ++index)); else controller.close(); } });
  await verify(openaiStream(body, "stream-request", text => text), fixtures[0].expected);
});

test("stream is lazy, uses the shared request, and yields before the provider finishes", async t => {
  let finish;
  const gate = new Promise(resolve => { finish = resolve; });
  t.after(finish);
  const { endpoint, requests } = await server(t, async (_request, response) => {
    response.writeHead(200, headers);
    response.write(sse(chunk("first")));
    await gate;
    response.end(ending);
  });
  const r = requestFixture.input.request;
  const iterator = selected(endpoint, { credentials: secret }).stream({ messages: r.messages, maxOutputTokens: r.max_output_tokens, temperature: r.temperature, topP: r.top_p, stop: r.stop, providerOptions: r.provider_options });
  assert.equal(typeof iterator.then, "undefined");
  await delay(10);
  assert.equal(requests.length, 0);
  assert.equal((await iterator.next()).value.type, "start");
  assert.equal((await iterator.next()).value.text, "first");
  assert.deepEqual(requests[0].body, { ...requestFixture.expected.wire_request.body, stream: true });
  assert.equal(requests[0].method, "POST");
  assert.equal(requests[0].path, "/v1/chat/completions");
  assert.ok(requests[0].headers.authorization === `Bearer ${secret}`, "Expected bearer header");
  assert.ok(!Object.hasOwn(requests[0].body, "stream_options"));
  finish();
  const rest = await collect(iterator);
  assert.equal(rest.length, 1);
  assert.equal(rest[0].type, "done");
  assert.equal(rest[0].response.text, "first");
});

test("stream_options is explicit passthrough; owned fields and n remain protected", async t => {
  const { endpoint, requests } = await server(t, (_request, response) => { response.writeHead(200, headers); response.end(sse(chunk("ok")) + ending); });
  const model = selected(endpoint);
  await collect(model.stream({ ...input, providerOptions: { stream_options: { include_usage: true }, native_flag: 1 } }));
  assert.deepEqual(requests[0].body.stream_options, { include_usage: true });
  assert.equal(requests[0].body.native_flag, 1);
  for (const key of ["model", "messages", "stream", "n", "max_tokens", "max_completion_tokens", "temperature", "top_p", "stop", "tools", "tool_choice", "functions", "function_call", "response_format", "modalities"]) {
    await assert.rejects(collect(model.stream({ ...input, providerOptions: { [key]: true } })), category("InvalidRequestError"));
  }
  for (const value of [null, false, [], 1]) await assert.rejects(collect(model.stream({ ...input, providerOptions: { stream_options: value } })), category("InvalidRequestError"));
  await assert.rejects(model.generate({ ...input, providerOptions: { stream_options: { include_usage: true } } }), category("InvalidRequestError"));
  assert.equal(requests.length, 1);
});

for (const f of errorFixtures) test(`stream HTTP failure reuses mapping: ${f.id}`, async t => {
  const wire = f.input.wire;
  const { endpoint, requests } = await server(t, (_request, response) => { response.writeHead(wire.status, wire.headers); response.end(wire.body_text ?? JSON.stringify(wire.body)); });
  const seen = [];
  await assert.rejects((async () => { for await (const event of selected(endpoint).stream(input)) seen.push(event); })(), error => {
    assert.equal(error.name, f.expected.error.category);
    assert.equal(error.message, f.expected.error.message);
    assert.equal(error.statusCode, f.expected.error.status_code);
    return error instanceof ConduitError;
  });
  assert.equal(seen.length, 0);
  assert.equal(requests.length, 1);
});

test("final streamed responses equal generate for equivalent outputs", async t => {
  for (const [text, finish, usage] of [["Hello 世界", "stop", { prompt_tokens: 0 }], ["", "length", undefined], ["hello", "future_reason", undefined], [null, "content_filter", undefined]]) {
    const { endpoint } = await server(t, (_request, response, wire) => {
      const metadata = { id: "same-id", model: "same-model" };
      response.setHeader("x-request-id", "same-request");
      if (wire.body.stream) {
        response.setHeader("content-type", "text/event-stream");
        response.end(sse(chunk(text, finish, { ...metadata, usage })) + "data: [DONE]\n\n");
      } else response.end(JSON.stringify({ ...metadata, choices: [{ message: { role: "assistant", content: text }, finish_reason: finish }], usage }));
    });
    const model = selected(endpoint);
    const generated = await model.generate(input);
    const events = await collect(model.stream(input));
    assert.deepEqual(events.at(-1).response, generated);
  }
});

test("invalid content, identity, usage, completion, and media types fail without done", async t => {
  let body;
  let contentType = "text/event-stream";
  const { endpoint } = await server(t, (_request, response) => { response.writeHead(200, { "content-type": contentType }); response.end(body); });
  for (const payload of [
    sse({ choices: [{ index: 1, delta: { content: "wrong" } }] }),
    sse({ choices: [] }), sse(chunk("text", 1)), sse(chunk("text", null, { usage: { total_tokens: -1 } })),
    sse(chunk("first", null, { id: "one" })) + sse(chunk("second", "stop", { id: "two" })),
    sse(chunk("first", null, { model: "one" })) + sse(chunk("second", "stop", { model: "two" })),
    sse(chunk("done", "stop")) + sse(chunk("too late")),
    sse({ choices: [{ index: 0, delta: { reasoning_content: "hidden" } }] }),
    sse({ choices: [{ index: 0, delta: { role: "user" } }] }),
    sse({ choices: [{ index: 0, delta: { content: [] } }] }),
    "data: {bad\n\n", "data: \n\n", "", ": heartbeat\n\n", "{\"choices\": []}\n\n",
  ]) {
    body = payload + "data: [DONE]\n\n";
    const events = [];
    await assert.rejects((async () => { for await (const event of selected(endpoint).stream(input)) events.push(event); })(), category("ProtocolError"));
    assert.ok(!events.some(event => event.type === "done"));
  }
  for (contentType of ["application/json", "text/plain"]) {
    body = sse(chunk("fine")) + ending;
    await assert.rejects(collect(selected(endpoint).stream(input)), category("ProtocolError"));
  }
});

test("native read failure after partial output is ConnectionError with no done", async t => {
  let fail;
  const { endpoint } = await server(t, (_request, response) => { response.writeHead(200, headers); response.write(sse(chunk("partial"))); fail = () => response.destroy(); });
  const iterator = selected(endpoint, { credentials: secret }).stream(input);
  assert.equal((await iterator.next()).value.type, "start");
  assert.equal((await iterator.next()).value.text, "partial");
  fail();
  await assert.rejects(iterator.next(), error => { noSecrets(error); return category("ConnectionError")(error); });
});

test("timeout before headers and after text covers the entire operation", async t => {
  let text = false;
  const { endpoint } = await server(t, (_request, response) => { if (text) { response.writeHead(200, headers); response.write(sse(chunk("partial"))); } });
  await assert.rejects(collect(selected(endpoint, { credentials: secret, timeout: 20 }).stream(input)), error => { noSecrets(error); return category("TimeoutError")(error); });
  text = true;
  const iterator = selected(endpoint, { credentials: secret, timeout: 5000 }).stream({ ...input, timeout: 50 });
  assert.equal((await iterator.next()).value.type, "start");
  assert.equal((await iterator.next()).value.text, "partial");
  await assert.rejects(iterator.next(), error => { noSecrets(error); return category("TimeoutError")(error); });
});

test("cancellation before I/O, while waiting for headers, and after text is CancelledError", async t => {
  let text = false;
  let seen;
  const arrived = new Promise(resolve => { seen = resolve; });
  const { endpoint, requests } = await server(t, (_request, response) => { seen(); if (text) { response.writeHead(200, headers); response.write(sse(chunk("partial"))); } });
  const model = selected(endpoint, { credentials: secret });
  await assert.rejects(collect(model.stream({ ...input, signal: AbortSignal.abort(secret) })), category("CancelledError"));
  assert.equal(requests.length, 0);
  const waiting = new AbortController();
  const before = model.stream({ ...input, signal: waiting.signal, timeout: 5000 });
  const pending = before.next();
  await arrived;
  waiting.abort(new DOMException(secret, "TimeoutError"));
  await assert.rejects(pending, error => { noSecrets(error); return category("CancelledError")(error); });
  text = true;
  const caller = new AbortController();
  const after = model.stream({ ...input, signal: caller.signal, timeout: 5000 });
  await after.next(); await after.next();
  caller.abort(secret);
  await assert.rejects(after.next(), error => { noSecrets(error); return category("CancelledError")(error); });
  assert.equal(getEventListeners(caller.signal, "abort").length, 0);
});

test("timeout during a consumer pause wins over later caller cancellation", async t => {
  const { endpoint } = await server(t, (_request, response) => { response.writeHead(200, headers); response.write(sse(chunk("partial"))); });
  const caller = new AbortController();
  const iterator = selected(endpoint).stream({ ...input, signal: caller.signal, timeout: 30 });
  await iterator.next();
  await delay(60);
  caller.abort(secret);
  await assert.rejects(iterator.next(), category("TimeoutError"));
});

test("break and completed done release readers, connections, timers, and listeners", async t => {
  let finish = false;
  let closed;
  const closedPromise = new Promise(resolve => { closed = resolve; });
  const { endpoint } = await server(t, (_request, response) => {
    response.on("close", closed);
    response.writeHead(200, headers);
    response.write(sse(chunk("partial")) + (finish ? ending : ""));
  });
  const nativeFetch = globalThis.fetch;
  let body;
  t.mock.method(globalThis, "fetch", async (...args) => { const response = await nativeFetch(...args); body = response.body; return response; });
  const nativeSet = globalThis.setTimeout;
  const nativeClear = globalThis.clearTimeout;
  let operationTimer;
  const cleared = new Set();
  t.mock.method(globalThis, "setTimeout", (callback, ms, ...args) => { const timer = nativeSet(callback, ms, ...args); if (ms === 54321) operationTimer = timer; return timer; });
  t.mock.method(globalThis, "clearTimeout", timer => { cleared.add(timer); return nativeClear(timer); });
  const caller = new AbortController();
  const model = selected(endpoint);
  for await (const event of model.stream({ ...input, timeout: 54321, signal: caller.signal })) {
    if (event.type === "text_delta") break;
  }
  await closedPromise;
  assert.equal(body.locked, false);
  assert.equal(getEventListeners(caller.signal, "abort").length, 0);
  assert.ok(cleared.has(operationTimer));
  finish = true;
  const iterator = model.stream({ ...input, timeout: 54321, signal: caller.signal });
  let event;
  do { event = (await iterator.next()).value; } while (event.type !== "done");
  // Do not call next/return after done: cleanup must already have happened.
  assert.equal(body.locked, false);
  assert.equal(getEventListeners(caller.signal, "abort").length, 0);
  assert.ok(cleared.has(operationTimer));
  caller.abort(secret);
  assert.equal((await iterator.next()).done, true);
});

test("semantic content matching secrets is preserved; diagnostics are redacted", async t => {
  const text = `before ${secret} and ${headerSecret} after`;
  const { endpoint } = await server(t, (_request, response) => {
    response.writeHead(200, { ...headers, "x-request-id": secret });
    for (const char of text) response.write(sse(chunk(char, null, { id: secret, model: headerSecret })));
    response.end(ending);
  });
  const events = await collect(selected(endpoint, { credentials: secret, headers: { "x-private": ` ${headerSecret} ` } }).stream(input));
  for (const event of events) {
    if (event.type === "start") noSecrets(event);
    if (event.type === "done") noSecrets(event.response.providerMetadata);
  }
  const deltas = events.filter(event => event.type === "text_delta").map(event => event.text).join("");
  assert.ok(deltas === text, "Semantic deltas must remain unchanged");
  assert.equal(events.at(-1).response.text, deltas);
});

test("overlapping secret values and trailing prefixes remain semantic content", async t => {
  for (const [credentials, header, text] of [["ab", "bcd", "abcd ab a"], ["abc", "bc", "abcabc bc a"], ["secret", "REDACTED", "secret secre"]]) {
    const { endpoint } = await server(t, (_request, response, wire) => {
      if (wire.body.stream) {
        response.writeHead(200, headers);
        for (const char of text) response.write(sse(chunk(char)));
        response.end(ending);
      } else response.end(JSON.stringify({ choices: [{ message: { role: "assistant", content: text }, finish_reason: "stop" }] }));
    });
    const model = selected(endpoint, { credentials, headers: { "x-private": header } });
    const generated = await model.generate(input);
    const events = await collect(model.stream(input));
    assert.ok(generated.text === text, "Generation content must remain unchanged");
    assert.ok(events.filter(event => event.type === "text_delta").map(event => event.text).join("") === text, "Streaming content must remain unchanged");
    assert.deepEqual(events.at(-1).response, generated);
  }
});

test("malformed SSE/JSON and in-band provider errors cannot expose credentials", async t => {
  let payload;
  const { endpoint } = await server(t, (_request, response) => { response.writeHead(200, { ...headers, "x-request-id": secret }); response.end(payload); });
  for (const [body, expected] of [[`data: {${secret}\n\n`, "ProtocolError"], [`data: ${secret}\n`, "ProtocolError"], [sse({ error: { message: secret, code: headerSecret } }), "ProviderError"]]) {
    payload = body;
    await assert.rejects(collect(selected(endpoint, { credentials: secret, headers: { "x-secret": headerSecret } }).stream(input)), error => { noSecrets(error); return category(expected)(error); });
  }
});

test("the streaming example runs with the local mock provider", async t => {
  const { endpoint } = await server(t, (_request, response) => { response.writeHead(200, headers); response.end(sse(chunk("Hello!")) + ending); });
  const { stdout, stderr } = await promisify(execFile)(process.execPath, [new URL("../../examples/typescript/stream.mjs", import.meta.url).pathname], {
    env: { ...process.env, CONDUIT_ENDPOINT: endpoint, CONDUIT_MODEL: "example-model", CONDUIT_API_KEY: secret },
  });
  noSecrets(stdout); noSecrets(stderr);
  assert.equal(stdout, "Hello!");
  assert.equal(stderr, "");
});
