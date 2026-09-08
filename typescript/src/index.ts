import { decode, httpError } from "./openai-response.js";
import { object } from "./response.js";
import { ollamaRequest, ollamaResponse, ollamaError, ollamaStream } from "./ollama.js";
import { openaiStream } from "./openai-stream.js";
import { anthropicRequest, anthropicResponse, anthropicError, anthropicStream } from "./anthropic.js";
import { geminiRequest, encodeGeminiTools, encodeGeminiToolChoice, encodeGeminiFormat, geminiResponse, geminiError, geminiStream } from "./gemini.js";
import { ConduitError } from "./errors.js";
import type { Client, ClientConfig, GenerationRequest, GenerationResponse, JsonValue, ListModelsOptions, Model, ModelInfo, StreamEvent } from "./types.js";

export { ConduitError } from "./errors.js";
export type { ErrorCode, ErrorDetails } from "./errors.js";
export type { Client, ClientConfig, ContentPart, GenerationRequest, GenerationResponse, JsonValue, ListModelsOptions, Message, Model, ModelInfo, ResponseFormat, StreamEvent, TextPart, ToolCallPart, ToolChoice, ToolDefinition, ToolResultPart, Usage } from "./types.js";

const ownedFields = new Set([
  "model", "messages", "stream", "stream_options", "max_tokens", "max_completion_tokens",
  "temperature", "top_p", "stop", "tools", "tool_choice", "functions", "function_call",
  "response_format", "n", "modalities",
]);
const protectedHeaders = new Set([
  "authorization", "proxy-authorization", "cookie", "host", "content-type", "content-length",
  "connection", "transfer-encoding", "upgrade", "trailer", "te", "keep-alive",
  "x-api-key", "anthropic-version", "x-goog-api-key",
]);
const requestFields = new Set([
  "messages", "maxOutputTokens", "temperature", "topP", "stop", "tools", "toolChoice", "responseFormat", "providerOptions", "signal", "timeout",
]);
const unsupportedFields = new Set(["stream", "reasoning", "vision"]);

function invalid(message: string): never {
  throw new ConduitError("InvalidRequestError", message);
}

function keys(value: object, allowed: Set<string>): void {
  if (Reflect.ownKeys(value).some(key => typeof key !== "string" || !allowed.has(key))) {
    invalid("Unrecognized field; use providerOptions for native request settings.");
  }
}

function timeoutValue(value: unknown): void {
  if (value !== undefined && (!Number.isInteger(value) || (value as number) < 1 || (value as number) > 2147483647)) {
    invalid("timeout must be an integer from 1 through 2147483647 milliseconds.");
  }
}

// Validate JSON rather than silently dropping undefined/functions or coercing nonfinite numbers.
function json(value: unknown, parents = new Set<object>()): void {
  if (value === null || typeof value === "string" || typeof value === "boolean") return;
  if (typeof value === "number" && Number.isFinite(value)) return;
  if (typeof value !== "object" || value === null || parents.has(value)) invalid("providerOptions must contain acyclic JSON data.");
  const prototype = Object.getPrototypeOf(value);
  if (!Array.isArray(value) && prototype !== Object.prototype && prototype !== null) invalid("providerOptions must contain plain JSON data.");
  parents.add(value);
  if (Array.isArray(value)) {
    for (const item of value) json(item, parents);
    if (Reflect.ownKeys(value).length !== value.length + 1) invalid("Invalid JSON array in providerOptions.");
  } else {
    for (const key of Reflect.ownKeys(value)) {
      const descriptor = Object.getOwnPropertyDescriptor(value, key)!;
      if (typeof key !== "string" || !descriptor.enumerable || !('value' in descriptor)) invalid("providerOptions must contain plain JSON data.");
      json(descriptor.value, parents);
    }
  }
  parents.delete(value);
}

function validateTools(tools: unknown): void {
  if (tools === undefined) return;
  if (!Array.isArray(tools) || tools.length === 0) invalid("tools must be a nonempty array.");
  const names = new Set<string>();
  for (const tool of tools) {
    if (!object(tool)) invalid("Each tool must be an object.");
    keys(tool, new Set(["name", "description", "inputSchema"]));
    if (typeof tool.name !== "string" || !tool.name.trim()) invalid("Tool name must be a nonempty string.");
    if (names.has(tool.name)) invalid("Tool names must be unique.");
    names.add(tool.name);
    if (tool.description !== undefined && typeof tool.description !== "string") invalid("Tool description must be a string.");
    if (!object(tool.inputSchema) && !Array.isArray(tool.inputSchema) && typeof tool.inputSchema !== "boolean") {
      // JSON Schema must be JSON-compatible; allow any JSON value but require acyclic
      try { json(tool.inputSchema); } catch { invalid("Tool inputSchema must be JSON-compatible."); }
    } else {
      try { json(tool.inputSchema); } catch (error) { if (error instanceof ConduitError) throw error; invalid("Tool inputSchema must be JSON-compatible."); }
    }
  }
}

function validateToolChoice(choice: unknown, tools: readonly unknown[] | undefined): void {
  if (choice === undefined) return;
  if (typeof choice === "string") {
    if (!["auto", "none", "required"].includes(choice)) {
      // Allow named tool as string shorthand
      if (!choice.trim()) invalid("toolChoice name must be a nonempty string.");
      if (!tools || !(tools as readonly { name: string }[]).some(t => t.name === choice)) invalid("toolChoice name must match a supplied tool.");
      return;
    }
    return;
  }
  if (!object(choice)) invalid("toolChoice must be \"auto\", \"none\", \"required\", or { name }.");
  keys(choice, new Set(["name"]));
  if (typeof choice.name !== "string" || !choice.name.trim()) invalid("toolChoice name must be a nonempty string.");
  if (!tools || !(tools as readonly { name: string }[]).some(t => t.name === choice.name)) invalid("toolChoice name must match a supplied tool.");
}

function encodeTools(tools: readonly import("./types.js").ToolDefinition[] | undefined): unknown[] | undefined {
  if (tools === undefined) return undefined;
  return tools.map(t => ({
    type: "function",
    function: {
      name: t.name,
      ...(t.description !== undefined && { description: t.description }),
      parameters: t.inputSchema,
    },
  }));
}

function encodeToolChoice(choice: unknown, driver?: ClientConfig["driver"]): unknown {
  if (choice === undefined) return undefined;
  if (driver === "anthropic") {
    if (choice === "auto") return { type: "auto" };
    if (choice === "none") return { type: "none" };
    if (choice === "required") return { type: "any" };
    if (typeof choice === "string") return { type: "tool", name: choice };
    if (object(choice) && typeof choice.name === "string") return { type: "tool", name: choice.name };
    return choice;
  }
  if (typeof choice === "string") {
    if (["auto", "none", "required"].includes(choice)) return choice;
    return { type: "function", function: { name: choice } };
  }
  if (object(choice) && typeof choice.name === "string") {
    return { type: "function", function: { name: choice.name } };
  }
  return choice;
}

function encodeToolsAnthropic(tools: readonly import("./types.js").ToolDefinition[] | undefined): unknown[] | undefined {
  if (tools === undefined) return undefined;
  return tools.map(t => ({
    name: t.name,
    ...(t.description !== undefined && { description: t.description }),
    input_schema: t.inputSchema,
  }));
}

function validateResponseFormat(value: unknown): void {
  if (value === undefined) return;
  if (!object(value)) invalid("responseFormat must be an object.");
  if (typeof value.type !== "string") invalid("responseFormat.type must be \"text\", \"json\", or \"json_schema\".");
  if (!["text", "json", "json_schema"].includes(value.type)) invalid("responseFormat.type must be \"text\", \"json\", or \"json_schema\".");
  if (value.type === "text" || value.type === "json") {
    keys(value, new Set(["type"]));
    return;
  }
  // json_schema
  keys(value, new Set(["type", "schema"]));
  try { json(value.schema); } catch { invalid("responseFormat.schema must be JSON-compatible."); }
}

function encodeResponseFormat(value: unknown, driver: ClientConfig["driver"]): unknown {
  if (value === undefined) return undefined;
  const fmt = value as Record<string, unknown>;
  if (fmt.type === "text") {
    // Text is provider default; omit wire field to avoid unnecessary restriction.
    // Some OpenAI endpoints accept {type:"text"} but omission is more compatible.
    return undefined;
  }
  if (fmt.type === "json") {
    return driver === "ollama" ? "json" : { type: "json_object" };
  }
  // json_schema
  const schema = fmt.schema;
  if (driver === "ollama") return schema;
  return {
    type: "json_schema",
    json_schema: {
      name: "response",
      strict: true,
      schema,
    },
  };
}

function normalizeRequest(req: GenerationRequest | string): GenerationRequest {
  if (typeof req === "string") return { messages: [{ role: "user", content: req }] } as GenerationRequest;
  return req;
}
function encode(model: string, request: GenerationRequest | string, streaming: boolean, driver: ClientConfig["driver"]): string {
  const normalized = normalizeRequest(request as GenerationRequest | string);
  if (!object(normalized)) invalid("A generation request is required.");
  if (Object.keys(normalized).some(key => unsupportedFields.has(key))) {
    throw new ConduitError("UnsupportedCapabilityError", streaming ? "Only text streaming is implemented." : "Only non-streaming text generation is implemented.");
  }
  keys(request, requestFields);
  if (!Array.isArray(normalized.messages) || normalized.messages.length === 0) invalid("messages must be a nonempty array.");
  validateTools(normalized.tools);
  validateToolChoice(normalized.toolChoice, normalized.tools as readonly unknown[] | undefined);
  validateResponseFormat(normalized.responseFormat);
  const messages = Array.from(normalized.messages, message => {
    if (!object(message)) invalid("Each message must be an object.");
    keys(message, new Set(["role", "content"]));
    const role = message.role as string;
    if (!["system", "user", "assistant", "tool"].includes(role)) invalid("Invalid message role.");
    const rawParts = typeof message.content === "string" ? [{ type: "text", text: message.content }] : message.content;
    if (!Array.isArray(rawParts)) invalid("Message content must be a string or text-part array.");
    if (rawParts.length === 0) invalid("Message content must be nonempty.");
    const content: unknown[] = [];
    let hasToolCall = false;
    let hasToolResult = false;
    let hasText = false;
    for (const part of rawParts) {
      if (!object(part)) invalid("Invalid content part.");
      const type = part.type as string;
      if (type === "text") {
        keys(part, new Set(["type", "text"]));
        if (typeof part.text !== "string") invalid("Invalid text content part.");
        hasText = true;
        content.push({ type: "text", text: part.text });
      } else if (type === "tool_call") {
        keys(part, new Set(["type", "id", "name", "arguments"]));
        if (typeof part.name !== "string" || !part.name.trim()) invalid("Tool call name must be a nonempty string.");
        if (part.id !== undefined && typeof part.id !== "string") invalid("Tool call id must be a string.");
        try { json(part.arguments); } catch { invalid("Tool call arguments must be JSON-compatible."); }
        hasToolCall = true;
        content.push({ type: "tool_call", ...(part.id !== undefined && { id: part.id }), name: part.name, arguments: part.arguments });
      } else if (type === "tool_result") {
        keys(part, new Set(["type", "callId", "name", "content"]));
        if (part.callId !== undefined && typeof part.callId !== "string") invalid("Tool result callId must be a string.");
        if (part.name !== undefined && typeof part.name !== "string") invalid("Tool result name must be a string.");
        if (typeof part.content !== "string" && !Array.isArray(part.content)) invalid("Tool result content must be a string or text-part array.");
        const inner = typeof part.content === "string" ? [{ type: "text", text: part.content }] : part.content as unknown[];
        if (!Array.isArray(inner)) invalid("Tool result content must be a string or text-part array.");
        // Validate inner text parts
        for (const p of inner) {
          if (!object(p)) invalid("Invalid tool result content part.");
          keys(p, new Set(["type", "text"]));
          if (p.type !== "text" || typeof p.text !== "string") invalid("Tool result content must be text.");
        }
        hasToolResult = true;
        content.push({ type: "tool_result", ...(part.callId !== undefined && { callId: part.callId }), ...(part.name !== undefined && { name: part.name }), content: part.content });
      } else if (type === "image") {
        throw new ConduitError("UnsupportedCapabilityError", "Image content is not implemented.");
      } else {
        invalid("Invalid content part type.");
      }
    }
    // Role validation
    if (role === "tool" && !hasToolResult) invalid("Tool messages must contain a tool_result part.");
    if (role !== "tool" && hasToolResult) invalid("Only tool messages may contain tool_result parts.");
    if (hasToolCall && role !== "assistant") invalid("Only assistant messages may contain tool_call parts.");
    if (role === "tool" && hasToolCall) invalid("Tool messages must not contain tool_call parts.");
    if (role === "tool" && hasText) invalid("Tool messages must not contain text parts; use tool_result.");
    return { role, content };
  });
  const { maxOutputTokens, temperature, topP, stop, providerOptions, tools, toolChoice, responseFormat } = normalized;
  if (maxOutputTokens !== undefined && (!Number.isSafeInteger(maxOutputTokens) || (driver === "anthropic" ? maxOutputTokens < 0 : maxOutputTokens < 1))) {
    invalid(driver === "anthropic" ? "maxOutputTokens must be a nonnegative safe integer." : "maxOutputTokens must be a positive safe integer.");
  }
  if (temperature !== undefined && (!Number.isFinite(temperature) || temperature < 0 || (driver === "openai-compatible" && temperature > 2) || (driver === "anthropic" && temperature > 1))) {
    if (driver === "anthropic") invalid("temperature must be between 0 and 1.");
    invalid(driver === "ollama" ? "temperature must be finite and nonnegative." : "temperature must be between 0 and 2.");
  }
  if (topP !== undefined && (!Number.isFinite(topP) || topP < 0 || topP > 1)) invalid("topP must be between 0 and 1.");
  if (stop !== undefined && (!Array.isArray(stop) || Array.from(stop).some(value => typeof value !== "string"))) invalid("stop must be an array of strings.");
  if (providerOptions !== undefined) {
    if (!object(providerOptions)) invalid("providerOptions must be an object.");
    if (driver === "openai-compatible" && Object.keys(providerOptions).some(key => ownedFields.has(key) && !(streaming && key === "stream_options"))) invalid("providerOptions conflicts with a Conduit-owned field.");
    if (driver === "openai-compatible" && streaming && providerOptions.stream_options !== undefined && !object(providerOptions.stream_options)) invalid("stream_options must be a JSON object.");
    // Anthropic collisions handled in anthropicRequest; generic JSON check still applies
    try { json(providerOptions); } catch (error) {
      if (error instanceof ConduitError) throw error;
      invalid("providerOptions must contain plain JSON data.");
    }
  }
  // If tools were provided, ensure providerOptions does not also contain raw tools (already reserved), but allow passthrough of native extras
  if (driver === "anthropic" && responseFormat !== undefined) {
    throw new ConduitError("UnsupportedCapabilityError", "responseFormat is not supported for Anthropic.");
  }
  if (driver === "gemini" && tools !== undefined) {
    // Gemini tool/function mapping – validate via geminiRequest, keep encode separate
  }
  const wireToolsGemini = encodeGeminiTools(tools as import("./types.js").ToolDefinition[] | undefined);
  const wireToolChoiceGemini = encodeGeminiToolChoice(toolChoice);
  const wireFormatGemini = encodeGeminiFormat(responseFormat);
  const wireTools = driver === "anthropic" ? encodeToolsAnthropic(tools as import("./types.js").ToolDefinition[] | undefined) : driver === "gemini" ? wireToolsGemini : encodeTools(tools as import("./types.js").ToolDefinition[] | undefined);
  const wireToolChoice = driver === "gemini" ? wireToolChoiceGemini : encodeToolChoice(toolChoice, driver);
  const wireFormat = driver === "anthropic" ? undefined : driver === "gemini" ? wireFormatGemini : encodeResponseFormat(responseFormat, driver);
  if (driver === "ollama") {
    if (wireToolChoice !== undefined) {
      throw new ConduitError("UnsupportedCapabilityError", "toolChoice is not supported for Ollama; omit toolChoice or use providerOptions for native fields.");
    }
    return ollamaRequest(model, messages as { role: unknown; content: unknown[] }[], normalized, streaming, wireTools, undefined, wireFormat);
  }
  if (driver === "anthropic") {
    return anthropicRequest(model, messages as { role: string; content: unknown[] }[], normalized, streaming, wireTools, wireToolChoice);
  }
  if (driver === "gemini") {
    return geminiRequest(messages as { role: string; content: unknown[] }[], request, wireTools, wireToolChoice, wireFormat);
  }
  // OpenAI-compatible wire: map tool messages and tool calls
  const wireMessages = messages.map(m => {
    const role = m.role as string;
    const parts = m.content as unknown[];
    if (role === "tool") {
      const tr = parts.find(p => (p as Record<string, unknown>).type === "tool_result") as Record<string, unknown> | undefined;
      if (!tr) invalid("Tool message must contain tool_result.");
      const innerContent = tr.content as unknown;
      const text = typeof innerContent === "string" ? innerContent as string : ((innerContent as unknown[]).map((q: unknown) => (q as Record<string, unknown>).text as string).join(""));
      const out: Record<string, unknown> = { role: "tool", content: text };
      if (typeof tr.callId === "string" && tr.callId) out.tool_call_id = tr.callId;
      else if (typeof tr.name === "string" && tr.name) out.tool_call_id = tr.name;
      return out;
    }
    const textParts = parts.filter(p => (p as Record<string, unknown>).type === "text") as unknown[];
    const toolCalls = parts.filter(p => (p as Record<string, unknown>).type === "tool_call") as unknown[];
    if (toolCalls.length) {
      const wireToolCalls = toolCalls.map((tc: unknown) => {
        const c = tc as Record<string, unknown>;
        return { id: c.id ?? `call_${Math.random().toString(36).slice(2)}`, type: "function", function: { name: c.name, arguments: JSON.stringify(c.arguments) } };
      });
      const content = textParts.length ? textParts.map((p: unknown) => (p as Record<string, unknown>).text as string).join("") : null;
      // Preserve array form for text-only expectations? For tool calls, use string/null per OpenAI spec
      return { role: "assistant", content, tool_calls: wireToolCalls };
    }
    // No tool calls: keep array-of-text-parts shape for backward compat with existing fixtures
    return m;
  });
  return JSON.stringify({ ...providerOptions, model, messages: wireMessages, stream: streaming,
    max_tokens: maxOutputTokens, temperature, top_p: topP, stop: stop === undefined ? undefined : Array.from(stop),
    ...(wireTools !== undefined && { tools: wireTools }),
    ...(wireToolChoice !== undefined && { tool_choice: wireToolChoice }),
    ...(wireFormat !== undefined && { response_format: wireFormat }),
  });
}

export function connect(config: ClientConfig & { model: string }): Model;
export function connect(config: ClientConfig): Client;
export function connect(config: ClientConfig & { model?: string }): Client | Model {
  if (!object(config)) invalid("Client configuration is required.");
  keys(config, new Set(["driver", "endpoint", "credentials", "headers", "timeout", "model"]));
  if (config.driver !== "openai-compatible" && config.driver !== "ollama" && config.driver !== "anthropic" && config.driver !== "gemini") invalid("Unknown driver.");
  const driver = config.driver;
  const defaults: Record<string, string> = { ollama: "http://localhost:11434", anthropic: "https://api.anthropic.com", gemini: "https://generativelanguage.googleapis.com" };
  const rawEndpoint = (config as Record<string, unknown>).endpoint as string | undefined;
  if (rawEndpoint === undefined) {
    if (driver === "openai-compatible") invalid("endpoint must be an HTTP(S) API base URL.");
    (config as Record<string, unknown>).endpoint = defaults[driver];
  }
  if (typeof config.endpoint !== "string") invalid("endpoint must be an HTTP(S) API base URL.");
  let endpoint: URL;
  try { endpoint = new URL(config.endpoint); } catch { invalid("endpoint must be an HTTP(S) API base URL."); }
  if (!["http:", "https:"].includes(endpoint.protocol) || endpoint.username || endpoint.password || endpoint.search || endpoint.hash || config.endpoint.includes("?") || config.endpoint.includes("#")) {
    invalid("endpoint must be an HTTP(S) API base URL without userinfo, query, or fragment.");
  }
  const basePath = endpoint.pathname.replace(/\/+$/, "");
  let url: string;
  let listUrl: string;
  if (driver === "gemini") {
    endpoint.pathname = basePath + "/v1beta/models";
    const base = endpoint.href.replace(/\/+$/, "");
    url = base; // per-model suffix added in operation
    listUrl = base;
  } else {
    endpoint.pathname = basePath + (driver === "ollama" ? "/api/chat" : driver === "anthropic" ? "/v1/messages" : "/chat/completions");
    url = endpoint.href;
    listUrl = `${endpoint.protocol}//${endpoint.host}${basePath}${driver === "ollama" ? "/api/tags" : driver === "anthropic" ? "/v1/models" : "/models"}`;
  }
  const credentials = config.credentials;
  if (credentials !== undefined && (typeof credentials !== "string" || !/^[A-Za-z0-9._~+\/-]+=*$/.test(credentials))) invalid("credentials must be a nonempty bearer token.");
  timeoutValue(config.timeout);
  const defaultTimeout = config.timeout;
  const headers = new Headers({ "content-type": "application/json" });
  const secrets: string[] = credentials ? [credentials] : [];
  if (config.headers !== undefined) {
    if (!object(config.headers)) invalid("headers must be a string-valued object.");
    for (const key of Reflect.ownKeys(config.headers)) {
      if (typeof key !== "string" || protectedHeaders.has(key.toLowerCase())) invalid("Custom header conflicts with a protected header.");
      const value = config.headers[key];
      if (typeof value !== "string") invalid("Custom header values must be strings.");
      try { headers.set(key, value); } catch { invalid("Invalid custom HTTP header."); }
      if (value) secrets.push(value);
      const normalized = headers.get(key);
      if (normalized) secrets.push(normalized);
    }
  }
  if (driver === "anthropic") {
    if (credentials) headers.set("x-api-key", credentials);
    headers.set("anthropic-version", "2023-06-01");
  } else if (driver === "gemini") {
    if (credentials) headers.set("x-goog-api-key", credentials);
  } else if (credentials) headers.set("authorization", `Bearer ${credentials}`);
  // ponytail: known raw/URL-encoded secrets only; add encodings when a provider demonstrates them.
  const redactions = [...new Set(secrets.flatMap(secret => [secret, encodeURIComponent(secret)]))].sort((a, b) => b.length - a.length);
  const redact = (text: string): string => redactions.reduce((result, secret) => result.split(secret).join("[REDACTED]"), text);
  function protocolModels(message = "Malformed or unsupported model listing response."): never {
    throw new ConduitError("ProtocolError", message);
  }
  function normalizeOllamaModels(value: unknown): ModelInfo[] {
    if (!object(value) || !Array.isArray((value as Record<string, unknown>).models)) protocolModels();
    const models = (value as Record<string, unknown>).models as unknown[];
    return models.map(entry => {
      if (!object(entry)) protocolModels();
      const id = typeof entry.name === "string" ? entry.name : typeof entry.model === "string" ? entry.model : undefined;
      if (typeof id !== "string" || !id.trim()) protocolModels();
      const providerMetadata: Record<string, JsonValue> = {};
      for (const [key, data] of Object.entries(entry)) {
        if (key === "name" || key === "model") continue;
        // Keep only JSON-compatible values; any non-JSON would have been rejected as malformed earlier, but stay defensive.
        try { JSON.stringify(data); } catch { protocolModels(); }
        if (data !== undefined) providerMetadata[key] = data as JsonValue;
      }
      // Preserve the alternate identifier in metadata when present and distinct.
      if (typeof entry.model === "string" && entry.model !== id) providerMetadata.model = entry.model as JsonValue;
      else if (typeof entry.name === "string" && entry.name !== id) providerMetadata.name = entry.name as JsonValue;
      const info: ModelInfo = { id: redact(id) };
      if (typeof entry.name === "string" && entry.name.trim()) info.name = redact(entry.name);
      if (Object.keys(providerMetadata).length) info.providerMetadata = providerMetadata;
      return info;
    });
  }
  function normalizeOpenAIModels(value: unknown): ModelInfo[] {
    if (!object(value) || !Array.isArray((value as Record<string, unknown>).data)) protocolModels();
    const data = (value as Record<string, unknown>).data as unknown[];
    return data.map(entry => {
      if (!object(entry) || typeof entry.id !== "string" || !entry.id.trim()) protocolModels();
      const id = entry.id as string;
      const providerMetadata: Record<string, JsonValue> = {};
      for (const [key, data] of Object.entries(entry)) {
        if (key === "id") continue;
        try { JSON.stringify(data); } catch { protocolModels(); }
        if (data !== undefined) providerMetadata[key] = data as JsonValue;
      }
      const info: ModelInfo = { id: redact(id) };
      // Preserve a display name if provider reuses id-like field; keep optional.
      if (Object.keys(providerMetadata).length) info.providerMetadata = providerMetadata;
      return info;
    });
  }
  function normalizeAnthropicModels(value: unknown): ModelInfo[] {
    if (!object(value) || !Array.isArray((value as Record<string, unknown>).data)) protocolModels();
    const data = (value as Record<string, unknown>).data as unknown[];
    return data.map(entry => {
      if (!object(entry) || typeof entry.id !== "string" || !entry.id.trim()) protocolModels();
      const id = entry.id as string;
      const providerMetadata: Record<string, JsonValue> = {};
      let displayName: string | undefined;
      for (const [key, data] of Object.entries(entry)) {
        if (key === "id" || key === "display_name") continue;
        try { JSON.stringify(data); } catch { protocolModels(); }
        if (data !== undefined) providerMetadata[key] = data as JsonValue;
      }
      if (typeof entry.display_name === "string" && entry.display_name.trim()) displayName = entry.display_name;
      // Also capture display_name in metadata for completeness? Preserve as not normalized, but name is normalized
      const info: ModelInfo = { id: redact(id) };
      if (displayName) info.name = redact(displayName);
      if (Object.keys(providerMetadata).length) info.providerMetadata = providerMetadata;
      return info;
    });
  }
  function normalizeGeminiModels(value: unknown): ModelInfo[] {
    if (!object(value) || !Array.isArray((value as Record<string, unknown>).models)) protocolModels();
    const models = (value as Record<string, unknown>).models as unknown[];
    return models.map(entry => {
      if (!object(entry) || typeof entry.name !== "string" || !entry.name.trim()) protocolModels();
      const rawName = entry.name as string;
      const id = rawName.startsWith("models/") ? rawName.slice("models/".length) : rawName;
      if (!id.trim()) protocolModels();
      const providerMetadata: Record<string, JsonValue> = {};
      for (const [key, data] of Object.entries(entry)) {
        if (key === "name" || key === "displayName") continue;
        try { JSON.stringify(data); } catch { protocolModels(); }
        if (data !== undefined) providerMetadata[key] = data as JsonValue;
      }
      const info: ModelInfo = { id: redact(id) };
      if (typeof entry.displayName === "string" && entry.displayName.trim()) info.name = redact(entry.displayName);
      // preserve full resource name in metadata if different
      if (rawName !== id) providerMetadata.name = rawName as JsonValue;
      if (Object.keys(providerMetadata).length) info.providerMetadata = providerMetadata;
      return info;
    });
  }
  async function listModels(opts?: ListModelsOptions): Promise<ModelInfo[]> {
    // Read before narrowing via object() to avoid Record<string,unknown> widening.
    const rawTimeout = (opts as ListModelsOptions | undefined)?.timeout;
    const rawSignal = (opts as ListModelsOptions | undefined)?.signal;
    if (opts !== undefined) {
      if (!object(opts)) invalid("listModels options must be an object.");
      keys(opts, new Set(["timeout", "signal"]));
    }
    timeoutValue(rawTimeout);
    if (rawSignal !== undefined && !(rawSignal instanceof AbortSignal)) invalid("signal must be an AbortSignal.");
    const timeout = rawTimeout ?? defaultTimeout;
    const signal = rawSignal;
    const controller = new AbortController();
    const cancel = () => controller.abort(new ConduitError("CancelledError", "Request cancelled by caller."));
    if (signal?.aborted) cancel();
    else signal?.addEventListener("abort", cancel, { once: true });
    const timer = timeout === undefined ? undefined : setTimeout(() => controller.abort(new ConduitError("TimeoutError", "Request deadline exceeded.")), timeout);
    let response: Response | undefined;
    let requestId: string | undefined;
    try {
      if (driver === "anthropic") {
        const all: ModelInfo[] = [];
        let cursor: string | undefined;
        const seenCursors = new Set<string>();
        // ponytail: sequential pagination, guard against non-progressing cursors; parallel fetch would violate timeout semantics.
        for (let pages = 0; pages < 100; pages++) {
          controller.signal.throwIfAborted();
          const pageUrl = cursor === undefined ? listUrl : `${listUrl}?after_id=${encodeURIComponent(cursor)}`;
          if (cursor !== undefined && seenCursors.has(cursor)) protocolModels("Malformed pagination cursor.");
          if (cursor !== undefined) seenCursors.add(cursor);
          response = await fetch(pageUrl, { method: "GET", headers, signal: controller.signal, redirect: "manual" });
          const rawRequestId = response.headers.get("x-request-id") ?? response.headers.get("request-id");
          const pageRequestId = rawRequestId === null ? undefined : redact(rawRequestId);
          if (requestId === undefined) requestId = pageRequestId;
          if (!response.ok) {
            const text = await response.text();
            controller.signal.throwIfAborted();
            throw anthropicError(response.status, text, pageRequestId ?? requestId, redact);
          }
          const text = await response.text();
          controller.signal.throwIfAborted();
          let value: unknown;
          try { value = JSON.parse(text); } catch { protocolModels("Provider returned invalid JSON."); }
          if (!object(value) || !Array.isArray((value as Record<string, unknown>).data)) protocolModels();
          const hasMore = (value as Record<string, unknown>).has_more;
          if (hasMore !== undefined && typeof hasMore !== "boolean") protocolModels();
          const rawLastId = (value as Record<string, unknown>).last_id;
          if (hasMore) {
            if (typeof rawLastId !== "string" || !rawLastId.trim()) protocolModels("Malformed pagination: last_id required when has_more true.");
          } else if (rawLastId !== undefined && typeof rawLastId !== "string") protocolModels("Malformed last_id.");
          const pageModels = normalizeAnthropicModels(value);
          all.push(...pageModels);
          if (!hasMore) return all;
          if (pageModels.length === 0) protocolModels("Malformed pagination: has_more true with empty data.");
          const nextCursor = rawLastId as string;
          if (!nextCursor || nextCursor === cursor) protocolModels("Malformed pagination: cursor did not advance.");
          if (seenCursors.has(nextCursor)) protocolModels("Malformed pagination: cursor cycle.");
          cursor = nextCursor;
          // loop continues, same controller/timer preserves operation-level timeout
          // ensure body is consumed before next iteration (already via text())
          if (response.body && !response.body.locked) await response.body.cancel().catch(() => {});
          response = undefined;
        }
        protocolModels("Too many pagination pages.");
      }
      if (driver === "gemini") {
        const all: ModelInfo[] = [];
        let pageToken: string | undefined;
        const seenTokens = new Set<string>();
        // ponytail: sequential pagination, guard non-progressing token; parallel would violate timeout.
        for (let pages = 0; pages < 100; pages++) {
          controller.signal.throwIfAborted();
          const pageUrl = pageToken === undefined ? listUrl : `${listUrl}?pageToken=${encodeURIComponent(pageToken)}`;
          if (pageToken !== undefined && seenTokens.has(pageToken)) protocolModels("Malformed pagination token.");
          if (pageToken !== undefined) seenTokens.add(pageToken);
          response = await fetch(pageUrl, { method: "GET", headers, signal: controller.signal, redirect: "manual" });
          const rawRequestId = response.headers.get("x-request-id") ?? response.headers.get("request-id");
          const pageRequestId = rawRequestId === null ? undefined : redact(rawRequestId);
          if (requestId === undefined) requestId = pageRequestId;
          if (!response.ok) {
            const text = await response.text();
            controller.signal.throwIfAborted();
            throw geminiError(response.status, text, pageRequestId ?? requestId, redact);
          }
          const text = await response.text();
          controller.signal.throwIfAborted();
          let value: unknown;
          try { value = JSON.parse(text); } catch { protocolModels("Provider returned invalid JSON."); }
          if (!object(value) || !Array.isArray((value as Record<string, unknown>).models)) protocolModels();
          const nextPageToken = (value as Record<string, unknown>).nextPageToken;
          if (nextPageToken !== undefined && typeof nextPageToken !== "string") protocolModels("Malformed nextPageToken.");
          const pageModels = normalizeGeminiModels(value);
          all.push(...pageModels);
          if (!nextPageToken) return all;
          if (nextPageToken === pageToken) protocolModels("Malformed pagination: token did not advance.");
          if (seenTokens.has(nextPageToken)) protocolModels("Malformed pagination: token cycle.");
          pageToken = nextPageToken as string;
          if (response.body && !response.body.locked) await response.body.cancel().catch(() => {});
          response = undefined;
        }
        protocolModels("Too many pagination pages.");
      }
      controller.signal.throwIfAborted();
      response = await fetch(listUrl, { method: "GET", headers, signal: controller.signal, redirect: "manual" });
      const rawRequestId = response.headers.get("x-request-id") ?? response.headers.get("request-id");
      requestId = rawRequestId === null ? undefined : redact(rawRequestId);
      if (!response.ok) {
        const text = await response.text();
        controller.signal.throwIfAborted();
        throw driver === "ollama" ? ollamaError(response.status, text, requestId, redact) : httpError(response.status, text, requestId, redact);
      }
      const text = await response.text();
      controller.signal.throwIfAborted();
      let value: unknown;
      try { value = JSON.parse(text); } catch { protocolModels("Provider returned invalid JSON."); }
      const models = driver === "ollama" ? normalizeOllamaModels(value) : normalizeOpenAIModels(value);
      return models;
    } catch (error) {
      if (controller.signal.aborted) throw controller.signal.reason;
      if (error instanceof ConduitError) {
        if (error.name === "ProtocolError" && response) {
          error.statusCode = response.status;
          if (requestId !== undefined) error.requestId = requestId;
        }
        throw error;
      }
      const native = error instanceof Error ? error : undefined;
      const cause = native && object(native.cause) ? native.cause : native;
      throw new ConduitError("ConnectionError", "Provider connection failed.", {
        ...(cause && { cause: { name: redact(typeof cause.name === "string" ? cause.name : "Error"), message: redact(typeof cause.message === "string" ? cause.message : "Native fetch failed."), ...("code" in cause && typeof cause.code === "string" && { code: redact(cause.code) }) } }),
      });
    } finally {
      clearTimeout(timer);
      signal?.removeEventListener("abort", cancel);
      controller.abort();
      if (response?.body && !response.body.locked) await response.body.cancel().catch(() => {});
    }
  }
  async function* operation(id: string, request: GenerationRequest | string, streaming: boolean): AsyncGenerator<StreamEvent> {
    const normalized = normalizeRequest(request);
    const body = encode(id, normalized, streaming, driver);
    const timeout = normalized.timeout ?? defaultTimeout;
    timeoutValue(normalized.timeout);
    const signal = normalized.signal;
    if (signal !== undefined && !(signal instanceof AbortSignal)) invalid("signal must be an AbortSignal.");
    const controller = new AbortController();
    const cancel = () => controller.abort(new ConduitError("CancelledError", "Request cancelled by caller."));
    if (signal?.aborted) cancel();
    else signal?.addEventListener("abort", cancel, { once: true });
    const timer = timeout === undefined ? undefined : setTimeout(() => controller.abort(new ConduitError("TimeoutError", "Request deadline exceeded.")), timeout);
    let response: Response | undefined;
    let requestId: string | undefined;
    let result: GenerationResponse | undefined;
    try {
      controller.signal.throwIfAborted();
      const fetchUrl = driver === "gemini" ? `${url}/${encodeURIComponent(id)}:${streaming ? "streamGenerateContent?alt=sse" : "generateContent"}` : url;
      response = await fetch(fetchUrl, { method: "POST", headers, body, signal: controller.signal, redirect: "manual" });
      const rawRequestId = response.headers.get("x-request-id") ?? response.headers.get("request-id");
      requestId = rawRequestId === null ? undefined : redact(rawRequestId);
      if (!response.ok) {
        const text = await response.text();
        controller.signal.throwIfAborted();
        throw driver === "ollama" ? ollamaError(response.status, text, requestId, redact, id) : driver === "anthropic" ? anthropicError(response.status, text, requestId, redact) : driver === "gemini" ? geminiError(response.status, text, requestId, redact) : httpError(response.status, text, requestId, redact);
      }
      if (streaming) {
        const media = response.headers.get("content-type")?.split(";")[0]?.trim().toLowerCase();
        if (!response.body || !(driver === "ollama" ? ["application/x-ndjson", "application/ndjson", "application/json"].includes(media ?? "") : driver === "gemini" ? media === "text/event-stream" : media === "text/event-stream")) {
          throw new ConduitError("ProtocolError", driver === "ollama" ? "Expected an NDJSON response." : "Expected a text/event-stream response.");
        }
        const fullContentType = response.headers.get("content-type");
        const events = driver === "ollama" ? ollamaStream(response.body, requestId, redact) : driver === "anthropic" ? anthropicStream(response.body, requestId, redact) : driver === "gemini" ? geminiStream(response.body, requestId, redact, fullContentType) : openaiStream(response.body, requestId, redact);
        for await (const event of events) {
          controller.signal.throwIfAborted();
          if (event.type === "done") { result = event.response; break; }
          yield event;
          controller.signal.throwIfAborted();
        }
      } else {
        const text = await response.text();
        controller.signal.throwIfAborted();
        let value: unknown;
        try { value = JSON.parse(text); } catch {
          throw new ConduitError("ProtocolError", "Provider returned invalid JSON.");
        }
        result = driver === "ollama" ? ollamaResponse(value, requestId, redact) : driver === "anthropic" ? anthropicResponse(value, requestId, redact) : driver === "gemini" ? geminiResponse(value, requestId, redact) : decode(value, requestId, redact);
      }
    } catch (error) {
      if (controller.signal.aborted) throw controller.signal.reason;
      if (error instanceof ConduitError) {
        if (error.name === "ProtocolError" && response) {
          error.statusCode = response.status;
          if (requestId !== undefined) error.requestId = requestId;
        }
        throw error;
      }
      const native = error instanceof Error ? error : undefined;
      const cause = native && object(native.cause) ? native.cause : native;
      throw new ConduitError("ConnectionError", "Provider connection failed.", {
        ...(cause && { cause: { name: redact(typeof cause.name === "string" ? cause.name : "Error"), message: redact(typeof cause.message === "string" ? cause.message : "Native fetch failed."), ...("code" in cause && typeof cause.code === "string" && { code: redact(cause.code) }) } }),
      });
    } finally {
      clearTimeout(timer);
      signal?.removeEventListener("abort", cancel);
      controller.abort();
      // The parser cancels its locked reader; this covers bodies rejected before parsing.
      if (response?.body && !response.body.locked) await response.body.cancel().catch(() => {});
    }
    if (result) yield { type: "done", response: result };
  }
  const client: Client = Object.freeze({
    listModels,
    model(id: string): Model {
      if (typeof id !== "string" || !id.trim()) invalid("model must be a nonempty string.");
      return Object.freeze({
        async generate(request: GenerationRequest | string): Promise<GenerationResponse> {
          for await (const event of operation(id, request, false)) {
            if (event.type === "done") return event.response;
          }
          throw new ConduitError("ProtocolError", "Missing generation response.");
        },
        stream(request: GenerationRequest | string): AsyncGenerator<StreamEvent> {
          return operation(id, request, true);
        },
      });
    },
  });
  return config.model === undefined ? client : client.model(config.model);
}
