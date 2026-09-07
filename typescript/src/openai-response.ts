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
  const message = choice.message;
  if (Object.entries(message).some(([key, data]) => !["role", "content"].includes(key) && data != null && !(key === "tool_calls" && Array.isArray(data) && data.length === 0))) protocol();
  if (typeof message.content !== "string" && !(message.content === null && choice.finish_reason === "content_filter")) protocol();
  if (value.id !== undefined && typeof value.id !== "string") protocol();
  if (value.model !== undefined && typeof value.model !== "string") protocol();
  const usage = normalizeUsage(value.usage);
  const finishReason = choice.finish_reason === "tool_calls" ? "tool_call"
    : choice.finish_reason === "stop" || choice.finish_reason === "length" || choice.finish_reason === "content_filter"
      ? choice.finish_reason : "other";
  return textResponse({
    ...(typeof value.id === "string" && { id: redact(value.id) }),
    ...(typeof value.model === "string" && { model: redact(value.model) }),
    content: typeof message.content === "string" ? [{ type: "text", text: message.content }] : [],
    finishReason,
    ...(usage !== undefined && { usage }),
    providerMetadata: { finishReason: redact(choice.finish_reason), ...(requestId !== undefined && { requestId }) },
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
