import assert from "node:assert/strict";
import { test } from "node:test";
import { connect, ConduitError } from "../dist/index.js";

const secret = "synthetic-bearer-47";

const tool = {
  name: "get_weather",
  description: "Get weather for a city",
  inputSchema: {
    type: "object",
    properties: { city: { type: "string", description: "City name" } },
    required: ["city"],
  },
};

const tool2 = {
  name: "get_time",
  inputSchema: { type: "object", properties: { zone: { type: "string" } } },
};

function jsonResponse(status, body, headers = {}) {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json", ...headers } });
}

test("openai-compatible: request tool schemas and tool_choice", async () => {
  let seen = null;
  globalThis.fetch = async (url, init) => {
    seen = JSON.parse(init.body);
    return jsonResponse(200, { id: "c1", model: "test", choices: [{ message: { role: "assistant", content: "ok" }, finish_reason: "stop" }] });
  };
  const m = connect({ driver: "openai-compatible", endpoint: "http://localhost:1234/v1", model: "test" });
  // single tool, auto
  await m.generate({ messages: [{ role: "user", content: "hi" }], tools: [tool], toolChoice: "auto" });
  assert.deepEqual(seen.tools, [{ type: "function", function: { name: "get_weather", description: "Get weather for a city", parameters: tool.inputSchema } }]);
  assert.equal(seen.tool_choice, "auto");
  // named tool via object
  await m.generate({ messages: [{ role: "user", content: "hi" }], tools: [tool, tool2], toolChoice: { name: "get_time" } });
  assert.deepEqual(seen.tool_choice, { type: "function", function: { name: "get_time" } });
  // named tool via string shorthand
  await m.generate({ messages: [{ role: "user", content: "hi" }], tools: [tool], toolChoice: "get_weather" });
  assert.deepEqual(seen.tool_choice, { type: "function", function: { name: "get_weather" } });
  // required
  await m.generate({ messages: [{ role: "user", content: "hi" }], tools: [tool], toolChoice: "required" });
  assert.equal(seen.tool_choice, "required");
  // no tool_choice
  await m.generate({ messages: [{ role: "user", content: "hi" }], tools: [tool] });
  assert.equal(seen.tool_choice, undefined);
  globalThis.fetch = undefined;
});

test("openai-compatible: non-streaming tool calls", async () => {
  globalThis.fetch = async () => jsonResponse(200, {
    id: "chat-1",
    model: "test",
    choices: [{
      message: {
        role: "assistant",
        content: null,
        tool_calls: [
          { id: "call_1", type: "function", function: { name: "get_weather", arguments: '{"city":"Tokyo"}' } },
          { id: "call_2", type: "function", function: { name: "get_time", arguments: '{"zone":"UTC"}' } },
        ],
      },
      finish_reason: "tool_calls",
    }],
  });
  const m = connect({ driver: "openai-compatible", endpoint: "http://localhost:1234/v1", model: "test" });
  const r = await m.generate({ messages: [{ role: "user", content: "hi" }], tools: [tool, tool2] });
  assert.equal(r.finishReason, "tool_call");
  assert.equal(r.content.length, 2);
  assert.equal(r.toolCalls.length, 2);
  assert.equal(r.toolCalls[0].name, "get_weather");
  assert.deepEqual(r.toolCalls[0].arguments, { city: "Tokyo" });
  assert.equal(r.toolCalls[0].id, "call_1");
  assert.equal(r.text, "");
  // content with text + tool call
  globalThis.fetch = async () => jsonResponse(200, {
    id: "chat-2",
    model: "test",
    choices: [{
      message: {
        role: "assistant",
        content: "I will check",
        tool_calls: [{ id: "call_3", type: "function", function: { name: "get_weather", arguments: '{}' } }],
      },
      finish_reason: "tool_calls",
    }],
  });
  const r2 = await m.generate({ messages: [{ role: "user", content: "hi" }], tools: [tool] });
  assert.equal(r2.content[0].type, "text");
  assert.equal(r2.content[0].text, "I will check");
  assert.equal(r2.content[1].type, "tool_call");
  globalThis.fetch = undefined;
});

test("openai-compatible: tool result messages", async () => {
  let seen = null;
  globalThis.fetch = async (url, init) => {
    seen = JSON.parse(init.body);
    return jsonResponse(200, { id: "c", model: "test", choices: [{ message: { role: "assistant", content: "sunny" }, finish_reason: "stop" }] });
  };
  const m = connect({ driver: "openai-compatible", endpoint: "http://localhost:1234/v1", model: "test" });
  await m.generate({
    messages: [
      { role: "user", content: "weather?" },
      { role: "assistant", content: [{ type: "tool_call", id: "call_1", name: "get_weather", arguments: { city: "Tokyo" } }] },
      { role: "tool", content: [{ type: "tool_result", callId: "call_1", content: "sunny" }] },
    ],
    tools: [tool],
  });
  assert.equal(seen.messages[2].role, "tool");
  assert.equal(seen.messages[2].content, "sunny");
  assert.equal(seen.messages[2].tool_call_id, "call_1");
  // assistant history with tool call
  assert.equal(seen.messages[1].role, "assistant");
  assert.equal(seen.messages[1].tool_calls[0].id, "call_1");
  assert.equal(seen.messages[1].tool_calls[0].function.name, "get_weather");
  assert.equal(seen.messages[1].tool_calls[0].function.arguments, '{"city":"Tokyo"}');
  globalThis.fetch = undefined;
});

test("openai-compatible: streaming tool_call_delta", async () => {
  const encoder = new TextEncoder();
  const payloads = [
    { choices: [{ index: 0, delta: { tool_calls: [{ index: 0, id: "call_1", type: "function", function: { name: "get_weather", arguments: "" } }] }, finish_reason: null }], id: "id1", model: "m" },
    { choices: [{ index: 0, delta: { tool_calls: [{ index: 0, function: { arguments: '{"city":' } }] }, finish_reason: null }], id: "id1", model: "m" },
    { choices: [{ index: 0, delta: { tool_calls: [{ index: 0, function: { arguments: '"Tokyo"}' } }] }, finish_reason: null }], id: "id1", model: "m" },
    { choices: [{ index: 0, delta: {}, finish_reason: "tool_calls" }], id: "id1", model: "m" },
  ];
  const sse = payloads.map(p => `data: ${JSON.stringify(p)}\n\n`).join("") + "data: [DONE]\n\n";
  globalThis.fetch = async () => new Response(new ReadableStream({ start(c) { c.enqueue(encoder.encode(sse)); c.close(); } }), { status: 200, headers: { "content-type": "text/event-stream" } });
  const m = connect({ driver: "openai-compatible", endpoint: "http://localhost:1234/v1", model: "test" });
  const events = [];
  for await (const e of m.stream({ messages: [{ role: "user", content: "hi" }], tools: [tool] })) events.push(e);
  const deltas = events.filter(e => e.type === "tool_call_delta");
  assert.equal(deltas.length, 3);
  assert.equal(deltas[0].name, "get_weather");
  assert.equal(deltas[0].id, "call_1");
  assert.equal(deltas[1].argumentsDelta, '{"city":');
  assert.equal(deltas[2].argumentsDelta, '"Tokyo"}');
  const done = events.find(e => e.type === "done");
  assert.equal(done.response.toolCalls[0].arguments.city, "Tokyo");
  assert.equal(done.response.finishReason, "tool_call");
  globalThis.fetch = undefined;
});

test("ollama: equivalent wire mapping, non-streaming tool calls", async () => {
  let seen = null;
  globalThis.fetch = async (url, init) => {
    seen = JSON.parse(init.body);
    return new Response(JSON.stringify({ model: "llama3.2", message: { role: "assistant", content: "", tool_calls: [{ function: { name: "get_weather", arguments: { city: "Tokyo" } } }] }, done: true }), { status: 200, headers: {} });
  };
  const m = connect({ driver: "ollama", endpoint: "http://localhost:11434", model: "llama3.2" });
  // Ollama native /api/chat has no toolChoice; tools without toolChoice must work
  const r = await m.generate({ messages: [{ role: "user", content: "hi" }], tools: [tool] });
  assert.deepEqual(seen.tools, [{ type: "function", function: { name: "get_weather", description: "Get weather for a city", parameters: tool.inputSchema } }]);
  assert.equal(seen.tool_choice, undefined);
  assert.equal(r.toolCalls[0].name, "get_weather");
  assert.deepEqual(r.toolCalls[0].arguments, { city: "Tokyo" });
  assert.equal(r.finishReason, "tool_call");
  // tool result
  globalThis.fetch = async (url, init) => {
    seen = JSON.parse(init.body);
    return new Response(JSON.stringify({ model: "llama3.2", message: { role: "assistant", content: "sunny" }, done: true }), { status: 200, headers: {} });
  };
  await m.generate({
    messages: [
      { role: "user", content: "hi" },
      { role: "assistant", content: [{ type: "tool_call", name: "get_weather", arguments: { city: "Tokyo" } }] },
      { role: "tool", content: [{ type: "tool_result", name: "get_weather", content: "sunny" }] },
    ],
    tools: [tool],
  });
  assert.equal(seen.messages[2].role, "tool");
  assert.equal(seen.messages[2].content, "sunny");
  assert.equal(seen.messages[2].tool_name, "get_weather");
  globalThis.fetch = undefined;
});

test("ollama: toolChoice is not supported natively", async () => {
  const m = connect({ driver: "ollama", endpoint: "http://localhost:11434", model: "llama3.2" });
  for (const choice of ["auto", "none", "required", { name: "get_weather" }, "get_weather"]) {
    await assert.rejects(m.generate({ messages: [{ role: "user", content: "hi" }], tools: [tool], toolChoice: choice }), e => e.name === "UnsupportedCapabilityError");
  }
  // without toolChoice it works
  let seen = null;
  globalThis.fetch = async (url, init) => {
    seen = JSON.parse(init.body);
    return new Response(JSON.stringify({ model: "llama3.2", message: { role: "assistant", content: "hi" }, done: true }), { status: 200, headers: {} });
  };
  await m.generate({ messages: [{ role: "user", content: "hi" }], tools: [tool] });
  assert.ok(seen.tools);
  assert.equal(seen.tool_choice, undefined);
  globalThis.fetch = undefined;
});

test("ollama: streaming tool deltas if clean", async () => {
  const encoder = new TextEncoder();
  const payloads = [
    JSON.stringify({ model: "llama", created_at: "now", message: { role: "assistant", content: "", tool_calls: [{ function: { name: "get_weather", arguments: { city: "Tokyo" } } }] }, done: false }) + "\n",
    JSON.stringify({ model: "llama", created_at: "now", message: { role: "assistant", content: "" }, done: true }) + "\n",
  ];
  globalThis.fetch = async () => new Response(new ReadableStream({ start(c) { for (const p of payloads) c.enqueue(encoder.encode(p)); c.close(); } }), { status: 200, headers: { "content-type": "application/x-ndjson" } });
  const m = connect({ driver: "ollama", endpoint: "http://localhost:11434", model: "llama" });
  const events = [];
  for await (const e of m.stream({ messages: [{ role: "user", content: "hi" }], tools: [tool] })) events.push(e);
  const deltas = events.filter(e => e.type === "tool_call_delta");
  assert.equal(deltas.length, 1);
  assert.equal(deltas[0].name, "get_weather");
  assert.equal(deltas[0].argumentsDelta, '{"city":"Tokyo"}');
  const done = events.find(e => e.type === "done");
  assert.equal(done.response.toolCalls[0].name, "get_weather");
  globalThis.fetch = undefined;
});

test("tool errors: malformed arguments, providerOptions conflict, missing correlation", async () => {
  const m = connect({ driver: "openai-compatible", endpoint: "http://localhost:1234/v1", model: "test" });
  // malformed tool call response -> ProtocolError
  globalThis.fetch = async () => jsonResponse(200, { id: "1", model: "m", choices: [{ message: { role: "assistant", content: null, tool_calls: [{ id: "call_1", type: "function", function: { name: "get_weather", arguments: "{bad" } }] }, finish_reason: "tool_calls" }] });
  await assert.rejects(m.generate({ messages: [{ role: "user", content: "hi" }], tools: [tool] }), e => e.name === "ProtocolError");
  // invalid tool args in request -> InvalidRequestError
  await assert.rejects(m.generate({ messages: [{ role: "assistant", content: [{ type: "tool_call", name: "get_weather", arguments: { bad: BigInt(1) } }] }] }), e => e.name === "InvalidRequestError");
  // providerOptions conflict
  await assert.rejects(m.generate({ messages: [{ role: "user", content: "hi" }], tools: [tool], providerOptions: { tools: [] } }), e => e.name === "InvalidRequestError");
  // unsupported image still fails
  await assert.rejects(m.generate({ messages: [{ role: "user", content: [{ type: "image", url: "http" }] }] }), e => e.name === "UnsupportedCapabilityError");
  globalThis.fetch = undefined;
});

test("providerOptions escape hatch remains intact with tools", async () => {
  let seen = null;
  globalThis.fetch = async (url, init) => {
    seen = JSON.parse(init.body);
    return jsonResponse(200, { id: "1", model: "m", choices: [{ message: { role: "assistant", content: "ok" }, finish_reason: "stop" }] });
  };
  const m = connect({ driver: "openai-compatible", endpoint: "http://localhost:1234/v1", model: "test" });
  await m.generate({ messages: [{ role: "user", content: "hi" }], tools: [tool], providerOptions: { native_flag: true, custom: 123 } });
  assert.equal(seen.native_flag, true);
  assert.equal(seen.custom, 123);
  assert.ok(seen.tools);
  globalThis.fetch = undefined;
});

test("openai streaming: fragmentation, interleaving, empty fragments, malformed", async () => {
  const encoder = new TextEncoder();
  const m = connect({ driver: "openai-compatible", endpoint: "http://localhost:1234/v1", model: "test" });
  // Fragmented arguments across arbitrary SSE chunks and interleaved indices
  const payloads = [
    // id arrives separately from name/arguments
    { choices: [{ index: 0, delta: { tool_calls: [{ index: 0, id: "call_0" }] }, finish_reason: null }], id: "id1", model: "m" },
    { choices: [{ index: 0, delta: { tool_calls: [{ index: 0, function: { name: "get_weather" } }] }, finish_reason: null }], id: "id1", model: "m" },
    { choices: [{ index: 0, delta: { tool_calls: [{ index: 0, function: { arguments: "" } }] }, finish_reason: null }], id: "id1", model: "m" }, // empty fragment
    { choices: [{ index: 0, delta: { tool_calls: [{ index: 0, function: { arguments: '{"city":' } }] }, finish_reason: null }], id: "id1", model: "m" },
    // interleaved second tool call
    { choices: [{ index: 0, delta: { tool_calls: [{ index: 1, id: "call_1", function: { name: "get_time", arguments: "" } }] }, finish_reason: null }], id: "id1", model: "m" },
    { choices: [{ index: 0, delta: { tool_calls: [{ index: 1, function: { arguments: '{"zone":"' } }] }, finish_reason: null }], id: "id1", model: "m" },
    { choices: [{ index: 0, delta: { tool_calls: [{ index: 0, function: { arguments: '"Tokyo"}' } }] }, finish_reason: null }], id: "id1", model: "m" },
    { choices: [{ index: 0, delta: { tool_calls: [{ index: 1, function: { arguments: 'UTC"}' } }] }, finish_reason: null }], id: "id1", model: "m" },
    { choices: [{ index: 0, delta: {}, finish_reason: "tool_calls" }], id: "id1", model: "m" },
  ];
  // Split each SSE data line across arbitrary byte boundaries to stress fragmentation
  const sse = payloads.map(p => `data: ${JSON.stringify(p)}\n\n`).join("") + "data: [DONE]\n\n";
  const bytes = encoder.encode(sse);
  // Fragment into 7-byte chunks
  const fragments = [];
  for (let i = 0; i < bytes.length; i += 7) fragments.push(bytes.slice(i, i + 7));
  let fragIdx = 0;
  globalThis.fetch = async () => new Response(new ReadableStream({
    start(c) { c.enqueue(bytes); c.close(); },
  }), { status: 200, headers: { "content-type": "text/event-stream" } });
  // Also test byte-fragmented stream via ReadableStream with tiny pulls
  globalThis.fetch = async () => new Response(new ReadableStream({
    async pull(controller) {
      if (fragIdx < fragments.length) controller.enqueue(fragments[fragIdx++]);
      else controller.close();
    },
  }), { status: 200, headers: { "content-type": "text/event-stream" } });
  const events = [];
  for await (const e of m.stream({ messages: [{ role: "user", content: "hi" }], tools: [tool, tool2] })) events.push(e);
  const done = events.find(e => e.type === "done");
  assert.equal(done.response.toolCalls.length, 2);
  assert.deepEqual(done.response.toolCalls[0].arguments, { city: "Tokyo" });
  assert.deepEqual(done.response.toolCalls[1].arguments, { zone: "UTC" });
  assert.equal(done.response.finishReason, "tool_call");
  // Ensure empty fragment did not produce spurious delta
  const argDeltas = events.filter(e => e.type === "tool_call_delta" && e.argumentsDelta);
  assert.ok(argDeltas.some(d => d.argumentsDelta === '{"city":'));
  // Malformed indices/fields must be ProtocolError
  const badPayloads = [
    { choices: [{ index: 0, delta: { tool_calls: [{ id: "call_0" }] }, finish_reason: null }], id: "id1", model: "m" }, // missing index
    { choices: [{ index: 0, delta: { tool_calls: [{ index: "0", id: "call_0" }] }, finish_reason: null }], id: "id1", model: "m" }, // index not integer
    { choices: [{ index: 0, delta: { tool_calls: [{ index: -1, id: "call_0" }] }, finish_reason: null }], id: "id1", model: "m" }, // negative index
    { choices: [{ index: 0, delta: { tool_calls: [{ index: 0, function: { arguments: 123 } }] }, finish_reason: null }], id: "id1", model: "m" }, // arguments not string
  ];
  for (const bad of badPayloads) {
    const sseBad = `data: ${JSON.stringify(bad)}\n\n` + "data: [DONE]\n\n";
    globalThis.fetch = async () => new Response(new ReadableStream({ start(c){ c.enqueue(encoder.encode(sseBad)); c.close(); }}), {status:200, headers:{"content-type":"text/event-stream"}});
    await assert.rejects(async () => { for await (const e of m.stream({ messages: [{role:"user", content:"hi"}], tools:[tool]})) {} }, e => e.name === "ProtocolError");
  }
  globalThis.fetch = undefined;
});

test("ollama streaming: complete tool_calls not token fragments", async () => {
  const encoder = new TextEncoder();
  const m = connect({ driver: "ollama", endpoint: "http://localhost:11434", model: "llama" });
  // Ollama sends complete tool_calls objects per NDJSON line, not incremental argument tokens
  const payloads = [
    JSON.stringify({ model: "llama", message: { role: "assistant", content: "", tool_calls: [{ function: { name: "get_weather", arguments: { city: "Tokyo" } } }] }, done: false }) + "\n",
    JSON.stringify({ model: "llama", message: { role: "assistant", content: "", tool_calls: [{ function: { name: "get_time", arguments: { zone: "UTC" } } }] }, done: false }) + "\n",
    JSON.stringify({ model: "llama", message: { role: "assistant", content: "" }, done: true }) + "\n",
  ];
  globalThis.fetch = async () => new Response(new ReadableStream({ start(c){ for(const p of payloads) c.enqueue(encoder.encode(p)); c.close(); }}), {status:200, headers:{"content-type":"application/x-ndjson"}});
  const events = [];
  for await (const e of m.stream({ messages: [{role:"user", content:"hi"}], tools:[tool, tool2]})) events.push(e);
  const deltas = events.filter(e=>e.type==="tool_call_delta");
  // Each delta represents a newly observed complete call, not a token fragment
  assert.equal(deltas.length, 2);
  assert.equal(deltas[0].name, "get_weather");
  assert.equal(deltas[0].argumentsDelta, '{"city":"Tokyo"}');
  assert.equal(deltas[1].name, "get_time");
  const done = events.find(e=>e.type==="done");
  assert.equal(done.response.toolCalls.length, 2);
  globalThis.fetch = undefined;
});

test("semantic-content safety: tool names/args/results not redacted", async () => {
  const m = connect({ driver: "openai-compatible", endpoint: "http://localhost:1234/v1", model: "test", credentials: secret });
  // Tool name/args/result containing secret must be preserved verbatim
  globalThis.fetch = async (url, init) => {
    // Verify request preserves secret in tool definition (not redacted)
    const body = JSON.parse(init.body);
    assert.equal(body.tools[0].function.name, secret); // name is secret, should not be redacted
    return new Response(JSON.stringify({
      id: "1", model: "m",
      choices: [{ message: { role: "assistant", content: null, tool_calls: [{ id: "call_1", type: "function", function: { name: secret, arguments: JSON.stringify({ city: secret }) } }] }, finish_reason: "tool_calls" }]
    }), {status:200, headers:{}});
  };
  const secretTool = { name: secret, inputSchema: { type:"object", properties:{ city:{type:"string"} } } };
  const r = await m.generate({ messages:[{role:"user", content:"hi"}], tools:[secretTool]});
  assert.equal(r.toolCalls[0].name, secret);
  assert.deepEqual(r.toolCalls[0].arguments, { city: secret });
  assert.equal(r.text, ""); // text empty but tool preserved
  // Tool result containing secret
  globalThis.fetch = async (url, init) => {
    const body = JSON.parse(init.body);
    const toolMsg = body.messages.find(m=>m.role==="tool");
    assert.equal(toolMsg.content, secret); // result content preserved
    assert.equal(toolMsg.tool_call_id, secret);
    return new Response(JSON.stringify({id:"1", model:"m", choices:[{message:{role:"assistant", content:`echo ${secret}`}, finish_reason:"stop"}]}),{status:200, headers:{}});
  };
  const r2 = await m.generate({
    messages:[
      {role:"user", content:"hi"},
      {role:"assistant", content:[{type:"tool_call", id: secret, name: secret, arguments:{city:secret}}]},
      {role:"tool", content:[{type:"tool_result", callId: secret, content: secret}]}
    ],
    tools:[secretTool]
  });
  assert.equal(r2.text, `echo ${secret}`);
  // Ollama same
  const o = connect({ driver: "ollama", endpoint: "http://localhost:11434", model:"llama", credentials: secret });
  globalThis.fetch = async () => new Response(JSON.stringify({model:"llama", message:{role:"assistant", content:"", tool_calls:[{function:{name:secret, arguments:{city: secret}}} ]}, done:true}),{status:200, headers:{}});
  const r3 = await o.generate({messages:[{role:"user", content:"hi"}], tools:[secretTool]});
  assert.equal(r3.toolCalls[0].name, secret);
  assert.deepEqual(r3.toolCalls[0].arguments, {city: secret});
  // Verify error redaction does not leak secret via tool content
  globalThis.fetch = async () => new Response(JSON.stringify({error:{message:`fail ${secret}`, type:"x", code:"y"}}),{status:500, headers:{}});
  try { await m.generate({messages:[{role:"user", content:"hi"}]}); assert.fail(); } catch(e){ assert.ok(!String(e.message).includes(secret)); assert.ok(JSON.stringify(e).includes("[REDACTED]")); }
  globalThis.fetch = undefined;
});
