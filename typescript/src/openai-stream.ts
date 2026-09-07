import { ConduitError } from "./errors.js";
import { decode, httpError, normalizeUsage, object } from "./openai-response.js";
import type { StreamEvent, Usage } from "./types.js";

function protocol(): never {
  throw new ConduitError("ProtocolError", "Malformed or incomplete Chat Completions stream.");
}

// Internal Chat Completions SSE framing, not an EventSource/reconnection API.
async function* dataEvents(body: ReadableStream<Uint8Array>): AsyncGenerator<string> {
  const reader = body.getReader();
  const decoder = new TextDecoder("utf-8", { fatal: true });
  let line = "";
  let data: string[] = [];
  let skipLF = false;
  try {
    while (true) {
      const { value, done } = await reader.read();
      let text: string;
      try { text = decoder.decode(value, { stream: !done }); } catch { protocol(); }
      for (const char of text) {
        if (skipLF && char === "\n") { skipLF = false; continue; }
        skipLF = char === "\r";
        if (char !== "\r" && char !== "\n") { line += char; continue; }
        if (!line) {
          if (data.length) yield data.join("\n");
          data = [];
        } else {
          const colon = line.indexOf(":");
          const field = colon < 0 ? line : line.slice(0, colon);
          let value = colon < 0 ? "" : line.slice(colon + 1);
          if (value.startsWith(" ")) value = value.slice(1);
          if (field === "data") data.push(value);
        }
        line = "";
      }
      // SSE does not dispatch unterminated events at EOF.
      if (done) return;
    }
  } finally {
    try { await reader.cancel(); } catch { /* Preserve the operation's primary error. */ }
    reader.releaseLock();
  }
}

export async function* openaiStream(
  body: ReadableStream<Uint8Array>,
  requestId: string | undefined,
  redact: (text: string) => string,
  redactText: (text: string, flush?: boolean) => string,
): AsyncGenerator<StreamEvent> {
  let started = false;
  let id: string | undefined;
  let model: string | undefined;
  let finish: string | undefined;
  let usage: Usage | undefined;
  // One semantic text accumulator; decode() builds the same final response as generate().
  const message = { role: "assistant", content: "" as string | null };
  for await (const data of dataEvents(body)) {
    if (data === "[DONE]") {
      if (!started || finish === undefined) protocol();
      const text = redactText("", true);
      if (text) yield { type: "text_delta", index: 0, text };
      if (!message.content && finish === "content_filter") message.content = null;
      const response = decode({
        id, model,
        choices: [{ message, finish_reason: finish }],
        ...(usage && { usage: { prompt_tokens: usage.inputTokens, completion_tokens: usage.outputTokens, total_tokens: usage.totalTokens } }),
      }, requestId, redact);
      yield { type: "done", response };
      return;
    }
    let value: unknown;
    try { value = JSON.parse(data); } catch { protocol(); }
    if (!object(value)) protocol();
    if (object(value.error)) throw httpError(200, data, requestId, redact);
    if (!Array.isArray(value.choices) || value.choices.length > 1) protocol();
    for (const field of ["id", "model"] as const) {
      const previous = field === "id" ? id : model;
      if (value[field] !== undefined && (typeof value[field] !== "string" || (previous !== undefined && previous !== value[field]))) protocol();
    }
    if (typeof value.id === "string") id = value.id;
    if (typeof value.model === "string") model = value.model;
    const reportedUsage = value.usage == null ? undefined : normalizeUsage(value.usage);
    let content = "";
    if (value.choices.length === 0) {
      if (!started || !reportedUsage) protocol();
    } else {
      const choice: unknown = value.choices[0];
      if (!object(choice) || choice.index !== 0 || !object(choice.delta)) protocol();
      const delta = choice.delta;
      if (delta.role !== undefined && delta.role !== "assistant") protocol();
      if (delta.content != null && typeof delta.content !== "string") protocol();
      if (Object.entries(delta).some(([key, data]) => !["role", "content"].includes(key) && data != null && !(key === "tool_calls" && Array.isArray(data) && !data.length))) protocol();
      if (finish !== undefined && (delta.content || choice.finish_reason != null)) protocol();
      if (choice.finish_reason != null && typeof choice.finish_reason !== "string") protocol();
      if (typeof choice.finish_reason === "string") finish = choice.finish_reason;
      if (typeof delta.content === "string") content = delta.content;
    }
    if (!started) {
      started = true;
      yield { type: "start", ...(id !== undefined && { id: redact(id) }), ...(model !== undefined && { model: redact(model) }) };
    }
    message.content += content;
    const text = redactText(content);
    if (text) yield { type: "text_delta", index: 0, text };
    if (reportedUsage) {
      usage = { ...usage, ...reportedUsage };
      yield { type: "usage", usage: { ...usage } };
    }
  }
  protocol();
}
