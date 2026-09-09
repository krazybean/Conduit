import { ConduitError, httpFailure } from "./errors.js";
import { object, textResponse } from "./response.js";
import type { GenerationRequest, GenerationResponse, StreamEvent, Usage } from "./types.js";

export function ollamaRequest(model: string, messages: { role: unknown; content: unknown[] }[], request: GenerationRequest, stream: boolean, wireTools?: unknown, _wireToolChoice?: unknown, wireFormat?: unknown): string {
  const native = request.providerOptions ?? {};
  const rawOptions = (native as Record<string, unknown>).options;
  if ((rawOptions !== undefined && !object(rawOptions)) ||
      ["model", "messages", "stream", "tools", "format", "tool_choice"].some(key => Object.hasOwn(native as object, key)) ||
      (object(rawOptions) && ["num_predict", "temperature", "top_p", "stop"].some(key => Object.hasOwn(rawOptions as object, key)))) {
    throw new ConduitError("InvalidRequestError", "Invalid Ollama options or conflict with a Conduit-owned field.");
  }
  const options = (object(rawOptions) ? rawOptions : {}) as Record<string, unknown>;
  const mapped = { ...options, num_predict: request.maxOutputTokens, temperature: request.temperature, top_p: request.topP, stop: request.stop === undefined ? undefined : Array.from(request.stop) };
  const wireMessages = messages.map(message => {
    const role = message.role as string;
    const parts = message.content as unknown[];
    // Extract text, tool_calls, tool_result
    const texts: string[] = [];
    const toolCalls: unknown[] = [];
    let toolResult: { content: string; tool_name?: string } | null = null;
    for (const part of parts) {
      const p = part as Record<string, unknown>;
      if (p.type === "text") texts.push(p.text as string);
      else if (p.type === "tool_call") {
        toolCalls.push({ function: { name: p.name, arguments: p.arguments } });
      } else if (p.type === "tool_result") {
        const inner = typeof p.content === "string" ? p.content as string : ((p.content as unknown[]).map((q: unknown) => (q as { text: string }).text).join(""));
        toolResult = { content: inner, ...(p.name !== undefined ? { tool_name: p.name as string } : p.callId !== undefined ? { tool_name: p.callId as string } : {}) };
      }
    }
    if (role === "tool") {
      if (!toolResult) throw new ConduitError("InvalidRequestError", "Tool message must contain tool_result.");
      return { role, content: toolResult.content, ...(toolResult.tool_name !== undefined && { tool_name: toolResult.tool_name }) };
    }
    if (toolCalls.length) {
      return { role, content: texts.join(""), tool_calls: toolCalls };
    }
    return { role, content: texts.join("") };
  });
  return JSON.stringify({ ...native, model, messages: wireMessages, stream,
    ...(wireTools !== undefined && { tools: wireTools }),
    ...(wireFormat !== undefined && { format: wireFormat }),
    ...(Object.values(mapped).some(value => value !== undefined) && { options: mapped }) });
}

function protocol(): never { throw new ConduitError("ProtocolError", "Malformed or unsupported Ollama chat response."); }

export function ollamaError(status: number, body: string, requestId: string | undefined, redact: (text: string) => string, model?: string): ConduitError {
  let message: string | undefined;
  try { const value: unknown = JSON.parse(body); if (object(value) && typeof value.error === "string") message = value.error; } catch { /* Preserve HTTP status for non-JSON bodies. */ }
  const missing = model !== undefined && [`model '${model}' not found`, `model "${model}" not found`, `model "${model}" not found, try pulling it first`].includes(message ?? "");
  return httpFailure(status, message === undefined ? {} : { message: redact(message) }, requestId, missing);
}

function record(value: unknown, allowTerminalWithoutMessage = false): Record<string, unknown> {
  if (!object(value) || typeof value.done !== "boolean") protocol();
  if (value.model !== undefined && typeof value.model !== "string") protocol();
  if (value.done_reason !== undefined && typeof value.done_reason !== "string") protocol();
  if (value.message === undefined && value.done && allowTerminalWithoutMessage) return value;
  if (!object(value.message) || value.message.role !== "assistant" || typeof value.message.content !== "string") protocol();
  const msg = value.message as Record<string, unknown>;
  if (msg.tool_calls !== undefined) {
    if (!Array.isArray(msg.tool_calls) || (msg.tool_calls as unknown[]).length === 0) protocol();
    for (const tc of msg.tool_calls as unknown[]) {
      if (!object(tc)) protocol();
      const fn = (tc as Record<string, unknown>).function;
      if (!object(fn) || typeof fn.name !== "string" || !(fn.name as string).trim()) protocol();
      const args = fn.arguments;
      if (args !== undefined) {
        try { JSON.stringify(args); } catch { protocol(); }
      }
    }
  }
  if (Object.entries(value.message).some(([key, data]) => !["role", "content", "tool_calls"].includes(key) && data != null &&
      !(key === "thinking" && data === "") && !(["images"].includes(key) && Array.isArray(data) && data.length === 0))) protocol();
  return value;
}

function usageOf(value: Record<string, unknown>): Usage | undefined {
  const usage: Usage = {};
  for (const [wire, normalized] of [["prompt_eval_count", "inputTokens"], ["eval_count", "outputTokens"]] as const) {
    const count = value[wire];
    if (count !== undefined) {
      if (typeof count !== "number" || !Number.isSafeInteger(count) || count < 0) protocol();
      usage[normalized] = count;
    }
  }
  return Object.keys(usage).length ? usage : undefined;
}

export function ollamaResponse(input: unknown, requestId: string | undefined, redact: (text: string) => string): GenerationResponse {
  if (object(input) && typeof input.error === "string") throw ollamaError(200, JSON.stringify(input), requestId, redact);
  const value = record(input);
  if (!value.done) protocol();
  const metadata: GenerationResponse["providerMetadata"] = {};
  if (requestId !== undefined) metadata.requestId = requestId;
  if (typeof value.done_reason === "string") metadata.finishReason = redact(value.done_reason);
  if (value.created_at !== undefined) {
    if (typeof value.created_at !== "string") protocol();
    metadata.created_at = redact(value.created_at);
  }
  for (const key of ["total_duration", "load_duration", "prompt_eval_duration", "eval_duration"]) {
    const duration = value[key];
    if (duration !== undefined) {
      if (typeof duration !== "number" || !Number.isSafeInteger(duration) || duration < 0) protocol();
      metadata[key] = duration;
    }
  }
  const usage = usageOf(value);
  const msg = value.message as Record<string, unknown>;
  const hasToolCalls = Array.isArray(msg.tool_calls) && (msg.tool_calls as unknown[]).length > 0;
  const content: GenerationResponse["content"] = [];
  const text = (msg.content as string);
  if (typeof text === "string" && (text !== "" || !hasToolCalls)) content.push({ type: "text", text });
  if (hasToolCalls) {
    for (const tc of msg.tool_calls as unknown[]) {
      const c = tc as Record<string, unknown>;
      const fn = c.function as Record<string, unknown>;
      const args = fn.arguments as unknown;
      // Ollama arguments is already object; ensure JSON-compatible
      let normalizedArgs: import("./types.js").JsonValue;
      if (args === undefined) normalizedArgs = {};
      else {
        try { JSON.stringify(args); normalizedArgs = args as import("./types.js").JsonValue; } catch { protocol(); }
      }
      content.push({ type: "tool_call", name: fn.name as string, arguments: normalizedArgs as import("./types.js").JsonValue });
    }
  }
  const finishReason = hasToolCalls ? "tool_call" : value.done_reason === "stop" || value.done_reason === "length" ? value.done_reason : "other";
  return textResponse({
    ...(typeof value.model === "string" && { model: redact(value.model) }),
    content,
    finishReason,
    ...(usage && { usage }), providerMetadata: metadata,
  });
}

// Native Ollama framing is NDJSON, independent of the OpenAI SSE parser.
// NOTE: incremental line buffering; unbounded single line grows until delimiter/cancellation.
async function* records(body: ReadableStream<Uint8Array>): AsyncGenerator<unknown> {
  const reader = body.getReader();
  const decoder = new TextDecoder("utf-8", { fatal: true });
  let line = "";
  const parse = (effective: string): unknown => { try { return JSON.parse(effective); } catch { protocol(); } };
  try {
    while (true) {
      const { value, done } = await reader.read();
      let text: string;
      try { text = decoder.decode(value, { stream: !done }); } catch { protocol(); }
      for (const char of text) {
        if (char === "\n") {
          const effective = line.endsWith("\r") ? line.slice(0, -1) : line;
          if (effective.trim()) yield parse(effective);
          line = "";
        } else line += char;
      }
      if (done) {
        const effective = line.endsWith("\r") ? line.slice(0, -1) : line;
        if (effective.trim()) yield parse(effective);
        return;
      }
    }
  } finally {
    try { await reader.cancel(); } catch { /* Keep the primary failure. */ }
    reader.releaseLock();
  }
}

// NOTE: Ollama streams complete tool-call objects per NDJSON line, not token-level argument fragments; each tool_call_delta represents a newly observed complete call.
export async function* ollamaStream(body: ReadableStream<Uint8Array>, requestId: string | undefined, redact: (text: string) => string): AsyncGenerator<StreamEvent> {
  let started = false;
  let model: string | undefined;
  const content = { role: "assistant", content: "" };
  let usage: Usage | undefined;
  const toolCalls: { name: string; arguments: unknown }[] = [];
  let toolEmitted = 0;
  for await (const input of records(body)) {
    if (object(input) && typeof input.error === "string") throw ollamaError(200, JSON.stringify(input), requestId, redact);
    const value = record(input, started);
    if (typeof value.model === "string") {
      if (model !== undefined && model !== value.model) protocol();
      model = value.model;
    }
    const reported = usageOf(value);
    if (reported) usage = { ...usage, ...reported };
    const msg = value.message as Record<string, unknown>;
    const text = typeof msg.content === "string" ? msg.content as string : "";
    content.content += text;
    // Accumulate tool calls from this record
    const incomingToolCalls = Array.isArray(msg.tool_calls) ? msg.tool_calls as unknown[] : [];
    for (const tc of incomingToolCalls) {
      const c = tc as Record<string, unknown>;
      const fn = c.function as Record<string, unknown>;
      toolCalls.push({ name: fn.name as string, arguments: fn.arguments });
    }
    // Validate final metadata before yielding a successful terminal record's events.
    // For Ollama, final response should include aggregated tool calls if any
    let response: import("./types.js").GenerationResponse | undefined;
    if (value.done) {
      const finalMessage: Record<string, unknown> = { ...content };
      if (toolCalls.length) (finalMessage as Record<string, unknown>).tool_calls = toolCalls.map(tc => ({ function: { name: tc.name, arguments: tc.arguments } }));
      response = ollamaResponse({ ...value, model, message: finalMessage,
        prompt_eval_count: usage?.inputTokens, eval_count: usage?.outputTokens }, requestId, redact);
    }
    if (!started) {
      started = true;
      yield { type: "start", ...(model !== undefined && { model: redact(model) }) };
    }
    if (text) yield { type: "text_delta", index: 0, text };
    // Emit tool deltas for newly arrived tool calls
    for (let i = toolEmitted; i < toolCalls.length; i++) {
      const tc = toolCalls[i] as { name: string; arguments: unknown };
      const argsStr = typeof tc.arguments === "string" ? tc.arguments as string : JSON.stringify(tc.arguments);
      yield { type: "tool_call_delta", index: i, name: tc.name, ...(argsStr && argsStr !== "{}" && { argumentsDelta: argsStr }) };
    }
    toolEmitted = toolCalls.length;
    if (reported) yield { type: "usage", usage: { ...usage } };
    if (response) { yield { type: "done", response }; return; }
  }
  protocol();
}
