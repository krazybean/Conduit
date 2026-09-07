import { ConduitError, httpFailure } from "./errors.js";
import { object, textResponse } from "./response.js";
import type { GenerationRequest, GenerationResponse, StreamEvent, Usage } from "./types.js";

export function ollamaRequest(model: string, messages: { role: unknown; content: { text: string }[] }[], request: GenerationRequest, stream: boolean): string {
  const native = request.providerOptions ?? {};
  const rawOptions = (native as Record<string, unknown>).options;
  if ((rawOptions !== undefined && !object(rawOptions)) ||
      ["model", "messages", "stream", "tools", "format"].some(key => Object.hasOwn(native as object, key)) ||
      (object(rawOptions) && ["num_predict", "temperature", "top_p", "stop"].some(key => Object.hasOwn(rawOptions as object, key)))) {
    throw new ConduitError("InvalidRequestError", "Invalid Ollama options or conflict with a Conduit-owned field.");
  }
  const options = (object(rawOptions) ? rawOptions : {}) as Record<string, unknown>;
  const mapped = { ...options, num_predict: request.maxOutputTokens, temperature: request.temperature, top_p: request.topP, stop: request.stop === undefined ? undefined : Array.from(request.stop) };
  return JSON.stringify({ ...native, model, messages: messages.map(message => ({ role: message.role, content: message.content.map(part => part.text).join("") })), stream,
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
  if (Object.entries(value.message).some(([key, data]) => !["role", "content"].includes(key) && data != null &&
      !(key === "thinking" && data === "") && !(["tool_calls", "images"].includes(key) && Array.isArray(data) && data.length === 0))) protocol();
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
  return textResponse({
    ...(typeof value.model === "string" && { model: redact(value.model) }),
    content: [{ type: "text", text: (value.message as { content: string }).content }],
    finishReason: value.done_reason === "stop" || value.done_reason === "length" ? value.done_reason : "other",
    ...(usage && { usage }), providerMetadata: metadata,
  });
}

// Native Ollama framing is NDJSON, independent of the OpenAI SSE parser.
// ponytail: incremental line buffering; unbounded single line grows until delimiter/cancellation.
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

export async function* ollamaStream(body: ReadableStream<Uint8Array>, requestId: string | undefined, redact: (text: string) => string): AsyncGenerator<StreamEvent> {
  let started = false;
  let model: string | undefined;
  const content = { role: "assistant", content: "" };
  let usage: Usage | undefined;
  for await (const input of records(body)) {
    if (object(input) && typeof input.error === "string") throw ollamaError(200, JSON.stringify(input), requestId, redact);
    const value = record(input, started);
    if (typeof value.model === "string") {
      if (model !== undefined && model !== value.model) protocol();
      model = value.model;
    }
    const reported = usageOf(value);
    if (reported) usage = { ...usage, ...reported };
    const text = object(value.message) ? value.message.content as string : "";
    content.content += text;
    // Validate final metadata before yielding a successful terminal record's events.
    const response = value.done ? ollamaResponse({ ...value, model, message: content,
      prompt_eval_count: usage?.inputTokens, eval_count: usage?.outputTokens }, requestId, redact) : undefined;
    if (!started) {
      started = true;
      yield { type: "start", ...(model !== undefined && { model: redact(model) }) };
    }
    if (text) yield { type: "text_delta", index: 0, text };
    if (reported) yield { type: "usage", usage: { ...usage } };
    if (response) { yield { type: "done", response }; return; }
  }
  protocol();
}
