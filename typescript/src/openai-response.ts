import { object, textResponse } from "./response.js";
import { ConduitError, httpFailure } from "./errors.js";
import type { ErrorDetails } from "./errors.js";
import type { GenerationResponse, Usage } from "./types.js";

export function normalizeUsage(value: unknown): Usage | undefined {
  if (value !== undefined) {
    if (!object(value)) throw new ConduitError("ProtocolError", "Malformed or unsupported Chat Completions response.");
    const usage: Usage = {};
    for (const [wire, normalized] of [["prompt_tokens", "inputTokens"], ["completion_tokens", "outputTokens"], ["total_tokens", "totalTokens"]] as const) {
      const count = value[wire];
      if (count !== undefined) {
        if (typeof count !== "number" || !Number.isSafeInteger(count) || count < 0) throw new ConduitError("ProtocolError", "Malformed or unsupported Chat Completions response.");
        usage[normalized] = count;
      }
    }
    return usage;
  }
}

export function decode(value: unknown, requestId: string | undefined, redact: (text: string) => string): GenerationResponse {
  function protocol(): never { throw new ConduitError("ProtocolError", "Malformed or unsupported Chat Completions response."); }
  if (!object(value) || !Array.isArray(value.choices) || value.choices.length !== 1) protocol();
  const choice: unknown = value.choices[0];
  if (!object(choice) || !object(choice.message) || choice.message.role !== "assistant" || typeof choice.finish_reason !== "string") protocol();
  const message = choice.message as Record<string, unknown>;
  const hasToolCalls = Array.isArray(message.tool_calls);
  if (hasToolCalls) {
    if ((message.tool_calls as unknown[]).length === 0) protocol();
    for (const tc of message.tool_calls as unknown[]) {
      if (!object(tc) || typeof tc.id !== "string" || tc.type !== "function" || !object(tc.function) || typeof tc.function.name !== "string" || typeof tc.function.arguments !== "string") protocol();
      if (tc.function.arguments !== "") {
        try { JSON.parse(tc.function.arguments as string); } catch { protocol(); }
      }
    }
  } else if (Object.entries(message).some(([key, data]) => !["role", "content"].includes(key) && data != null && !(key === "tool_calls" && Array.isArray(data) && data.length === 0))) protocol();
  if (typeof message.content !== "string" && !(message.content === null && (choice.finish_reason === "content_filter" || hasToolCalls))) protocol();
  // When tool_calls present, content may be empty string or null
  if (hasToolCalls && typeof message.content === "string" && message.content !== "" && (message.content as string).length > 0) {
    // Allow text plus tool calls - text will be preserved as text part
  }
  if (value.id !== undefined && typeof value.id !== "string") protocol();
  if (value.model !== undefined && typeof value.model !== "string") protocol();
  const usage = normalizeUsage(value.usage);
  const finishReason = choice.finish_reason === "tool_calls" ? "tool_call"
    : hasToolCalls ? "tool_call"
    : choice.finish_reason === "stop" || choice.finish_reason === "length" || choice.finish_reason === "content_filter"
      ? choice.finish_reason : "other";
  const content: GenerationResponse["content"] = [];
  if (typeof message.content === "string") {
    if (message.content !== "" || !hasToolCalls) content.push({ type: "text", text: message.content });
  }
  if (hasToolCalls) {
    for (const tc of message.tool_calls as unknown[]) {
      const c = tc as Record<string, unknown>;
      const fn = c.function as Record<string, unknown>;
      let args: unknown;
      try { args = fn.arguments === "" ? {} : JSON.parse(fn.arguments as string); } catch { protocol(); }
      content.push({ type: "tool_call", id: c.id as string, name: fn.name as string, arguments: args as import("./types.js").JsonValue });
    }
  }
  return textResponse({
    ...(typeof value.id === "string" && { id: redact(value.id) }),
    ...(typeof value.model === "string" && { model: redact(value.model) }),
    content,
    finishReason: finishReason as GenerationResponse["finishReason"],
    ...(usage !== undefined && { usage }),
    providerMetadata: { finishReason: redact(choice.finish_reason as string), ...(requestId !== undefined && { requestId }) },
  });
}

export function httpError(status: number, body: string, requestId: string | undefined, redact: (text: string) => string): ConduitError {
  const details: NonNullable<ErrorDetails["providerDetails"]> = {};
  let modelNotFound = false;
  try {
    const value: unknown = JSON.parse(body);
    if (object(value) && object(value.error)) {
      modelNotFound = value.error.code === "model_not_found";
      for (const field of ["message", "type", "code"] as const) {
        if (typeof value.error[field] === "string") details[field] = redact(value.error[field]);
      }
    }
  } catch { /* HTTP status remains useful for empty, plain-text, or malformed bodies. */ }
  return httpFailure(status, details, requestId, modelNotFound);
}
