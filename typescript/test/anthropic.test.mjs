import assert from "node:assert/strict";
import { test } from "node:test";
import { connect, ConduitError } from "../dist/index.js";

const secret = "synthetic-anthropic-key";

function jsonResponse(status, body, headers = {}) {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json", ...headers } });
}

function anthropicMsg(body) {
  return {
    id: "msg_test",
    type: "message",
    role: "assistant",
    model: "claude-sonnet-4-20250514",
    content: body.content,
    stop_reason: body.stop_reason ?? "end_turn",
    stop_sequence: body.stop_sequence ?? null,
    usage: body.usage ?? { input_tokens: 4, output_tokens: 2 },
  };
}

const withTokens = (req) => ({ maxOutputTokens: 32, ...req });

test("anthropic: endpoint and headers", async () => {
  let seenUrl = null, seenHeaders = null;
  globalThis.fetch = async (url, init) => {
    seenUrl = url; seenHeaders = init.headers;
    return jsonResponse(200, anthropicMsg({ content: [{ type: "text", text: "hi" }] }));
  };
  const client = connect({ driver: "anthropic", endpoint: "https://api.anthropic.com", credentials: secret, model: "claude-sonnet-4-20250514" });
  await client.generate(withTokens({ messages: [{ role: "user", content: "hello" }] }));
  assert.ok(seenUrl.includes("/v1/messages"), `url ${seenUrl} should contain /v1/messages`);
  const h = new Headers(seenHeaders);
  assert.equal(h.get("x-api-key"), secret);
  assert.equal(h.get("anthropic-version"), "2023-06-01");
  assert.equal(h.get("content-type"), "application/json");
  // custom base path preserved
  seenUrl = null;
  const client2 = connect({ driver: "anthropic", endpoint: "https://example.com/custom", credentials: secret, model: "m" });
  globalThis.fetch = async (url, init) => { seenUrl = url; return jsonResponse(200, anthropicMsg({ content: [{ type: "text", text: "hi" }] })); };
  await client2.generate(withTokens({ messages: [{ role: "user", content: "hi" }] }));
  assert.ok(seenUrl.includes("/custom/v1/messages"), `custom base path not preserved: ${seenUrl}`);
  globalThis.fetch = undefined;
});

test("anthropic: system mapping", async () => {
  let seen = null;
  globalThis.fetch = async (url, init) => { seen = JSON.parse(init.body); return jsonResponse(200, anthropicMsg({ content: [{ type: "text", text: "ok" }] })); };
  const m = connect({ driver: "anthropic", endpoint: "https://api.anthropic.com", model: "claude-sonnet-4-20250514" });
  await m.generate(withTokens({ messages: [{ role: "system", content: "Be concise." }, { role: "user", content: "hi" }] }));
  assert.equal(seen.system, "Be concise.");
  assert.equal(seen.messages.length, 1);
  assert.equal(seen.messages[0].role, "user");
  // multiple system messages -> array
  await m.generate(withTokens({ messages: [{ role: "system", content: "A" }, { role: "system", content: "B" }, { role: "user", content: "hi" }] }));
  assert.ok(Array.isArray(seen.system));
  assert.equal(seen.system.length, 2);
  // system not included in messages
  assert.ok(!seen.messages.some(v => v.role === "system"));
  globalThis.fetch = undefined;
});

test("anthropic: request mapping tools and toolChoice", async () => {
  const tool = { name: "get_weather", description: "Get weather", inputSchema: { type: "object", properties: { location: { type: "string" } }, required: ["location"] } };
  let seen = null;
  globalThis.fetch = async (url, init) => { seen = JSON.parse(init.body); return jsonResponse(200, anthropicMsg({ content: [{ type: "text", text: "ok" }] })); };
  const m = connect({ driver: "anthropic", endpoint: "https://api.anthropic.com", model: "claude-sonnet-4-20250514" });
  await m.generate(withTokens({ messages: [{ role: "user", content: "hi" }], tools: [tool], toolChoice: "auto" }));
  assert.deepEqual(seen.tools, [{ name: "get_weather", description: "Get weather", input_schema: tool.inputSchema }]);
  assert.deepEqual(seen.tool_choice, { type: "auto" });
  await m.generate(withTokens({ messages: [{ role: "user", content: "hi" }], tools: [tool], toolChoice: "none" }));
  assert.deepEqual(seen.tool_choice, { type: "none" });
  await m.generate(withTokens({ messages: [{ role: "user", content: "hi" }], tools: [tool], toolChoice: "required" }));
  assert.deepEqual(seen.tool_choice, { type: "any" });
  await m.generate(withTokens({ messages: [{ role: "user", content: "hi" }], tools: [tool], toolChoice: { name: "get_weather" } }));
  assert.deepEqual(seen.tool_choice, { type: "tool", name: "get_weather" });
  await m.generate(withTokens({ messages: [{ role: "user", content: "hi" }], tools: [tool], toolChoice: "get_weather" }));
  assert.deepEqual(seen.tool_choice, { type: "tool", name: "get_weather" });
  globalThis.fetch = undefined;
});

test("anthropic: responseFormat rejected", async () => {
  const m = connect({ driver: "anthropic", endpoint: "https://api.anthropic.com", model: "claude-sonnet-4-20250514" });
  for (const fmt of [{ type: "json" }, { type: "json_schema", schema: { type: "object" } }, { type: "text" }]) {
    await assert.rejects(() => m.generate(withTokens({ messages: [{ role: "user", content: "hi" }], responseFormat: fmt })), e => e.name === "UnsupportedCapabilityError");
  }
});

test("anthropic: tool result mapping", async () => {
  let seen = null;
  globalThis.fetch = async (url, init) => { seen = JSON.parse(init.body); return jsonResponse(200, anthropicMsg({ content: [{ type: "text", text: "sunny" }] })); };
  const m = connect({ driver: "anthropic", endpoint: "https://api.anthropic.com", model: "claude-sonnet-4-20250514" });
  const tool = { name: "get_weather", inputSchema: { type: "object", properties: { location: { type: "string" } } } };
  await m.generate(withTokens({
    messages: [
      { role: "user", content: "weather?" },
      { role: "assistant", content: [{ type: "tool_call", id: "toolu_01", name: "get_weather", arguments: { location: "Paris" } }] },
      { role: "tool", content: [{ type: "tool_result", callId: "toolu_01", content: "sunny" }] },
    ],
    tools: [tool],
  }));
  // Assistant tool_use mapped
  const asst = seen.messages.find(v => v.role === "assistant");
  assert.equal(asst.content[0].type, "tool_use");
  assert.equal(asst.content[0].id, "toolu_01");
  assert.deepEqual(asst.content[0].input, { location: "Paris" });
  // Tool result mapped to user with tool_result
  const toolMsg = seen.messages.find(v => v.role === "user" && Array.isArray(v.content) && v.content.some(c => c.type === "tool_result"));
  assert.ok(toolMsg);
  assert.equal(toolMsg.content[0].tool_use_id, "toolu_01");
  assert.equal(toolMsg.content[0].content, "sunny");
  globalThis.fetch = undefined;
});

test("anthropic: non-streaming response mapping", async () => {
  const m = connect({ driver: "anthropic", endpoint: "https://api.anthropic.com", model: "claude-sonnet-4-20250514" });
  globalThis.fetch = async () => jsonResponse(200, anthropicMsg({ content: [{ type: "text", text: "Hello" }], stop_reason: "end_turn", usage: { input_tokens: 10, output_tokens: 5 } }), { "request-id": "req_1" });
  const r = await m.generate(withTokens({ messages: [{ role: "user", content: "hi" }] }));
  assert.equal(r.text, "Hello");
  assert.equal(r.finishReason, "stop");
  assert.equal(r.providerMetadata.finishReason, "end_turn");
  assert.equal(r.providerMetadata.requestId, "req_1");
  assert.equal(r.usage.inputTokens, 10);
  assert.equal(r.usage.outputTokens, 5);

  globalThis.fetch = async () => jsonResponse(200, anthropicMsg({ content: [{ type: "tool_use", id: "toolu_01", name: "get_weather", input: { location: "Paris" } }], stop_reason: "tool_use" }));
  const r2 = await m.generate(withTokens({ messages: [{ role: "user", content: "hi" }] }));
  assert.equal(r2.finishReason, "tool_call");
  assert.equal(r2.toolCalls[0].name, "get_weather");
  assert.deepEqual(r2.toolCalls[0].arguments, { location: "Paris" });

  globalThis.fetch = async () => jsonResponse(200, anthropicMsg({ content: [{ type: "text", text: "a" }], stop_reason: "max_tokens" }));
  const r3 = await m.generate(withTokens({ messages: [{ role: "user", content: "hi" }] }));
  assert.equal(r3.finishReason, "length");

  globalThis.fetch = async () => jsonResponse(200, anthropicMsg({ content: [{ type: "text", text: "" }], stop_reason: "refusal" }));
  const r4 = await m.generate(withTokens({ messages: [{ role: "user", content: "hi" }] }));
  assert.equal(r4.finishReason, "content_filter");

  globalThis.fetch = undefined;
});

test("anthropic: streaming text and tool_use with split JSON", async () => {
  const encoder = new TextEncoder();
  const payload = [
    `event: message_start\ndata: {"type":"message_start","message":{"id":"msg_123","type":"message","role":"assistant","content":[],"model":"claude-sonnet-4-20250514","stop_reason":null,"stop_sequence":null,"usage":{"input_tokens":4,"output_tokens":1}}}\n\n`,
    `event: content_block_start\ndata: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}\n\n`,
    `event: content_block_delta\ndata: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hello "}}\n\n`,
    `event: content_block_delta\ndata: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"world"}}\n\n`,
    `event: content_block_stop\ndata: {"type":"content_block_stop","index":0}\n\n`,
    `event: message_delta\ndata: {"type":"message_delta","delta":{"stop_reason":"end_turn","stop_sequence":null},"usage":{"output_tokens":2}}\n\n`,
    `event: message_stop\ndata: {"type":"message_stop"}\n\n`,
  ].join("");
  globalThis.fetch = async () => new Response(new ReadableStream({ start(c){ c.enqueue(encoder.encode(payload)); c.close(); }}), { status:200, headers:{ "content-type":"text/event-stream"}});
  const m = connect({ driver: "anthropic", endpoint: "https://api.anthropic.com", model: "claude-sonnet-4-20250514" });
  const events = [];
  for await (const e of m.stream(withTokens({ messages: [{ role: "user", content: "hi" }] }))) events.push(e);
  assert.equal(events[0].type, "start");
  const textDeltas = events.filter(e=>e.type==="text_delta");
  assert.equal(textDeltas.length, 2);
  assert.equal(textDeltas[0].text, "Hello ");
  assert.equal(textDeltas[1].text, "world");
  const done = events.find(e=>e.type==="done");
  assert.equal(done.response.text, "Hello world");
  assert.equal(done.response.finishReason, "stop");

  // tool streaming with partial JSON split across reads (byte fragmentation)
  const toolPayload = [
    `event: message_start\ndata: {"type":"message_start","message":{"id":"msg_tool","type":"message","role":"assistant","content":[],"model":"claude-sonnet-4-20250514","stop_reason":null,"stop_sequence":null,"usage":{"input_tokens":10,"output_tokens":1}}}\n\n`,
    `event: content_block_start\ndata: {"type":"content_block_start","index":0,"content_block":{"type":"tool_use","id":"toolu_01","name":"get_weather","input":{}}}\n\n`,
    `event: content_block_delta\ndata: {"type":"content_block_delta","index":0,"delta":{"type":"input_json_delta","partial_json":"{\\"location\\": \\"Pa"}}\n\n`,
    `event: content_block_delta\ndata: {"type":"content_block_delta","index":0,"delta":{"type":"input_json_delta","partial_json":"ris\\"}"}}\n\n`,
    `event: content_block_stop\ndata: {"type":"content_block_stop","index":0}\n\n`,
    `event: message_delta\ndata: {"type":"message_delta","delta":{"stop_reason":"tool_use","stop_sequence":null},"usage":{"output_tokens":5}}\n\n`,
    `event: message_stop\ndata: {"type":"message_stop"}\n\n`,
  ].join("");
  const bytes = encoder.encode(toolPayload);
  // fragment into 7-byte chunks to test arbitrary boundaries and UTF-8 handling
  const frags = [];
  for(let i=0;i<bytes.length;i+=7) frags.push(bytes.slice(i,i+7));
  let idx=0;
  globalThis.fetch = async () => new Response(new ReadableStream({ async pull(c){ if(idx < frags.length) c.enqueue(frags[idx++]); else c.close(); }}), {status:200, headers:{"content-type":"text/event-stream"}});
  const events2 = [];
  for await (const e of m.stream(withTokens({ messages: [{ role: "user", content: "hi" }] }))) events2.push(e);
  const toolDeltas = events2.filter(e=>e.type==="tool_call_delta");
  assert.ok(toolDeltas.some(d=>d.argumentsDelta==='{"location": "Pa'));
  assert.ok(toolDeltas.some(d=>d.argumentsDelta==='ris"}'));
  const done2 = events2.find(e=>e.type==="done");
  assert.equal(done2.response.toolCalls[0].name, "get_weather");
  assert.deepEqual(done2.response.toolCalls[0].arguments, { location: "Paris" });
  globalThis.fetch = undefined;
});

test("anthropic: listModels", async () => {
  globalThis.fetch = async (url, init) => {
    assert.ok(String(url).includes("/v1/models"));
    assert.equal(init.method, "GET");
    return jsonResponse(200, { data: [{ id: "claude-sonnet-4-20250514", type: "model" }], has_more: false });
  };
  const c = connect({ driver: "anthropic", endpoint: "https://api.anthropic.com", credentials: secret });
  const models = await c.listModels();
  assert.equal(models.length, 1);
  assert.equal(models[0].id, "claude-sonnet-4-20250514");
  globalThis.fetch = undefined;
});

test("anthropic: providerOptions collisions rejected", async () => {
  const m = connect({ driver: "anthropic", endpoint: "https://api.anthropic.com", model: "m" });
  await assert.rejects(() => m.generate(withTokens({ messages: [{ role: "user", content: "hi" }], providerOptions: { model: "other" } })), e=>e.name==="InvalidRequestError");
  await assert.rejects(() => m.generate(withTokens({ messages: [{ role: "user", content: "hi" }], providerOptions: { messages: [] } })), e=>e.name==="InvalidRequestError");
  await assert.rejects(() => m.generate(withTokens({ messages: [{ role: "user", content: "hi" }], providerOptions: { max_tokens: 10 } })), e=>e.name==="InvalidRequestError");
  // custom headers must not override auth
  assert.throws(() => connect({ driver: "anthropic", endpoint: "https://api.anthropic.com", credentials: secret, headers: { "x-api-key": "other" } }), e=>e.name==="InvalidRequestError");
  assert.throws(() => connect({ driver: "anthropic", endpoint: "https://api.anthropic.com", credentials: secret, headers: { "anthropic-version": "bad" } }), e=>e.name==="InvalidRequestError");
});

test("anthropic: errors and redaction", async () => {
  const m = connect({ driver: "anthropic", endpoint: "https://api.anthropic.com", model: "m", credentials: secret });
  globalThis.fetch = async () => jsonResponse(401, { type: "error", error: { type: "authentication_error", message: `bad ${secret}` } });
  await assert.rejects(() => m.generate(withTokens({ messages: [{ role: "user", content: "hi" }] })), e => {
    assert.equal(e.name, "AuthenticationError");
    assert.ok(!String(e.message).includes(secret));
    return true;
  });
  // streaming in-band error
  const encoder = new TextEncoder();
  const errPayload = `event: error\ndata: {"type":"error","error":{"type":"overloaded_error","message":"Overloaded"}}\n\n`;
  globalThis.fetch = async () => new Response(new ReadableStream({ start(c){ c.enqueue(encoder.encode(errPayload)); c.close(); }}), {status:200, headers:{"content-type":"text/event-stream"}});
  await assert.rejects(async ()=>{ for await(const e of m.stream(withTokens({ messages:[{role:"user", content:"hi"}]}))) {} }, e=> e.name==="ProviderError" || e.name==="ProtocolError");
  globalThis.fetch = undefined;
});

test("anthropic: CRLF and ping handling", async () => {
  const encoder = new TextEncoder();
  const payload = [
    `event: message_start\r\ndata: {"type":"message_start","message":{"id":"msg_1","type":"message","role":"assistant","content":[],"model":"m","stop_reason":null,"stop_sequence":null,"usage":{"input_tokens":1,"output_tokens":1}}}\r\n\r\n`,
    `event: ping\r\ndata: {"type":"ping"}\r\n\r\n`,
    `event: content_block_start\r\ndata: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}\r\n\r\n`,
    `event: content_block_delta\r\ndata: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"hi"}}\r\n\r\n`,
    `event: content_block_stop\r\ndata: {"type":"content_block_stop","index":0}\r\n\r\n`,
    `event: message_delta\r\ndata: {"type":"message_delta","delta":{"stop_reason":"end_turn","stop_sequence":null},"usage":{"output_tokens":2}}\r\n\r\n`,
    `event: message_stop\r\ndata: {"type":"message_stop"}\r\n\r\n`,
  ].join("");
  globalThis.fetch = async () => new Response(new ReadableStream({ start(c){ c.enqueue(encoder.encode(payload)); c.close(); }}), {status:200, headers:{"content-type":"text/event-stream"}});
  const m = connect({ driver: "anthropic", endpoint: "https://api.anthropic.com", model: "m" });
  const events=[];
  for await(const e of m.stream(withTokens({ messages:[{role:"user", content:"hi"}]}))) events.push(e);
  assert.ok(events.some(e=>e.type==="text_delta" && e.text==="hi"));
  assert.ok(events.some(e=>e.type==="done"));
  globalThis.fetch = undefined;
});

test("anthropic: maxOutputTokens required", async () => {
  const m = connect({ driver: "anthropic", endpoint: "https://api.anthropic.com", model: "claude-sonnet-4-20250514" });
  await assert.rejects(() => m.generate({ messages: [{ role: "user", content: "hi" }] }), e => e.name === "InvalidRequestError");
  await assert.rejects(async () => { for await (const _ of m.stream({ messages: [{ role: "user", content: "hi" }] })) {} }, e => e.name === "InvalidRequestError");
  // with tokens succeeds (no I/O beyond mock)
  globalThis.fetch = async () => jsonResponse(200, anthropicMsg({ content: [{ type: "text", text: "ok" }] }));
  const r = await m.generate(withTokens({ messages: [{ role: "user", content: "hi" }] }));
  assert.equal(r.text, "ok");
  // providerOptions must not supply max_tokens via escape hatch either (collision)
  await assert.rejects(() => m.generate({ messages: [{ role: "user", content: "hi" }], providerOptions: { max_tokens: 100 } }), e => e.name === "InvalidRequestError");
  globalThis.fetch = undefined;
});

test("anthropic: tool_result requires callId and tool_call requires id", async () => {
  const m = connect({ driver: "anthropic", endpoint: "https://api.anthropic.com", model: "claude-sonnet-4-20250514" });
  // tool_result without callId -> InvalidRequestError before I/O, even if name present
  await assert.rejects(() => m.generate(withTokens({
    messages: [
      { role: "user", content: "hi" },
      { role: "assistant", content: [{ type: "tool_call", id: "toolu_01", name: "get_weather", arguments: {} }] },
      { role: "tool", content: [{ type: "tool_result", name: "get_weather", content: "sunny" }] },
    ],
    tools: [{ name: "get_weather", inputSchema: {} }],
  })), e => e.name === "InvalidRequestError");
  await assert.rejects(() => m.generate(withTokens({
    messages: [
      { role: "user", content: "hi" },
      { role: "assistant", content: [{ type: "tool_call", id: "toolu_01", name: "get_weather", arguments: {} }] },
      { role: "tool", content: [{ type: "tool_result", content: "sunny" }] },
    ],
    tools: [{ name: "get_weather", inputSchema: {} }],
  })), e => e.name === "InvalidRequestError");

  // assistant tool_call without id -> InvalidRequestError (no fabrication)
  await assert.rejects(() => m.generate(withTokens({
    messages: [{ role: "assistant", content: [{ type: "tool_call", name: "get_weather", arguments: {} }] }],
    tools: [{ name: "get_weather", inputSchema: {} }],
  })), e => e.name === "InvalidRequestError");

  // openai/ollama still allow missing id (no regression)
  globalThis.fetch = async () => jsonResponse(200, { id: "1", model: "m", choices: [{ message: { role: "assistant", content: null, tool_calls: [{ id: "call_1", type: "function", function: { name: "get_weather", arguments: "{}" } }] }, finish_reason: "tool_calls" }] });
  const oai = connect({ driver: "openai-compatible", endpoint: "http://localhost:1234/v1", model: "m" });
  const r2 = await oai.generate({ messages: [{ role: "assistant", content: [{ type: "tool_call", name: "get_weather", arguments: {} }] }] });
  assert.equal(r2.toolCalls[0].name, "get_weather");
  globalThis.fetch = undefined;
});

test("anthropic: listModels pagination follows has_more via last_id", async () => {
  const calls = [];
  globalThis.fetch = async (url, init) => {
    calls.push(String(url));
    const u = new URL(String(url));
    const after = u.searchParams.get("after_id");
    if (!after) {
      return jsonResponse(200, { data: [{ id: "claude-1", type: "model" }, { id: "claude-2", type: "model" }], has_more: true, last_id: "claude-2", first_id: "claude-1" });
    }
    if (after === "claude-2") {
      return jsonResponse(200, { data: [{ id: "claude-3", type: "model", display_name: "Claude 3", created_at: "2025-01-01", capabilities: { batch: { supported: true } } }], has_more: false, first_id: "claude-3", last_id: "claude-3" });
    }
    throw new Error("unexpected cursor " + after);
  };
  const c = connect({ driver: "anthropic", endpoint: "https://api.anthropic.com", credentials: secret });
  const models = await c.listModels();
  assert.equal(models.length, 3);
  assert.deepEqual(models.map(m => m.id), ["claude-1", "claude-2", "claude-3"]);
  // display_name -> name mapping and metadata preserved
  assert.equal(models[2].name, "Claude 3");
  assert.equal(models[2].providerMetadata.type, "model");
  assert.equal(models[2].providerMetadata.created_at, "2025-01-01");
  assert.deepEqual(models[2].providerMetadata.capabilities, { batch: { supported: true } });
  assert.equal(calls.length, 2);
  assert.ok(calls[0].endsWith("/v1/models"));
  assert.ok(calls[1].includes("after_id=claude-2"));
  // cursor must be last_id, not inferred data id: return divergent last_id
  calls.length = 0;
  globalThis.fetch = async (url) => {
    const u = new URL(String(url));
    const after = u.searchParams.get("after_id");
    if (!after) return jsonResponse(200, { data: [{ id: "a", type: "model" }], has_more: true, last_id: "cursor-xyz" });
    assert.equal(after, "cursor-xyz");
    return jsonResponse(200, { data: [{ id: "b", type: "model" }], has_more: false, last_id: "b" });
  };
  const models2 = await c.listModels();
  assert.equal(models2.length, 2);
  // malformed pagination: has_more true with empty data -> ProtocolError
  globalThis.fetch = async () => jsonResponse(200, { data: [], has_more: true, last_id: "x" });
  await assert.rejects(() => c.listModels(), e => e.name === "ProtocolError");
  // malformed: has_more true without last_id -> ProtocolError
  globalThis.fetch = async () => jsonResponse(200, { data: [{ id: "a", type: "model" }], has_more: true });
  await assert.rejects(() => c.listModels(), e => e.name === "ProtocolError");
  // malformed: last_id empty string
  globalThis.fetch = async () => jsonResponse(200, { data: [{ id: "a", type: "model" }], has_more: true, last_id: "" });
  await assert.rejects(() => c.listModels(), e => e.name === "ProtocolError");
  // non-progressing cursor -> ProtocolError
  let page = 0;
  globalThis.fetch = async () => {
    if (page++ === 0) return jsonResponse(200, { data: [{ id: "a", type: "model" }], has_more: true, last_id: "a" });
    return jsonResponse(200, { data: [{ id: "a", type: "model" }], has_more: true, last_id: "a" });
  };
  await assert.rejects(() => c.listModels(), e => e.name === "ProtocolError");
  globalThis.fetch = undefined;
});

test("anthropic: system messages must be leading", async () => {
  const m = connect({ driver: "anthropic", endpoint: "https://api.anthropic.com", model: "claude-sonnet-4-20250514" });
  globalThis.fetch = async (url, init) => {
    const body = JSON.parse(init.body);
    assert.ok(body.system);
    return jsonResponse(200, anthropicMsg({ content: [{ type: "text", text: "ok" }] }));
  };
  // leading system messages allowed
  await m.generate(withTokens({ messages: [{ role: "system", content: "A" }, { role: "system", content: "B" }, { role: "user", content: "hi" }] }));
  // interleaved / late system must fail before I/O
  let fetched = false;
  globalThis.fetch = async () => { fetched = true; return jsonResponse(200, anthropicMsg({ content: [{ type: "text", text: "ok" }] })); };
  await assert.rejects(() => m.generate(withTokens({ messages: [{ role: "user", content: "hi" }, { role: "system", content: "late" }] })), e => e.name === "InvalidRequestError");
  assert.equal(fetched, false);
  fetched = false;
  await assert.rejects(() => m.generate(withTokens({ messages: [{ role: "user", content: "hi" }, { role: "assistant", content: "ok" }, { role: "system", content: "late" }] })), e => e.name === "InvalidRequestError");
  assert.equal(fetched, false);
  // streaming variant also
  await assert.rejects(async () => { for await (const _ of m.stream(withTokens({ messages: [{ role: "user", content: "hi" }, { role: "system", content: "late" }] }))) {} }, e => e.name === "InvalidRequestError");
  globalThis.fetch = undefined;
});

test("anthropic: thinking blocks preserved in providerMetadata, not ProtocolError", async () => {
  const m = connect({ driver: "anthropic", endpoint: "https://api.anthropic.com", model: "m" });
  // non-streaming with thinking + redacted_thinking
  globalThis.fetch = async () => jsonResponse(200, {
    id: "msg_think", type: "message", role: "assistant", model: "m",
    content: [
      { type: "thinking", thinking: "internal reasoning", signature: "sig123" },
      { type: "text", text: "answer" },
      { type: "redacted_thinking", data: "redacted-data" },
    ],
    stop_reason: "end_turn", stop_sequence: null,
    usage: { input_tokens: 2, output_tokens: 5 }
  });
  const r = await m.generate(withTokens({ messages: [{ role: "user", content: "hi" }] }));
  assert.equal(r.text, "answer");
  assert.equal(r.content.length, 1);
  assert.ok(Array.isArray(r.providerMetadata.thinking));
  assert.equal(r.providerMetadata.thinking.length, 2);
  assert.equal(r.providerMetadata.thinking[0].type, "thinking");
  // malformed thinking should ProtocolError
  globalThis.fetch = async () => jsonResponse(200, {
    id: "msg_bad", type: "message", role: "assistant", model: "m",
    content: [{ type: "thinking", thinking: 123 }],
    stop_reason: "end_turn", stop_sequence: null, usage: { input_tokens: 1, output_tokens: 1 }
  });
  await assert.rejects(() => m.generate(withTokens({ messages: [{ role: "user", content: "hi" }] })), e => e.name === "ProtocolError");
  // streaming with thinking deltas
  const encoder = new TextEncoder();
  const payload = [
    `event: message_start\ndata: {"type":"message_start","message":{"id":"msg_s","type":"message","role":"assistant","content":[],"model":"m","stop_reason":null,"stop_sequence":null,"usage":{"input_tokens":1,"output_tokens":1}}}\n\n`,
    `event: content_block_start\ndata: {"type":"content_block_start","index":0,"content_block":{"type":"thinking","thinking":"","signature":""}}\n\n`,
    `event: content_block_delta\ndata: {"type":"content_block_delta","index":0,"delta":{"type":"thinking_delta","thinking":"step1 "}}\n\n`,
    `event: content_block_delta\ndata: {"type":"content_block_delta","index":0,"delta":{"type":"thinking_delta","thinking":"step2"}}\n\n`,
    `event: content_block_delta\ndata: {"type":"content_block_delta","index":0,"delta":{"type":"signature_delta","signature":"sig-xyz"}}\n\n`,
    `event: content_block_stop\ndata: {"type":"content_block_stop","index":0}\n\n`,
    `event: content_block_start\ndata: {"type":"content_block_start","index":1,"content_block":{"type":"text","text":""}}\n\n`,
    `event: content_block_delta\ndata: {"type":"content_block_delta","index":1,"delta":{"type":"text_delta","text":"final"}}\n\n`,
    `event: content_block_stop\ndata: {"type":"content_block_stop","index":1}\n\n`,
    `event: message_delta\ndata: {"type":"message_delta","delta":{"stop_reason":"end_turn","stop_sequence":null},"usage":{"output_tokens":2}}\n\n`,
    `event: message_stop\ndata: {"type":"message_stop"}\n\n`,
  ].join("");
  globalThis.fetch = async () => new Response(new ReadableStream({ start(c){ c.enqueue(encoder.encode(payload)); c.close(); }}), { status:200, headers:{ "content-type":"text/event-stream"}});
  const events = [];
  for await (const e of m.stream(withTokens({ messages: [{ role: "user", content: "hi" }] }))) events.push(e);
  assert.ok(events.some(e=>e.type==="text_delta" && e.text==="final"));
  // thinking deltas must not produce text/tool deltas
  assert.equal(events.filter(e=>e.type==="text_delta").length, 1);
  const done = events.find(e=>e.type==="done");
  assert.equal(done.response.text, "final");
  assert.ok(Array.isArray(done.response.providerMetadata.thinking));
  assert.equal(done.response.providerMetadata.thinking[0].thinking, "step1 step2");
  assert.equal(done.response.providerMetadata.thinking[0].signature, "sig-xyz");
  // malformed thinking_delta without string -> ProtocolError
  const badPayload = [
    `event: message_start\ndata: {"type":"message_start","message":{"id":"msg_s","type":"message","role":"assistant","content":[],"model":"m","stop_reason":null,"stop_sequence":null,"usage":{"input_tokens":1,"output_tokens":1}}}\n\n`,
    `event: content_block_start\ndata: {"type":"content_block_start","index":0,"content_block":{"type":"thinking","thinking":"","signature":""}}\n\n`,
    `event: content_block_delta\ndata: {"type":"content_block_delta","index":0,"delta":{"type":"thinking_delta","thinking":123}}\n\n`,
  ].join("");
  globalThis.fetch = async () => new Response(new ReadableStream({ start(c){ c.enqueue(encoder.encode(badPayload)); c.close(); }}), { status:200, headers:{ "content-type":"text/event-stream"}});
  await assert.rejects(async ()=>{ for await (const _ of m.stream(withTokens({ messages: [{ role: "user", content: "hi" }] }))) {} }, e=> e.name==="ProtocolError");
  globalThis.fetch = undefined;
});

test("anthropic: maxOutputTokens 0 allowed, missing still rejected", async () => {
  const m = connect({ driver: "anthropic", endpoint: "https://api.anthropic.com", model: "m" });
  let seen = null;
  globalThis.fetch = async (url, init) => { seen = JSON.parse(init.body); return jsonResponse(200, anthropicMsg({ content: [{ type: "text", text: "ok" }] })); };
  await m.generate({ messages: [{ role: "user", content: "hi" }], maxOutputTokens: 0 });
  assert.equal(seen.max_tokens, 0);
  // 0 in streaming also allowed
  const encoder = new TextEncoder();
  const payload = [
    `event: message_start\ndata: {"type":"message_start","message":{"id":"msg_1","type":"message","role":"assistant","content":[],"model":"m","stop_reason":null,"stop_sequence":null,"usage":{"input_tokens":1,"output_tokens":0}}}\n\n`,
    `event: content_block_start\ndata: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}\n\n`,
    `event: content_block_delta\ndata: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"hi"}}\n\n`,
    `event: content_block_stop\ndata: {"type":"content_block_stop","index":0}\n\n`,
    `event: message_delta\ndata: {"type":"message_delta","delta":{"stop_reason":"end_turn","stop_sequence":null},"usage":{"output_tokens":1}}\n\n`,
    `event: message_stop\ndata: {"type":"message_stop"}\n\n`,
  ].join("");
  globalThis.fetch = async () => new Response(new ReadableStream({ start(c){ c.enqueue(encoder.encode(payload)); c.close(); }}), { status:200, headers:{ "content-type":"text/event-stream"}});
  const events = [];
  for await (const e of m.stream({ messages: [{ role: "user", content: "hi" }], maxOutputTokens: 0 })) events.push(e);
  assert.ok(events.some(e=>e.type==="done"));
  // missing still rejected before I/O (no fetch)
  let fetched = false;
  globalThis.fetch = async () => { fetched = true; return jsonResponse(200, anthropicMsg({ content: [{ type: "text", text: "ok" }] })); };
  await assert.rejects(() => m.generate({ messages: [{ role: "user", content: "hi" }] }), e=>e.name==="InvalidRequestError");
  assert.equal(fetched, false);
  // openai/ollama still require >=1 (no regression): 0 should be rejected
  const oai = connect({ driver: "openai-compatible", endpoint: "http://localhost:1234/v1", model: "m" });
  await assert.rejects(() => oai.generate({ messages: [{ role: "user", content: "hi" }], maxOutputTokens: 0 }), e=>e.name==="InvalidRequestError");
  const oll = connect({ driver: "ollama", endpoint: "http://localhost:11434", model: "m" });
  await assert.rejects(() => oll.generate({ messages: [{ role: "user", content: "hi" }], maxOutputTokens: 0 }), e=>e.name==="InvalidRequestError");
  globalThis.fetch = undefined;
});

test("anthropic: toolChoice protocol vs model distinction", async () => {
  const tool = { name: "get_weather", inputSchema: { type: "object", properties: {} } };
  const m = connect({ driver: "anthropic", endpoint: "https://api.anthropic.com", model: "claude-sonnet-4-20250514" });
  // protocol supports these wire variants (no preemptive blacklist)
  let seen = null;
  globalThis.fetch = async (url, init) => { seen = JSON.parse(init.body); return jsonResponse(200, anthropicMsg({ content: [{ type: "text", text: "ok" }] })); };
  for (const [choice, expected] of [
    ["auto", { type: "auto" }],
    ["none", { type: "none" }],
    ["required", { type: "any" }],
    [{ name: "get_weather" }, { type: "tool", name: "get_weather" }],
    ["get_weather", { type: "tool", name: "get_weather" }],
  ]) {
    await m.generate(withTokens({ messages: [{ role: "user", content: "hi" }], tools: [tool], toolChoice: choice }));
    assert.deepEqual(seen.tool_choice, expected);
  }
  // model may reject forced toolChoice -> normalized error via existing handling (no hardcoded blacklist)
  globalThis.fetch = async () => jsonResponse(400, { type: "error", error: { type: "invalid_request_error", message: "tool_choice any not supported for this model" } });
  await assert.rejects(() => m.generate(withTokens({ messages: [{ role: "user", content: "hi" }], tools: [tool], toolChoice: "required" })), e => e.name === "InvalidRequestError");
  globalThis.fetch = undefined;
});
