import { object } from "./response.js";
import { ConduitError } from "./errors.js";
import { decode, httpError, normalizeUsage } from "./openai-response.js";
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
): AsyncGenerator<StreamEvent> {
  let started = false;
  let id: string | undefined;
  let model: string | undefined;
  let finish: string | undefined;
  let usage: Usage | undefined;
  // One semantic text accumulator; decode() builds the same final response as generate().
  const message: { role: string; content: string | null; tool_calls?: unknown[] } = { role: "assistant", content: "" as string | null };
  let hasContent = false;
  const toolAccum = new Map<number, { id?: string; name?: string; arguments: string }>();
  for await (const data of dataEvents(body)) {
    if (data === "[DONE]") {
      if (!started || finish === undefined) protocol();
      if (!message.content && finish === "content_filter") message.content = null;
      else if (!hasContent && !toolAccum.size) {
        // No text content was ever observed; distinguish from explicitly observed empty string
        // Keep message.content as "" will be treated as observed empty by decode; to preserve absence, use undefined path
        // For empty-text fixture, hasContent is true (delta with ""), so this branch not taken
        // For true absence (e.g., tool-only with no text), hasContent is false and toolAccum empty -> should produce no text part?
        // But empty-text expects "" -> hasContent true, so we keep ""
        // If truly no content observed, decode would produce [] which matches "no content" vs "" distinction
        // To implement distinction, set content to undefined when never observed
        (message as Record<string, unknown>).content = undefined;
      }
      // Attach accumulated tool calls for final decode
      if (toolAccum.size) {
        const sorted = [...toolAccum.entries()].sort((a, b) => a[0] - b[0]);
        message.tool_calls = sorted.map(([idx, v]) => ({
          id: v.id ?? `call_${idx}`,
          type: "function",
          function: { name: v.name ?? "", arguments: v.arguments },
        }));
      }
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
    const toolDeltas: { index: number; id?: string; name?: string; argumentsDelta?: string }[] = [];
    if (value.choices.length === 0) {
      if (!started || !reportedUsage) protocol();
    } else {
      const choice: unknown = value.choices[0];
      if (!object(choice) || choice.index !== 0 || !object(choice.delta)) protocol();
      const delta = choice.delta as Record<string, unknown>;
      if (delta.role !== undefined && delta.role !== "assistant") protocol();
      if (delta.content != null && typeof delta.content !== "string") protocol();
      // Validate tool_calls delta if present
      if (delta.tool_calls !== undefined) {
        if (!Array.isArray(delta.tool_calls)) protocol();
        for (const tc of delta.tool_calls as unknown[]) {
          if (!object(tc)) protocol();
          const idx = (tc as Record<string, unknown>).index;
          if (typeof idx !== "number" || !Number.isInteger(idx) || idx < 0) protocol();
          const tid = (tc as Record<string, unknown>).id;
          const ttype = (tc as Record<string, unknown>).type;
          const tfn = (tc as Record<string, unknown>).function;
          if (tid !== undefined && typeof tid !== "string") protocol();
          if (ttype !== undefined && ttype !== "function") protocol();
          if (tfn !== undefined) {
            if (!object(tfn)) protocol();
            if (tfn.name !== undefined && typeof tfn.name !== "string") protocol();
            if (tfn.arguments !== undefined && typeof tfn.arguments !== "string") protocol();
          }
          // At least one of id/name/arguments must be present
          if (tid === undefined && tfn === undefined) protocol();
        }
      }
      if (Object.entries(delta).some(([key, data]) => !["role", "content", "tool_calls"].includes(key) && data != null)) protocol();
      const hasToolCalls = Array.isArray(delta.tool_calls) && (delta.tool_calls as unknown[]).length > 0;
      if (finish !== undefined && (delta.content || hasToolCalls || choice.finish_reason != null)) protocol();
      if (choice.finish_reason != null && typeof choice.finish_reason !== "string") protocol();
      if (typeof choice.finish_reason === "string") finish = choice.finish_reason;
      if (typeof delta.content === "string") {
        content = delta.content;
        hasContent = true;
      }
      if (Array.isArray(delta.tool_calls)) {
        for (const tc of delta.tool_calls as unknown[]) {
          const e = tc as Record<string, unknown>;
          const idx = e.index as number;
          const fn = (e.function ?? {}) as Record<string, unknown>;
          const cur = toolAccum.get(idx) ?? { arguments: "" };
          if (typeof e.id === "string") cur.id = e.id;
          if (typeof fn.name === "string") cur.name = fn.name;
          if (typeof fn.arguments === "string") cur.arguments += fn.arguments;
          toolAccum.set(idx, cur);
          toolDeltas.push({ index: idx, ...(typeof e.id === "string" && { id: e.id }), ...(typeof fn.name === "string" && { name: fn.name }), ...(typeof fn.arguments === "string" && fn.arguments.length && { argumentsDelta: fn.arguments }) });
        }
        // If tool call fragment contains no id/name/arguments, it's still a valid placeholder but we already validated
        // Ensure at least an entry exists for indexes that were referenced without payload
      }
    }
    if (!started) {
      started = true;
      yield { type: "start", ...(id !== undefined && { id: redact(id) }), ...(model !== undefined && { model: redact(model) }) };
    }
    message.content += content;
    if (content) yield { type: "text_delta", index: 0, text: content };
    for (const d of toolDeltas) {
      // Only emit if it carries data
      if (d.id !== undefined || d.name !== undefined || d.argumentsDelta !== undefined) {
        yield { type: "tool_call_delta", index: d.index, ...(d.id !== undefined && { id: d.id }), ...(d.name !== undefined && { name: d.name }), ...(d.argumentsDelta !== undefined && { argumentsDelta: d.argumentsDelta }) };
      }
    }
    if (reportedUsage) {
      usage = { ...usage, ...reportedUsage };
      yield { type: "usage", usage: { ...usage } };
    }
  }
  protocol();
}
