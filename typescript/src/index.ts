import { decode, httpError } from "./openai-response.js";
import { object } from "./response.js";
import { ollamaRequest, ollamaResponse, ollamaError, ollamaStream } from "./ollama.js";
import { openaiStream } from "./openai-stream.js";
import { ConduitError } from "./errors.js";
import type { Client, ClientConfig, GenerationRequest, GenerationResponse, Model, StreamEvent } from "./types.js";

export { ConduitError } from "./errors.js";
export type { ErrorCode, ErrorDetails } from "./errors.js";
export type { Client, ClientConfig, GenerationRequest, GenerationResponse, JsonValue, Message, Model, StreamEvent, TextPart, Usage } from "./types.js";

const ownedFields = new Set([
  "model", "messages", "stream", "stream_options", "max_tokens", "max_completion_tokens",
  "temperature", "top_p", "stop", "tools", "tool_choice", "functions", "function_call",
  "response_format", "n", "modalities",
]);
const protectedHeaders = new Set([
  "authorization", "proxy-authorization", "cookie", "host", "content-type", "content-length",
  "connection", "transfer-encoding", "upgrade", "trailer", "te", "keep-alive",
]);
const requestFields = new Set([
  "messages", "maxOutputTokens", "temperature", "topP", "stop", "providerOptions", "signal", "timeout",
]);
const unsupportedFields = new Set(["tools", "toolChoice", "responseFormat", "stream", "reasoning", "vision"]);

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

function encode(model: string, request: GenerationRequest, streaming: boolean, driver: ClientConfig["driver"]): string {
  if (!object(request)) invalid("A generation request is required.");
  if (Object.keys(request).some(key => unsupportedFields.has(key))) {
    throw new ConduitError("UnsupportedCapabilityError", streaming ? "Only text streaming is implemented." : "Only non-streaming text generation is implemented.");
  }
  keys(request, requestFields);
  if (!Array.isArray(request.messages) || request.messages.length === 0) invalid("messages must be a nonempty array.");
  const messages = Array.from(request.messages, message => {
    if (!object(message)) invalid("Each message must be an object.");
    keys(message, new Set(["role", "content"]));
    if (message.role === "tool") throw new ConduitError("UnsupportedCapabilityError", "Tool messages are not implemented.");
    if (!["system", "user", "assistant"].includes(message.role as string)) invalid("Invalid text message role.");
    const parts = typeof message.content === "string" ? [{ type: "text", text: message.content }] : message.content;
    if (!Array.isArray(parts)) invalid("Message content must be a string or text-part array.");
    const content = Array.from(parts, part => {
      if (!object(part)) invalid("Invalid content part.");
      if (["image", "tool_call", "tool_result"].includes(part.type as string)) {
        throw new ConduitError("UnsupportedCapabilityError", "Only text content is implemented.");
      }
      keys(part, new Set(["type", "text"]));
      if (part.type !== "text" || typeof part.text !== "string") invalid("Invalid text content part.");
      return { type: "text", text: part.text };
    });
    return { role: message.role, content };
  });
  const { maxOutputTokens, temperature, topP, stop, providerOptions } = request;
  if (maxOutputTokens !== undefined && (!Number.isSafeInteger(maxOutputTokens) || maxOutputTokens < 1)) invalid("maxOutputTokens must be a positive safe integer.");
  if (temperature !== undefined && (!Number.isFinite(temperature) || temperature < 0 || (driver === "openai-compatible" && temperature > 2))) invalid(driver === "ollama" ? "temperature must be finite and nonnegative." : "temperature must be between 0 and 2.");
  if (topP !== undefined && (!Number.isFinite(topP) || topP < 0 || topP > 1)) invalid("topP must be between 0 and 1.");
  if (stop !== undefined && (!Array.isArray(stop) || Array.from(stop).some(value => typeof value !== "string"))) invalid("stop must be an array of strings.");
  if (providerOptions !== undefined) {
    if (!object(providerOptions)) invalid("providerOptions must be an object.");
    if (driver === "openai-compatible" && Object.keys(providerOptions).some(key => ownedFields.has(key) && !(streaming && key === "stream_options"))) invalid("providerOptions conflicts with a Conduit-owned field.");
    if (driver === "openai-compatible" && streaming && providerOptions.stream_options !== undefined && !object(providerOptions.stream_options)) invalid("stream_options must be a JSON object.");
    try { json(providerOptions); } catch (error) {
      if (error instanceof ConduitError) throw error;
      invalid("providerOptions must contain plain JSON data.");
    }
  }
  if (driver === "ollama") return ollamaRequest(model, messages, request, streaming);
  return JSON.stringify({ ...providerOptions, model, messages, stream: streaming,
    max_tokens: maxOutputTokens, temperature, top_p: topP, stop: stop === undefined ? undefined : Array.from(stop) });
}

export function connect(config: ClientConfig & { model: string }): Model;
export function connect(config: ClientConfig): Client;
export function connect(config: ClientConfig & { model?: string }): Client | Model {
  if (!object(config)) invalid("Client configuration is required.");
  keys(config, new Set(["driver", "endpoint", "credentials", "headers", "timeout", "model"]));
  if (config.driver !== "openai-compatible" && config.driver !== "ollama") invalid("Unknown driver.");
  const driver = config.driver;
  if (typeof config.endpoint !== "string") invalid("endpoint must be an HTTP(S) API base URL.");
  let endpoint: URL;
  try { endpoint = new URL(config.endpoint); } catch { invalid("endpoint must be an HTTP(S) API base URL."); }
  if (!["http:", "https:"].includes(endpoint.protocol) || endpoint.username || endpoint.password || endpoint.search || endpoint.hash || config.endpoint.includes("?") || config.endpoint.includes("#")) {
    invalid("endpoint must be an HTTP(S) API base URL without userinfo, query, or fragment.");
  }
  endpoint.pathname = endpoint.pathname.replace(/\/+$/, "") + (driver === "ollama" ? "/api/chat" : "/chat/completions");
  const url = endpoint.href;
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
  if (credentials) headers.set("authorization", `Bearer ${credentials}`);
  // ponytail: known raw/URL-encoded secrets only; add encodings when a provider demonstrates them.
  const redactions = [...new Set(secrets.flatMap(secret => [secret, encodeURIComponent(secret)]))].sort((a, b) => b.length - a.length);
  const redact = (text: string): string => redactions.reduce((result, secret) => result.split(secret).join("[REDACTED]"), text);
  async function* operation(id: string, request: GenerationRequest, streaming: boolean): AsyncGenerator<StreamEvent> {
    const body = encode(id, request, streaming, driver);
    const timeout = request.timeout ?? defaultTimeout;
    timeoutValue(request.timeout);
    const signal = request.signal;
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
      response = await fetch(url, { method: "POST", headers, body, signal: controller.signal, redirect: "manual" });
      const rawRequestId = response.headers.get("x-request-id") ?? response.headers.get("request-id");
      requestId = rawRequestId === null ? undefined : redact(rawRequestId);
      if (!response.ok) {
        const text = await response.text();
        controller.signal.throwIfAborted();
        throw driver === "ollama" ? ollamaError(response.status, text, requestId, redact, id) : httpError(response.status, text, requestId, redact);
      }
      if (streaming) {
        const media = response.headers.get("content-type")?.split(";")[0]?.trim().toLowerCase();
        if (!response.body || !(driver === "ollama" ? ["application/x-ndjson", "application/ndjson", "application/json"].includes(media ?? "") : media === "text/event-stream")) {
          throw new ConduitError("ProtocolError", driver === "ollama" ? "Expected an NDJSON response." : "Expected a text/event-stream response.");
        }
        const events = driver === "ollama" ? ollamaStream(response.body, requestId, redact) : openaiStream(response.body, requestId, redact);
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
        result = driver === "ollama" ? ollamaResponse(value, requestId, redact) : decode(value, requestId, redact);
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
    model(id: string): Model {
      if (typeof id !== "string" || !id.trim()) invalid("model must be a nonempty string.");
      return Object.freeze({
        async generate(request: GenerationRequest): Promise<GenerationResponse> {
          for await (const event of operation(id, request, false)) {
            if (event.type === "done") return event.response;
          }
          throw new ConduitError("ProtocolError", "Missing generation response.");
        },
        stream(request: GenerationRequest): AsyncGenerator<StreamEvent> {
          return operation(id, request, true);
        },
      });
    },
  });
  return config.model === undefined ? client : client.model(config.model);
}
