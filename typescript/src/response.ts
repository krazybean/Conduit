import type { GenerationResponse } from "./types.js";

export function object(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

export function textResponse(fields: Omit<GenerationResponse, "text">): GenerationResponse {
  return { ...fields, get text() { return this.content.map(part => part.text).join(""); } };
}
