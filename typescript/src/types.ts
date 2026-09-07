export interface ClientConfig {
  driver: "openai-compatible";
  /** API base URL, including any version prefix (e.g. http://localhost:1234/v1). */
  endpoint: string;
  credentials?: string;
  headers?: Record<string, string>;
  /** Entire operation deadline in milliseconds; omitted means no Conduit deadline. */
  timeout?: number;
}

export interface TextPart {
  type: "text";
  text: string;
}

export interface Message {
  role: "system" | "user" | "assistant";
  content: string | readonly TextPart[];
}

export type JsonValue = null | boolean | number | string | JsonValue[] | { [key: string]: JsonValue };

export interface GenerationRequest {
  messages: readonly Message[];
  maxOutputTokens?: number;
  temperature?: number;
  topP?: number;
  stop?: readonly string[];
  providerOptions?: Record<string, JsonValue>;
  signal?: AbortSignal;
  timeout?: number;
}

export interface Usage {
  inputTokens?: number;
  outputTokens?: number;
  totalTokens?: number;
}

export interface GenerationResponse {
  id?: string;
  model?: string;
  content: TextPart[];
  readonly text: string;
  finishReason: "stop" | "length" | "tool_call" | "content_filter" | "other";
  usage?: Usage;
  providerMetadata: { finishReason: string; requestId?: string };
}

export interface Client {
  model(id: string): Model;
}

export type StreamEvent =
  | { type: "start"; id?: string; model?: string }
  | { type: "text_delta"; index: 0; text: string }
  | { type: "usage"; usage: Usage }
  | { type: "done"; response: GenerationResponse };

export interface Model {
  generate(request: GenerationRequest): Promise<GenerationResponse>;
  stream(request: GenerationRequest): AsyncGenerator<StreamEvent>;
}
