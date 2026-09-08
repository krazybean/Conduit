import type { GenerationResponse, ToolCallPart } from "./types.js";

export function object(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

export function textResponse(fields: Omit<GenerationResponse, "text" | "toolCalls">): GenerationResponse {
  return {
    ...fields,
    get text() { return this.content.filter((part): part is Extract<typeof part, { type: "text" }> => part.type === "text").map(part => part.text).join(""); },
    get toolCalls() { return this.content.filter((part): part is ToolCallPart => part.type === "tool_call"); },
  };
}
