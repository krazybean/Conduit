import assert from "node:assert/strict";
import { test } from "node:test";
import { connect } from "../dist/index.js";

const secret = "synthetic-gemini-key";

function jsonResponse(status, body, headers = {}) {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json", ...headers } });
}

function geminiResp(contentParts, finishReason="STOP", usage={promptTokenCount:4,candidatesTokenCount:2,totalTokenCount:6}) {
  return {
    candidates: [{ content: { parts: contentParts, role: "model" }, finishReason, index: 0 }],
    usageMetadata: usage,
    responseId: "resp-123",
    modelVersion: "gemini-2.0-flash"
  };
}

test("gemini: no-I/O construction", () => {
  const c = connect({ driver: "gemini", endpoint: "https://generativelanguage.googleapis.com", credentials: secret });
  assert.ok(c);
  const m = c.model("gemini-2.0-flash");
  assert.ok(m);
});

test("gemini: endpoint and auth", async () => {
  let seenUrl=null, seenHeaders=null;
  globalThis.fetch = async (url, init) => { seenUrl=url; seenHeaders=init.headers; return jsonResponse(200, geminiResp([{text:"hi"}])); };
  const m = connect({ driver: "gemini", endpoint: "https://generativelanguage.googleapis.com", credentials: secret, model: "gemini-2.0-flash" });
  await m.generate({ messages:[{role:"user",content:"hello"}] });
  assert.ok(String(seenUrl).includes("/v1beta/models/gemini-2.0-flash:generateContent"), `url ${seenUrl}`);
  assert.equal(new Headers(seenHeaders).get("x-goog-api-key"), secret);
  assert.equal(new Headers(seenHeaders).get("content-type"), "application/json");
  // custom base path
  seenUrl=null;
  const m2 = connect({ driver: "gemini", endpoint: "https://example.com/custom", credentials: secret, model: "gemini-2.0-flash" });
  globalThis.fetch = async (url)=>{ seenUrl=url; return jsonResponse(200, geminiResp([{text:"hi"}])); };
  await m2.generate({ messages:[{role:"user",content:"hi"}] });
  assert.ok(String(seenUrl).includes("/custom/v1beta/models/gemini-2.0-flash:generateContent"));
  // streaming URL
  globalThis.fetch = async (url)=>{ seenUrl=url; return new Response(new ReadableStream({start(c){ c.enqueue(new TextEncoder().encode('data: {"candidates":[{"content":{"parts":[{"text":"hi"}],"role":"model"},"finishReason":"STOP"}]}\n\n')); c.close();}}), {status:200, headers:{"content-type":"text/event-stream"}}); };
  const events=[]; for await(const e of m2.stream({messages:[{role:"user",content:"hi"}]})) events.push(e);
  assert.ok(String(seenUrl).includes(":streamGenerateContent?alt=sse"));
  // redirect manual is set via fetch init – we trust
  // protected header collision
  assert.throws(()=> connect({ driver:"gemini", endpoint:"https://generativelanguage.googleapis.com", credentials:secret, headers:{"x-goog-api-key":"other"}}), e=>e.name==="InvalidRequestError");
  globalThis.fetch=undefined;
});

test("gemini: system and message mapping", async () => {
  let seen=null;
  globalThis.fetch = async (url, init)=>{ seen=JSON.parse(init.body); return jsonResponse(200, geminiResp([{text:"ok"}])); };
  const m = connect({ driver:"gemini", endpoint:"https://generativelanguage.googleapis.com", model:"gemini-2.0-flash" });
  await m.generate({ messages:[{role:"system",content:"Be helpful."},{role:"user",content:"Hello"}] });
  assert.deepEqual(seen.systemInstruction, {parts:[{text:"Be helpful."}]});
  assert.equal(seen.contents.length,1);
  assert.equal(seen.contents[0].role,"user");
  await m.generate({ messages:[{role:"system",content:"A"},{role:"system",content:"B"},{role:"user",content:"hi"}] });
  assert.equal(seen.systemInstruction.parts.length,2);
  // late system must fail before I/O
  let fetched=false; globalThis.fetch=async()=>{ fetched=true; return jsonResponse(200, geminiResp([{text:"ok"}])); };
  await assert.rejects(()=> m.generate({ messages:[{role:"user",content:"hi"},{role:"system",content:"late"}] }), e=>e.name==="InvalidRequestError");
  assert.equal(fetched,false);
  globalThis.fetch=undefined;
});

test("gemini: tools and tool result correlation by name", async () => {
  let seen=null;
  const tool={ name:"get_weather", description:"Get weather", inputSchema:{type:"object", properties:{location:{type:"string"}}, required:["location"]}};
  globalThis.fetch = async (url,init)=>{ seen=JSON.parse(init.body); return jsonResponse(200, geminiResp([{text:"ok"}])); };
  const m = connect({ driver:"gemini", endpoint:"https://generativelanguage.googleapis.com", model:"gemini-2.0-flash" });
  await m.generate({ messages:[{role:"user",content:"hi"}], tools:[tool] });
  assert.deepEqual(seen.tools, [{functionDeclarations:[{name:"get_weather",description:"Get weather",parametersJsonSchema:tool.inputSchema}]}]);
  // tool call in history
  await m.generate({ messages:[{role:"user",content:"hi"},{role:"assistant",content:[{type:"tool_call", name:"get_weather", arguments:{location:"Paris"}}]}], tools:[tool] });
  assert.equal(seen.contents[1].role,"model");
  assert.equal(seen.contents[1].parts[0].functionCall.name,"get_weather");
  // tool result requires name, not callId, correlation by name
  await m.generate({ messages:[{role:"user",content:"hi"},{role:"assistant",content:[{type:"tool_call", name:"get_weather", arguments:{location:"Paris"}}]},{role:"tool",content:[{type:"tool_result", name:"get_weather", content:'{"temp":72}'}]}], tools:[tool] });
  assert.equal(seen.contents[2].role,"user");
  assert.equal(seen.contents[2].parts[0].functionResponse.name,"get_weather");
  // missing name should fail before I/O
  await assert.rejects(()=> m.generate({ messages:[{role:"user",content:"hi"},{role:"assistant",content:[{type:"tool_call", name:"get_weather", arguments:{}}]}, {role:"tool",content:[{type:"tool_result", content:"hi"}]} ], tools:[tool] }), e=>e.name==="InvalidRequestError");
  // tool_call without name should fail
  await assert.rejects(()=> m.generate({ messages:[{role:"assistant",content:[{type:"tool_call", arguments:{}}]}] }), e=>e.name==="InvalidRequestError");
  // Gemini does not use id – id is optional and not sent
  globalThis.fetch=undefined;
});

test("gemini: toolChoice mapping", async () => {
  let seen=null;
  const tool={ name:"get_weather", inputSchema:{type:"object", properties:{}}};
  globalThis.fetch=async(url,init)=>{ seen=JSON.parse(init.body); return jsonResponse(200, geminiResp([{text:"ok"}])); };
  const m=connect({ driver:"gemini", endpoint:"https://generativelanguage.googleapis.com", model:"gemini-2.0-flash" });
  await m.generate({ messages:[{role:"user",content:"hi"}], tools:[tool], toolChoice:"auto" });
  assert.deepEqual(seen.toolConfig, {functionCallingConfig:{mode:"AUTO"}});
  await m.generate({ messages:[{role:"user",content:"hi"}], tools:[tool], toolChoice:"none" });
  assert.deepEqual(seen.toolConfig, {functionCallingConfig:{mode:"NONE"}});
  await m.generate({ messages:[{role:"user",content:"hi"}], tools:[tool], toolChoice:"required" });
  assert.deepEqual(seen.toolConfig, {functionCallingConfig:{mode:"ANY"}});
  await m.generate({ messages:[{role:"user",content:"hi"}], tools:[tool], toolChoice:{name:"get_weather"}});
  assert.deepEqual(seen.toolConfig, {functionCallingConfig:{mode:"ANY",allowedFunctionNames:["get_weather"]}});
  await m.generate({ messages:[{role:"user",content:"hi"}], tools:[tool], toolChoice:"get_weather"});
  assert.deepEqual(seen.toolConfig, {functionCallingConfig:{mode:"ANY",allowedFunctionNames:["get_weather"]}});
  // model rejection should normalize via existing error handling (no blacklist)
  globalThis.fetch=async()=> jsonResponse(400, {error:{code:400,message:"tool not supported",status:"INVALID_ARGUMENT"}});
  await assert.rejects(()=> m.generate({ messages:[{role:"user",content:"hi"}], tools:[tool], toolChoice:"required"}), e=> e.name==="InvalidRequestError");
  globalThis.fetch=undefined;
});

test("gemini: responseFormat", async () => {
  let seen=null;
  globalThis.fetch=async(url,init)=>{ seen=JSON.parse(init.body); return jsonResponse(200, geminiResp([{text:'{"a":1}'}])); };
  const m=connect({ driver:"gemini", endpoint:"https://generativelanguage.googleapis.com", model:"gemini-2.0-flash" });
  await m.generate({ messages:[{role:"user",content:"hi"}], responseFormat:{type:"text"} });
  assert.equal(seen.generationConfig?.responseMimeType, undefined);
  await m.generate({ messages:[{role:"user",content:"hi"}], responseFormat:{type:"json"} });
  assert.equal(seen.generationConfig.responseMimeType, "application/json");
  await m.generate({ messages:[{role:"user",content:"hi"}], responseFormat:{type:"json_schema", schema:{type:"object", properties:{a:{type:"string"}}}} });
  assert.equal(seen.generationConfig.responseMimeType, "application/json");
  assert.deepEqual(seen.generationConfig.responseJsonSchema, {type:"object", properties:{a:{type:"string"}}});
  globalThis.fetch=undefined;
});

test("gemini: response normalization", async () => {
  const m=connect({ driver:"gemini", endpoint:"https://generativelanguage.googleapis.com", model:"gemini-2.0-flash" });
  // text
  globalThis.fetch=async()=> jsonResponse(200, geminiResp([{text:"Hello!"}], "STOP", {promptTokenCount:4,candidatesTokenCount:2,totalTokenCount:6}));
  let r=await m.generate({ messages:[{role:"user",content:"hi"}] });
  assert.equal(r.text,"Hello!");
  assert.equal(r.finishReason,"stop");
  assert.equal(r.usage.inputTokens,4);
  assert.equal(r.usage.outputTokens,2);
  assert.equal(r.usage.totalTokens,6);
  // tool call
  globalThis.fetch=async()=> jsonResponse(200, geminiResp([{functionCall:{name:"get_weather",args:{location:"Paris"}}}], "STOP"));
  r=await m.generate({ messages:[{role:"user",content:"hi"}] });
  assert.equal(r.finishReason,"tool_call");
  assert.equal(r.toolCalls[0].name,"get_weather");
  // raw text preservation with credential
  const secret2="synthetic-gemini-key";
  const m2=connect({ driver:"gemini", endpoint:"https://generativelanguage.googleapis.com", credentials:secret2, model:"gemini-2.0-flash" });
  globalThis.fetch=async()=> jsonResponse(200, geminiResp([{text:secret2}]));
  r=await m2.generate({ messages:[{role:"user",content:"hi"}] });
  assert.equal(r.text,secret2);
  // finish reasons
  globalThis.fetch=async()=> jsonResponse(200, geminiResp([{text:"a"}], "MAX_TOKENS"));
  r=await m.generate({ messages:[{role:"user",content:"hi"}] });
  assert.equal(r.finishReason,"length");
  globalThis.fetch=async()=> jsonResponse(200, geminiResp([{text:"a"}], "SAFETY"));
  r=await m.generate({ messages:[{role:"user",content:"hi"}] });
  assert.equal(r.finishReason,"content_filter");
  // content_filter via promptFeedback
  globalThis.fetch=async()=> jsonResponse(200, {promptFeedback:{blockReason:"SAFETY"}, usageMetadata:{promptTokenCount:2}});
  r=await m.generate({ messages:[{role:"user",content:"hi"}] });
  assert.equal(r.finishReason,"content_filter");
  globalThis.fetch=undefined;
});

test("gemini: streaming fragmentation", async () => {
  const encoder=new TextEncoder();
  const payload=[
    'data: {"candidates":[{"content":{"parts":[{"text":"Hello "}],"role":"model"},"finishReason":"STOP"}],"usageMetadata":{"promptTokenCount":4,"candidatesTokenCount":1}}\n\n',
    'data: {"candidates":[{"content":{"parts":[{"text":"world"}],"role":"model"},"finishReason":"STOP"}],"usageMetadata":{"promptTokenCount":4,"candidatesTokenCount":2,"totalTokenCount":6}}\n\n',
  ].join("");
  const bytes=encoder.encode(payload);
  // fragment into 7-byte chunks
  const frags=[]; for(let i=0;i<bytes.length;i+=7) frags.push(bytes.slice(i,i+7));
  let idx=0;
  globalThis.fetch=async()=> new Response(new ReadableStream({async pull(c){ if(idx<frags.length) c.enqueue(frags[idx++]); else c.close();}}), {status:200, headers:{"content-type":"text/event-stream"}});
  const m=connect({ driver:"gemini", endpoint:"https://generativelanguage.googleapis.com", model:"gemini-2.0-flash" });
  const events=[]; for await(const e of m.stream({messages:[{role:"user",content:"hi"}]})) events.push(e);
  assert.equal(events[0].type,"start");
  assert.ok(events.some(e=>e.type==="text_delta" && e.text==="Hello "));
  assert.ok(events.some(e=>e.type==="text_delta" && e.text==="world"));
  const done=events.find(e=>e.type==="done");
  assert.equal(done.response.text,"Hello world");
  // tool streaming
  const payload2=[
    'data: {"candidates":[{"content":{"parts":[{"functionCall":{"name":"get_weather","args":{"location":"Paris"}}}],"role":"model"}}]}\n\n',
  ].join("");
  globalThis.fetch=async()=> new Response(new ReadableStream({start(c){ c.enqueue(encoder.encode(payload2)); c.close();}}), {status:200, headers:{"content-type":"text/event-stream"}});
  const events2=[]; for await(const e of m.stream({messages:[{role:"user",content:"hi"}]})) events2.push(e);
  const toolDeltas=events2.filter(e=>e.type==="tool_call_delta");
  assert.equal(toolDeltas[0].name,"get_weather");
  // malformed
  globalThis.fetch=async()=> new Response(new ReadableStream({start(c){ c.enqueue(encoder.encode('data: {bad json\n\n')); c.close();}}), {status:200, headers:{"content-type":"text/event-stream"}});
  await assert.rejects(async()=>{ for await(const _ of m.stream({messages:[{role:"user",content:"hi"}]})) {} }, e=>e.name==="ProtocolError");
  // abrupt EOF
  globalThis.fetch=async()=> new Response(new ReadableStream({start(c){ c.close();}}), {status:200, headers:{"content-type":"text/event-stream"}});
  await assert.rejects(async()=>{ for await(const _ of m.stream({messages:[{role:"user",content:"hi"}]})) {} }, e=>e.name==="ProtocolError");
  globalThis.fetch=undefined;
});

test("gemini: model listing pagination", async () => {
  const calls=[];
  globalThis.fetch=async(url)=>{
    calls.push(String(url));
    const u=new URL(String(url));
    const token=u.searchParams.get("pageToken");
    if(!token) return jsonResponse(200, {models:[{name:"models/gemini-2.0-flash",displayName:"Flash"},{name:"models/gemini-1.5-pro",displayName:"Pro"}], nextPageToken:"token-1"});
    assert.equal(token,"token-1");
    return jsonResponse(200, {models:[{name:"models/gemini-1.0-pro",displayName:"Legacy"}],});
  };
  const c=connect({ driver:"gemini", endpoint:"https://generativelanguage.googleapis.com", credentials:secret });
  const models=await c.listModels();
  assert.equal(models.length,3);
  assert.equal(models[0].id,"gemini-2.0-flash");
  assert.equal(models[0].name,"Flash");
  assert.equal(models[2].id,"gemini-1.0-pro");
  assert.equal(calls.length,2);
  assert.ok(calls[0].endsWith("/v1beta/models"));
  assert.ok(calls[1].includes("pageToken=token-1"));
  // malformed nextPageToken
  globalThis.fetch=async()=> jsonResponse(200, {models:[{name:"models/a",displayName:"a"}], nextPageToken:123});
  await assert.rejects(()=> c.listModels(), e=>e.name==="ProtocolError");
  globalThis.fetch=undefined;
});

test("gemini: providerOptions", async () => {
  let seen=null;
  globalThis.fetch=async(url,init)=>{ seen=JSON.parse(init.body); return jsonResponse(200, geminiResp([{text:"ok"}])); };
  const m=connect({ driver:"gemini", endpoint:"https://generativelanguage.googleapis.com", model:"gemini-2.0-flash" });
  await m.generate({ messages:[{role:"user",content:"hi"}], providerOptions:{candidateCount:1} });
  assert.equal(seen.candidateCount,1);
  await assert.rejects(()=> m.generate({ messages:[{role:"user",content:"hi"}], providerOptions:{contents:[]}}), e=>e.name==="InvalidRequestError");
  await assert.rejects(()=> m.generate({ messages:[{role:"user",content:"hi"}], providerOptions:{generationConfig:{temperature:1}}}), e=>e.name==="InvalidRequestError");
  globalThis.fetch=undefined;
});

test("gemini: errors and redaction", async () => {
  const m=connect({ driver:"gemini", endpoint:"https://generativelanguage.googleapis.com", credentials:secret, model:"gemini-2.0-flash" });
  globalThis.fetch=async()=> jsonResponse(401, {error:{code:401,message:`bad ${secret}`,status:"UNAUTHENTICATED"}});
  await assert.rejects(()=> m.generate({ messages:[{role:"user",content:"hi"}] }), e=>{ assert.equal(e.name,"AuthenticationError"); assert.ok(!String(e.message).includes(secret)); return true; });
  globalThis.fetch=async()=> jsonResponse(429, {error:{code:429,message:"quota",status:"RESOURCE_EXHAUSTED"}});
  await assert.rejects(()=> m.generate({ messages:[{role:"user",content:"hi"}] }), e=>e.name==="RateLimitError");
  // in-band stream error
  const enc=new TextEncoder();
  globalThis.fetch=async()=> new Response(new ReadableStream({start(c){ c.enqueue(enc.encode('data: {"error":{"code":401,"message":"bad","status":"UNAUTHENTICATED"}}\n\n')); c.close();}}), {status:200, headers:{"content-type":"text/event-stream"}});
  await assert.rejects(async()=>{ for await(const _ of m.stream({messages:[{role:"user",content:"hi"}]})) {} }, e=>e.name==="AuthenticationError" || e.name==="ProviderError");
  globalThis.fetch=undefined;
});

test("gemini: timeout and cancellation", async () => {
  const m=connect({ driver:"gemini", endpoint:"https://generativelanguage.googleapis.com", model:"gemini-2.0-flash", timeout:10 });
  globalThis.fetch=async(url, init)=>{ await new Promise(r=>setTimeout(r,50)); return jsonResponse(200, geminiResp([{text:"hi"}])); };
  await assert.rejects(()=> m.generate({ messages:[{role:"user",content:"hi"}] }), e=>e.name==="TimeoutError");
  const ac=new AbortController(); ac.abort();
  await assert.rejects(()=> m.generate({ messages:[{role:"user",content:"hi"}], signal:ac.signal }), e=>e.name==="CancelledError");
  // early break cleanup
  const enc=new TextEncoder();
  globalThis.fetch=async()=> new Response(new ReadableStream({start(c){ c.enqueue(enc.encode('data: {"candidates":[{"content":{"parts":[{"text":"hi"}],"role":"model"}}]}\n\n')); c.close();}}), {status:200, headers:{"content-type":"text/event-stream"}});
  const stream=m.stream({messages:[{role:"user",content:"hi"}]});
  for await(const e of stream){ if(e.type==="text_delta") break; }
  // should not hang
  globalThis.fetch=undefined;
});

test("gemini: functionCall id preservation and responseJsonSchema", async () => {
  // A: response with id -> ToolCallPart with id
  const m=connect({ driver:"gemini", endpoint:"https://generativelanguage.googleapis.com", model:"gemini-2.0-flash" });
  globalThis.fetch=async()=> jsonResponse(200, geminiResp([{functionCall:{id:"call-123",name:"weather",args:{location:"Paris"}}}]));
  let r=await m.generate({ messages:[{role:"user",content:"hi"}] });
  assert.equal(r.toolCalls[0].id, "call-123");
  assert.equal(r.toolCalls[0].name, "weather");
  // D: id-less remains valid
  globalThis.fetch=async()=> jsonResponse(200, geminiResp([{functionCall:{name:"weather",args:{location:"Paris"}}}]));
  r=await m.generate({ messages:[{role:"user",content:"hi"}] });
  assert.equal(r.toolCalls[0].id, undefined);
  assert.equal(r.toolCalls[0].name, "weather");
  // B: sending assistant tool_call preserves id
  let seen=null;
  globalThis.fetch=async(url,init)=>{ seen=JSON.parse(init.body); return jsonResponse(200, geminiResp([{text:"ok"}])); };
  await m.generate({ messages:[{role:"assistant",content:[{type:"tool_call", id:"call-123", name:"weather", arguments:{location:"Paris"}}]}] });
  assert.equal(seen.contents[0].parts[0].functionCall.id, "call-123");
  // without id, no id field sent
  await m.generate({ messages:[{role:"assistant",content:[{type:"tool_call", name:"weather", arguments:{location:"Paris"}}]}] });
  assert.equal(seen.contents[0].parts[0].functionCall.id, undefined);
  // C: ToolResultPart with callId and name -> functionResponse id+name
  await m.generate({ messages:[{role:"assistant",content:[{type:"tool_call", id:"call-123", name:"weather", arguments:{}}]}, {role:"tool",content:[{type:"tool_result", callId:"call-123", name:"weather", content:'{"temp":1}'}]}] });
  assert.equal(seen.contents[1].parts[0].functionResponse.id, "call-123");
  assert.equal(seen.contents[1].parts[0].functionResponse.name, "weather");
  // D: id-less tool_result still valid (name required)
  await m.generate({ messages:[{role:"assistant",content:[{type:"tool_call", name:"weather", arguments:{}}]}, {role:"tool",content:[{type:"tool_result", name:"weather", content:"ok"}]}] });
  assert.equal(seen.contents[1].parts[0].functionResponse.name, "weather");
  assert.equal(seen.contents[1].parts[0].functionResponse.id, undefined);
  // E: IDs not redacted even if equals credential
  const cred = secret;
  const mCred=connect({ driver:"gemini", endpoint:"https://generativelanguage.googleapis.com", credentials:cred, model:"gemini-2.0-flash" });
  globalThis.fetch=async()=> jsonResponse(200, geminiResp([{functionCall:{id:cred, name:"weather",args:{}}}]));
  r=await mCred.generate({ messages:[{role:"user",content:"hi"}] });
  assert.equal(r.toolCalls[0].id, cred);
  // streaming preserves id
  const enc=new TextEncoder();
  globalThis.fetch=async()=> new Response(new ReadableStream({start(c){ c.enqueue(enc.encode('data: {"candidates":[{"content":{"parts":[{"functionCall":{"id":"call-123","name":"weather","args":{"location":"Paris"}}}],"role":"model"}}]}\n\n')); c.close();}}), {status:200, headers:{"content-type":"text/event-stream"}});
  const events=[]; for await(const e of m.stream({messages:[{role:"user",content:"hi"}]})) events.push(e);
  const tdelta=events.find(e=>e.type==="tool_call_delta");
  assert.equal(tdelta.id, "call-123");
  // structured output wire uses responseJsonSchema not responseSchema
  globalThis.fetch=async(url,init)=>{ seen=JSON.parse(init.body); return jsonResponse(200, geminiResp([{text:"{}"}])); };
  await m.generate({ messages:[{role:"user",content:"hi"}], responseFormat:{type:"json_schema", schema:{type:"object", properties:{a:{type:"string"}}}} });
  assert.equal(seen.generationConfig.responseJsonSchema.type, "object");
  assert.equal(seen.generationConfig.responseSchema, undefined);
  // tool definition uses parametersJsonSchema
  const tool={ name:"get_weather", inputSchema:{type:"object", properties:{location:{type:"string"}}} };
  await m.generate({ messages:[{role:"user",content:"hi"}], tools:[tool] });
  assert.equal(seen.tools[0].functionDeclarations[0].parametersJsonSchema.type, "object");
  assert.equal(seen.tools[0].functionDeclarations[0].parameters, undefined);
  // owned-field protection for new fields
  await assert.rejects(()=> m.generate({ messages:[{role:"user",content:"hi"}], providerOptions:{generationConfig:{responseJsonSchema:{}}} }), e=>e.name==="InvalidRequestError");
  await assert.rejects(()=> m.generate({ messages:[{role:"user",content:"hi"}], providerOptions:{generationConfig:{responseSchema:{}}} }), e=>e.name==="InvalidRequestError");
  globalThis.fetch=undefined;
});
