import { server as httpServer } from "./http.mjs";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { inspect } from "node:util";
import { promisify } from "node:util";
import { execFile } from "node:child_process";
import { test } from "node:test";
import { setTimeout as delay } from "node:timers/promises";
import { connect, ConduitError } from "../dist/index.js";

const fixture = async path => JSON.parse(await readFile(new URL(`../../conformance/${path}`, import.meta.url), "utf8"));
const requestFixture = await fixture("requests/openai-text.json");
const responseFixtures = await fixture("responses/openai-text.json");
const errorFixtures = await fixture("errors/openai-http.json");
const good = responseFixtures[0].input.wire.body;
const input = { messages: [{ role: "user", content: "Hello" }] };
// Synthetic values stay out of assertion output, even if a regression leaks them.
const secret = "synthetic-bearer-47";
const headerSecret = "synthetic-header-83";
const isError = name => error => error instanceof ConduitError && error.name === name;
const noSecrets = value => {
  for (const representation of [JSON.stringify(value), String(value), inspect(value, { showHidden: true, depth: 10 })]) {
    assert.ok(!representation?.includes(secret), "Bearer credential leaked");
    assert.ok(!representation?.includes(headerSecret), "Custom header value leaked");
  }
};

const server = (t, handle = (_request, response) => response.end(JSON.stringify(good))) => httpServer(t, handle);

function model(endpoint, extra = {}) {
  return connect({ driver: "openai-compatible", endpoint, model: "test-model", ...extra });
}

test("construction is local; both API forms, text parts, parameters, auth, and native options", async t => {
  const { endpoint, requests } = await server(t);
  const config = { driver: "openai-compatible", endpoint, credentials: secret, headers: { "x-private": headerSecret } };
  const client = connect(config);
  const selected = client.model("test-model");
  const shorthand = connect({ ...config, model: "test-model" });
  await delay(10);
  assert.equal(requests.length, 0);
  noSecrets(client);
  noSecrets(selected);
  noSecrets(shorthand);
  // Configuration mutation must not change the captured connection.
  config.endpoint = "http://127.0.0.1:1";
  config.credentials = "changed";
  config.headers["x-private"] = "changed";
  const r = requestFixture.input.request;
  const response = await selected.generate({ messages: r.messages, maxOutputTokens: r.max_output_tokens, temperature: r.temperature, topP: r.top_p, stop: r.stop, providerOptions: r.provider_options });
  const wire = requests[0];
  const expected = requestFixture.expected.wire_request;
  assert.equal(wire.method, expected.method);
  assert.equal(wire.path, expected.path);
  assert.deepEqual(wire.body, expected.body);
  assert.ok(wire.headers.authorization === `Bearer ${secret}`, "Expected bearer authorization");
  assert.ok(wire.headers["x-private"] === headerSecret, "Expected custom header");
  assert.equal(wire.headers["content-type"], "application/json");
  assert.equal(response.text, "Hello!");
  await shorthand.generate(input);
  assert.equal(requests.length, 2);
});

test("API base paths and trailing slashes are preserved predictably; credentials are optional", async t => {
  const { endpoint, requests } = await server(t);
  for (const [suffix, path] of [["", "/v1/chat/completions"], ["/", "/v1/chat/completions"], ["///", "/v1/chat/completions"], ["/custom", "/v1/custom/chat/completions"]]) {
    await model(endpoint + suffix).generate(input);
    assert.equal(requests.at(-1).path, path);
    assert.equal(requests.at(-1).headers.authorization, undefined);
  }
  await model(new URL(endpoint).origin).generate(input);
  assert.equal(requests.at(-1).path, "/chat/completions");
});

for (const f of responseFixtures) test(`shared response: ${f.id}`, async t => {
  const wire = f.input.wire;
  const { endpoint } = await server(t, (_request, response) => { response.writeHead(wire.status, wire.headers); response.end(JSON.stringify(wire.body)); });
  const result = await model(endpoint).generate(input);
  const expected = f.expected.response;
  assert.deepEqual(result.content, expected.content);
  assert.equal(result.id, expected.id);
  assert.equal(result.model, expected.model);
  assert.equal(result.finishReason, expected.finish_reason);
  assert.deepEqual(result.providerMetadata, { finishReason: expected.provider_metadata.finish_reason, requestId: expected.provider_metadata.request_id });
  assert.equal(result.text, result.content.map(part => part.text).join(""));
  if (expected.usage) {
    const usage = Object.fromEntries([["inputTokens", expected.usage.input_tokens], ["outputTokens", expected.usage.output_tokens], ["totalTokens", expected.usage.total_tokens]].filter(([, value]) => value !== undefined));
    assert.deepEqual(result.usage, usage);
  } else assert.ok(!Object.hasOwn(result, "usage"));
  result.content.push({ type: "text", text: "more" });
  assert.ok(result.text.endsWith("more"), "text must remain a view of content");
});

for (const f of errorFixtures) test(`shared HTTP error: ${f.id}`, async t => {
  const wire = f.input.wire;
  const { endpoint, requests } = await server(t, (_request, response) => { response.writeHead(wire.status, wire.headers); response.end(wire.body_text ?? JSON.stringify(wire.body)); });
  await assert.rejects(model(endpoint).generate(input), error => {
    const expected = f.expected.error;
    assert.ok(error instanceof ConduitError);
    assert.equal(error.name, expected.category);
    assert.equal(error.message, expected.message);
    assert.equal(error.statusCode, expected.status_code);
    assert.equal(error.providerCode, expected.provider_code);
    assert.equal(error.requestId, expected.request_id);
    assert.deepEqual(error.providerDetails, expected.provider_details);
    return true;
  });
  assert.equal(requests.length, 1, "No automatic retries");
});

test("all owned native fields are rejected, even when omitted or equal", async t => {
  const { endpoint, requests } = await server(t);
  for (const field of ["model", "messages", "stream", "stream_options", "max_tokens", "max_completion_tokens", "temperature", "top_p", "stop", "tools", "tool_choice", "functions", "function_call", "response_format", "n", "modalities"]) {
    await assert.rejects(model(endpoint).generate({ ...input, providerOptions: { [field]: field === "stream" ? true : 1 } }), isError("InvalidRequestError"));
  }
  await assert.rejects(model(endpoint).generate({ ...input, temperature: 1, providerOptions: { temperature: 1 } }), isError("InvalidRequestError"));
  assert.equal(requests.length, 0);
});

test("input validation and unsupported features fail before I/O", async t => {
  const { endpoint, requests } = await server(t);
  const cycle = {}; cycle.self = cycle;
  for (const change of [
    { messages: [] }, { messages: [null] }, { messages: new Array(1) }, { messages: [{ role: "developer", content: "hello" }] },
    { messages: [{ role: "user", content: [{ type: "text", text: 42 }] }] },
    { maxOutputTokens: 0 }, { maxOutputTokens: 1.5 }, { maxOutputTokens: Number.MAX_SAFE_INTEGER + 1 },
    { temperature: NaN }, { temperature: -1 }, { temperature: 3 }, { topP: Infinity }, { topP: 2 },
    { stop: "stop" }, { stop: new Array(1) }, { timeout: null }, { timeout: 0 }, { timeout: 1.5 }, { timeout: 2147483648 },
    { signal: {} }, { typo: 1 }, { providerOptions: null }, { providerOptions: [] }, { providerOptions: cycle },
    { providerOptions: { x: undefined } }, { providerOptions: { x: () => 1 } }, { providerOptions: { x: 1n } },
    { providerOptions: { x: Infinity } }, { providerOptions: { x: new Date() } }, { providerOptions: { x: new Array(1) } },
  ]) await assert.rejects(model(endpoint).generate({ ...input, ...change }), isError("InvalidRequestError"));
  for (const change of [{ responseFormat: { type: "json" } }, { stream: true }, { messages: [{ role: "user", content: [{ type: "image", url: "https://invalid.example" }] }] }]) {
    await assert.rejects(model(endpoint).generate({ ...input, ...change }), isError("UnsupportedCapabilityError"));
  }
  // tools: [] is now InvalidRequestError (empty array), tool message with string content is InvalidRequestError
  await assert.rejects(model(endpoint).generate({ ...input, tools: [] }), isError("InvalidRequestError"));
  await assert.rejects(model(endpoint).generate({ ...input, messages: [{ role: "tool", content: "hello" }] }), isError("InvalidRequestError"));
  assert.equal(requests.length, 0);
});

test("invalid configuration and case-insensitive protected headers never echo input", () => {
  for (const endpoint of ["invalid", `http://${secret}@localhost/v1`, `http://localhost/v1?key=${secret}`, `http://localhost/v1#${secret}`, "ftp://localhost/v1", "http://localhost/v1?", "http://localhost/v1#"]) {
    assert.throws(() => model(endpoint), error => { noSecrets(error); return isError("InvalidRequestError")(error); });
  }
  for (const headers of [{ Authorization: secret }, { "aUtHoRiZaTiOn": secret }, { "Proxy-Authorization": secret }, { Cookie: secret }, { Host: "other" }, { "Content-Type": "text/plain" }, { "Content-Length": "0" }, { Connection: "close" }, { "bad\nheader": secret }]) {
    assert.throws(() => model("http://localhost/v1", { credentials: secret, headers }), error => { noSecrets(error); return isError("InvalidRequestError")(error); });
  }
  for (const config of [{ driver: "unknown" }, { model: "" }, { model: 1 }, { timeout: 0 }, { credentials: "" }, { credentials: `${secret}\n` }, { typo: secret }]) {
    assert.throws(() => model("http://localhost/v1", config), isError("InvalidRequestError"));
  }
});

test("malformed successful payloads and unhandled content fail explicitly", async t => {
  let body;
  const { endpoint } = await server(t, (_request, response) => response.end(body));
  const malformed = [null, {}, { choices: [] }, { ...good, choices: [...good.choices, ...good.choices] }, { ...good, id: 42 }, { ...good, usage: null }, { ...good, usage: { prompt_tokens: -1 } }, { ...good, usage: { total_tokens: "6" } }];
  for (const message of [{ role: "assistant", content: null }, { role: "user", content: "Hi" }, { role: "assistant", content: [] }, { role: "assistant", content: "Hi", tool_calls: [{ id: "call" }] }, { role: "assistant", content: "Hi", reasoning_content: "hidden" }]) malformed.push({ ...good, choices: [{ message, finish_reason: "stop" }] });
  malformed.push({ ...good, choices: [{ message: good.choices[0].message, finish_reason: null }] });
  for (const value of malformed) {
    body = JSON.stringify(value);
    await assert.rejects(model(endpoint).generate(input), isError("ProtocolError"));
  }
  body = "{bad json";
  await assert.rejects(model(endpoint).generate(input), isError("ProtocolError"));
});

test("omitted IDs, empty text, and optional sampling values are preserved", async t => {
  const { endpoint, requests } = await server(t, (_request, response) => response.end(JSON.stringify({ choices: [{ message: { role: "assistant", content: "" }, finish_reason: "stop" }] })));
  const result = await model(endpoint).generate({ messages: [{ role: "user", content: "" }], temperature: 0, topP: 0, maxOutputTokens: 1, stop: [] });
  assert.equal(result.text, "");
  assert.ok(!Object.hasOwn(result, "id") && !Object.hasOwn(result, "model"));
  assert.equal(requests[0].body.temperature, 0);
  assert.equal(requests[0].body.top_p, 0);
  assert.deepEqual(requests[0].body.messages[0].content, [{ type: "text", text: "" }]);
});

test("provider diagnostics redact credentials and custom header values", async t => {
  let failure = true;
  const { endpoint } = await server(t, (_request, response) => {
    response.writeHead(failure ? 401 : 200, { "x-request-id": secret });
    response.end(JSON.stringify(failure ? { error: { message: `Denied ${secret} ${headerSecret}`, type: headerSecret, code: secret, raw_request: { authorization: secret } } } : { ...good, id: secret, model: secret, choices: [{ message: { role: "assistant", content: `Echo ${secret} ${headerSecret}` }, finish_reason: secret }] }));
  });
  const selected = model(endpoint, { credentials: secret, headers: { "x-private": ` ${headerSecret} ` } });
  await assert.rejects(selected.generate(input), error => { noSecrets(error); noSecrets(error.stack); return isError("AuthenticationError")(error); });
  failure = false;
  const response = await selected.generate(input);
  noSecrets(response.providerMetadata);
  assert.ok(response.text === `Echo ${secret} ${headerSecret}`, "Semantic content must remain unchanged");
});

test("timeouts cover response reads and client defaults can be overridden", async t => {
  const { endpoint } = await server(t, (_request, response) => { response.writeHead(200); response.write('{"choices":'); });
  const selected = model(endpoint, { credentials: secret, timeout: 5000 });
  await assert.rejects(selected.generate({ ...input, timeout: 30 }), error => { noSecrets(error); return isError("TimeoutError")(error); });
  await assert.rejects(model(endpoint, { timeout: 30 }).generate(input), isError("TimeoutError"));
});

test("caller cancellation ignores arbitrary abort reasons and pre-aborted calls do no I/O", async t => {
  const { endpoint, requests } = await server(t, () => {});
  const controller = new AbortController();
  const pending = model(endpoint, { credentials: secret }).generate({ ...input, signal: controller.signal, timeout: 5000 });
  controller.abort(new DOMException(secret, "TimeoutError"));
  await assert.rejects(pending, error => { noSecrets(error); return isError("CancelledError")(error); });
  const before = requests.length;
  await assert.rejects(model(endpoint).generate({ ...input, signal: controller.signal, timeout: 1 }), isError("CancelledError"));
  assert.equal(requests.length, before);
});

test("cancellation during body reads and timeout-first races retain their category", async t => {
  let onRequest;
  const seen = new Promise(resolve => { onRequest = resolve; });
  const { endpoint } = await server(t, (_request, response) => { response.writeHead(200); response.write("{"); onRequest(); });
  const caller = new AbortController();
  const pending = model(endpoint).generate({ ...input, signal: caller.signal });
  await seen;
  caller.abort(secret);
  await assert.rejects(pending, isError("CancelledError"));
  const later = new AbortController();
  const timed = model(endpoint).generate({ ...input, signal: later.signal, timeout: 10 });
  await assert.rejects(timed, isError("TimeoutError"));
  later.abort(secret);
});

test("redirects do not forward credentials or generate a second request", async t => {
  const target = await server(t);
  const source = await server(t, (_request, response) => { response.writeHead(307, { location: `${target.endpoint}/chat/completions` }); response.end(); });
  await assert.rejects(model(source.endpoint, { credentials: secret }).generate(input), isError("ProviderError"));
  assert.equal(source.requests.length, 1);
  assert.equal(target.requests.length, 0);
});

test("connection refusal is normalized with a safe native cause", async () => {
  const temporary = createServer();
  await new Promise(resolve => temporary.listen(0, "127.0.0.1", resolve));
  const port = temporary.address().port;
  await new Promise(resolve => temporary.close(resolve));
  await assert.rejects(model(`http://127.0.0.1:${port}/v1`, { credentials: secret }).generate(input), error => {
    noSecrets(error);
    assert.equal(error.cause?.code, "ECONNREFUSED");
    return isError("ConnectionError")(error);
  });
});

test("the documented example runs against a local compatible endpoint", async t => {
  const { endpoint, requests } = await server(t);
  const { stdout, stderr } = await promisify(execFile)(process.execPath, [new URL("../../examples/typescript/generate.mjs", import.meta.url).pathname], {
    env: { ...process.env, CONDUIT_ENDPOINT: endpoint, CONDUIT_MODEL: "example-model", CONDUIT_API_KEY: secret },
  });
  noSecrets(stdout);
  noSecrets(stderr);
  assert.equal(stdout.trim(), "Hello!");
  assert.equal(stderr, "");
  assert.equal(requests[0].body.model, "example-model");
});
