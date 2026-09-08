export interface ClientConfig {
  driver: "openai-compatible" | "ollama" | "anthropic";
  /** OpenAI-compatible API base (including /v1), or Ollama server base. */
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

export interface ToolDefinition {
  name: string;
  description?: string;
  inputSchema: JsonValue;
}

export type ToolChoice = "auto" | "none" | "required" | { name: string };

export interface ToolCallPart {
  type: "tool_call";
  id?: string;
  name: string;
  arguments: JsonValue;
}

export interface ToolResultPart {
  type: "tool_result";
  callId?: string;
  name?: string;
  content: string | readonly TextPart[];
}

export type ContentPart = TextPart | ToolCallPart | ToolResultPart;

export interface Message {
  role: "system" | "user" | "assistant" | "tool";
  content: string | readonly ContentPart[];
}

export type JsonValue = null | boolean | number | string | JsonValue[] | { [key: string]: JsonValue };

export type ResponseFormat =
  | { type: "text" }
  | { type: "json" }
  | { type: "json_schema"; schema: JsonValue };

export interface GenerationRequest {
  messages: readonly Message[];
  maxOutputTokens?: number;
  temperature?: number;
  topP?: number;
  stop?: readonly string[];
  tools?: readonly ToolDefinition[];
  toolChoice?: ToolChoice;
  responseFormat?: ResponseFormat;
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
  content: (TextPart | ToolCallPart)[];
  readonly text: string;
  readonly toolCalls: ToolCallPart[];
  finishReason: "stop" | "length" | "tool_call" | "content_filter" | "other";
  usage?: Usage;
  providerMetadata: { finishReason?: string; requestId?: string; [key: string]: JsonValue | undefined };
}

export interface ModelInfo {
  id: string;
  name?: string;
  providerMetadata?: Record<string, JsonValue>;
}

export interface ListModelsOptions {
  timeout?: number;
  signal?: AbortSignal;
}

export interface Client {
  model(id: string): Model;
  listModels(options?: ListModelsOptions): Promise<ModelInfo[]>;
}

export type StreamEvent =
  | { type: "start"; id?: string; model?: string }
  | { type: "text_delta"; index: 0; text: string }
  | { type: "tool_call_delta"; index: number; id?: string; name?: string; argumentsDelta?: string }
  | { type: "usage"; usage: Usage }
  | { type: "done"; response: GenerationResponse };

export interface Model {
  generate(request: GenerationRequest): Promise<GenerationResponse>;
  stream(request: GenerationRequest): AsyncGenerator<StreamEvent>;
}
