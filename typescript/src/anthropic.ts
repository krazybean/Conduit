import { ConduitError, httpFailure } from "./errors.js";
import { object, textResponse } from "./response.js";
import type { GenerationRequest, GenerationResponse, StreamEvent, Usage } from "./types.js";

function protocol(message = "Malformed or unsupported Anthropic response."): never {
  throw new ConduitError("ProtocolError", message);
}

export function anthropicRequest(
  model: string,
  messages: { role: string; content: unknown[] }[],
  request: GenerationRequest,
  streaming: boolean,
  wireTools?: unknown,
  wireToolChoice?: unknown,
): string {
  const native = (request.providerOptions ?? {}) as Record<string, unknown>;
  // Owned fields for Anthropic
  const owned = ["model", "messages", "system", "stream", "max_tokens", "temperature", "top_p", "top_k", "stop_sequences", "tools", "tool_choice"];
  if (owned.some(key => Object.hasOwn(native, key))) {
    throw new ConduitError("InvalidRequestError", "providerOptions conflicts with a Conduit-owned field.");
  }
  // Also check nested thinking fields? Not owned for now; pass through.
  try {
    JSON.stringify(native);
  } catch {
    protocol("Invalid providerOptions.");
  }

  // Extract system messages — only leading system messages are faithful to Anthropic's top-level system field
  const systemParts: string[] = [];
  const anthropicMessages: unknown[] = [];
  let seenNonSystem = false;
  for (const m of messages) {
    const role = m.role as string;
    const parts = m.content as unknown[];
    if (role === "system") {
      if (seenNonSystem) {
        throw new ConduitError("InvalidRequestError", "System messages must be leading.");
      }
      for (const p of parts) {
        const c = p as Record<string, unknown>;
        if (c.type === "text") systemParts.push(c.text as string);
        else if (c.type === "tool_result" || c.type === "tool_call") {
          throw new ConduitError("InvalidRequestError", "System messages must not contain tool content.");
        }
      }
      continue;
    }
    seenNonSystem = true;
    if (role === "tool") {
      // Map tool message -> user with tool_result blocks; Anthropic requires exact tool_use_id correlation
      const toolResults: unknown[] = [];
      for (const p of parts) {
        const c = p as Record<string, unknown>;
        if (c.type === "tool_result") {
          if (typeof c.callId !== "string" || !c.callId.trim()) {
            throw new ConduitError("InvalidRequestError", "Tool result callId is required for Anthropic.");
          }
          const inner = c.content as unknown;
          const contentValue = typeof inner === "string"
            ? inner as string
            : ((inner as unknown[]).map((q: unknown) => (q as Record<string, unknown>).text as string).join(""));
          const block: Record<string, unknown> = {
            type: "tool_result",
            tool_use_id: c.callId,
            content: contentValue,
          };
          toolResults.push(block);
        } else {
          throw new ConduitError("InvalidRequestError", "Tool messages must contain tool_result parts.");
        }
      }
      anthropicMessages.push({ role: "user", content: toolResults });
      continue;
    }
    // user or assistant
    const blocks: unknown[] = [];
    for (const p of parts) {
      const c = p as Record<string, unknown>;
      if (c.type === "text") {
        blocks.push({ type: "text", text: c.text });
      } else if (c.type === "tool_call") {
        if (role !== "assistant") throw new ConduitError("InvalidRequestError", "Only assistant messages may contain tool_call parts.");
        if (typeof c.id !== "string" || !c.id.trim()) {
          throw new ConduitError("InvalidRequestError", "Tool call id is required for Anthropic.");
        }
        blocks.push({ type: "tool_use", id: c.id, name: c.name, input: c.arguments });
      } else if (c.type === "tool_result") {
        throw new ConduitError("InvalidRequestError", "Only tool messages may contain tool_result parts.");
      }
    }
    // Anthropic messages must have role user or assistant
    if (role !== "user" && role !== "assistant") protocol("Invalid message role for Anthropic.");
    // Content as string shorthand if single text block and no tool_use
    const hasToolUse = blocks.some(b => (b as Record<string, unknown>).type === "tool_use");
    if (!hasToolUse && blocks.length === 1 && (blocks[0] as Record<string, unknown>).type === "text") {
      anthropicMessages.push({ role, content: (blocks[0] as Record<string, unknown>).text });
    } else {
      anthropicMessages.push({ role, content: blocks });
    }
  }

  // System handling
  let system: unknown = undefined;
  if (systemParts.length === 1) system = systemParts[0];
  else if (systemParts.length > 1) system = systemParts.map(t => ({ type: "text", text: t }));

  const maxTokens = request.maxOutputTokens;
  if (maxTokens === undefined) {
    throw new ConduitError("InvalidRequestError", "maxOutputTokens is required for Anthropic.");
  }
  const wireMaxTokens = maxTokens;

  const body: Record<string, unknown> = {
    ...native,
    model,
    messages: anthropicMessages,
    stream: streaming,
    max_tokens: wireMaxTokens,
    ...(system !== undefined && { system }),
    ...(request.temperature !== undefined && { temperature: request.temperature }),
    ...(request.topP !== undefined && { top_p: request.topP }),
    ...(request.stop !== undefined && { stop_sequences: Array.from(request.stop) }),
    ...(wireTools !== undefined && { tools: wireTools }),
    ...(wireToolChoice !== undefined && { tool_choice: wireToolChoice }),
  };
  return JSON.stringify(body);
}

function usageOf(value: Record<string, unknown>): Usage | undefined {
  const usage: Usage = {};
  // Anthropic usage: input_tokens, output_tokens
  if (value.input_tokens !== undefined) {
    if (typeof value.input_tokens !== "number" || !Number.isSafeInteger(value.input_tokens) || value.input_tokens < 0) protocol();
    usage.inputTokens = value.input_tokens;
  }
  if (value.output_tokens !== undefined) {
    if (typeof value.output_tokens !== "number" || !Number.isSafeInteger(value.output_tokens) || value.output_tokens < 0) protocol();
    usage.outputTokens = value.output_tokens;
  }
  // Some responses include cache_creation_input_tokens etc - ignore but preserve in metadata
  return Object.keys(usage).length ? usage : undefined;
}

function finishReasonOf(stop: unknown): GenerationResponse["finishReason"] {
  if (stop === "end_turn" || stop === "stop_sequence") return "stop";
  if (stop === "max_tokens") return "length";
  if (stop === "tool_use") return "tool_call";
  if (stop === "refusal") return "content_filter";
  // pause_turn, model_context_window_exceeded etc
  return "other";
}

export function anthropicResponse(input: unknown, requestId: string | undefined, redact: (text: string) => string): GenerationResponse {
  if (!object(input)) protocol();
  if (object((input as Record<string, unknown>).error)) {
    // Error shape handled via anthropicError but non-200 already throws; 200 with error type still error
    throw anthropicError(200, JSON.stringify(input), requestId, redact);
  }
  const v = input as Record<string, unknown>;
  if (v.type !== "message" || v.role !== "assistant") protocol();
  if (typeof v.id !== "string" || typeof v.model !== "string") protocol();
  if (!Array.isArray(v.content)) protocol();
  if (v.stop_reason !== null && typeof v.stop_reason !== "string") protocol();
  if (v.stop_sequence !== null && v.stop_sequence !== undefined && typeof v.stop_sequence !== "string") protocol();
  const contentArr = v.content as unknown[];
  const content: GenerationResponse["content"] = [];
  let hasToolUse = false;
  const nativeThinking: unknown[] = [];
  for (const block of contentArr) {
    if (!object(block) || typeof block.type !== "string") protocol();
    if (block.type === "text") {
      if (typeof block.text !== "string") protocol();
      // Allow empty text? Anthropic text minLength 1, but preserve
      content.push({ type: "text", text: block.text as string });
    } else if (block.type === "tool_use") {
      if (typeof block.id !== "string" || typeof block.name !== "string" || block.input === undefined) protocol();
      // input must be object
      if (block.input !== null && typeof block.input !== "object") protocol();
      try { JSON.stringify(block.input); } catch { protocol(); }
      hasToolUse = true;
      content.push({ type: "tool_call", id: block.id as string, name: block.name as string, arguments: block.input as import("./types.js").JsonValue });
    } else if (block.type === "thinking" || block.type === "redacted_thinking") {
      // Provider-native thinking: validate minimal shape, preserve in metadata, do not fabricate normalized events
      if (block.type === "thinking") {
        if (typeof block.thinking !== "string") protocol();
        if (block.signature !== undefined && typeof block.signature !== "string") protocol();
      } else {
        if (typeof block.data !== "string") protocol();
      }
      // preserve raw block for metadata
      try { JSON.stringify(block); } catch { protocol(); }
      nativeThinking.push(block);
      continue;
    } else {
      // Unknown block type -> ProtocolError to avoid silent loss
      protocol();
    }
  }
  // Anthropic may return empty content with stop_reason? Validate at least one block or allow empty?
  // Allow empty content but fine.
  const usage = object(v.usage) ? usageOf(v.usage as Record<string, unknown>) : undefined;
  const metadata: GenerationResponse["providerMetadata"] = {};
  if (requestId !== undefined) metadata.requestId = requestId;
  if (typeof v.stop_reason === "string") metadata.finishReason = redact(v.stop_reason as string);
  if (typeof v.stop_sequence === "string" && v.stop_sequence) metadata.stop_sequence = redact(v.stop_sequence as string);
  // Preserve cache tokens etc in metadata if present
  if (object(v.usage)) {
    const u = v.usage as Record<string, unknown>;
    for (const k of ["cache_creation_input_tokens", "cache_read_input_tokens"] ) {
      if (u[k] !== undefined) {
        if (typeof u[k] !== "number" || !Number.isSafeInteger(u[k] as number) || (u[k] as number) < 0) protocol();
        metadata[k] = u[k] as import("./types.js").JsonValue;
      }
    }
    if (u.cache_creation !== undefined && u.cache_creation !== null) {
      try { JSON.stringify(u.cache_creation); metadata.cache_creation = u.cache_creation as import("./types.js").JsonValue; } catch { /* ignore */ }
    }
  }
  if (nativeThinking.length) {
    metadata.thinking = nativeThinking as import("./types.js").JsonValue;
  }
  const finish = finishReasonOf(v.stop_reason);
  // If stop_reason is tool_use but no tool_use block, still tool_call; else mapped
  const finalFinish = hasToolUse ? "tool_call" as const : finish;
  return textResponse({
    ...(typeof v.id === "string" && { id: redact(v.id) }),
    ...(typeof v.model === "string" && { model: redact(v.model) }),
    content,
    finishReason: finalFinish,
    ...(usage && { usage }),
    providerMetadata: metadata,
  });
}

export function anthropicError(status: number, body: string, requestId: string | undefined, redact: (text: string) => string): ConduitError {
  const details: NonNullable<import("./errors.js").ErrorDetails["providerDetails"]> = {};
  let providerCode: string | undefined;
  try {
    const value: unknown = JSON.parse(body);
    if (object(value)) {
      // Anthropic error shape: {type:"error", error:{type:"...", message:"..."}}
      const errObj = object(value.error) ? value.error as Record<string, unknown> : value as Record<string, unknown>;
      if (object(value.error)) {
        if (typeof errObj.type === "string") { details.type = redact(errObj.type); providerCode = errObj.type; }
        if (typeof errObj.message === "string") details.message = redact(errObj.message);
        if (typeof errObj.code === "string") details.code = redact(errObj.code);
      } else {
        if (typeof (value as Record<string, unknown>).type === "string") details.type = redact((value as Record<string, unknown>).type as string);
        if (typeof (value as Record<string, unknown>).message === "string") details.message = redact((value as Record<string, unknown>).message as string);
      }
      // request_id may be in body
      if (typeof (value as Record<string, unknown>).request_id === "string" && requestId === undefined) {
        requestId = redact((value as Record<string, unknown>).request_id as string);
      }
    }
  } catch { /* preserve HTTP status */ }
  return httpFailure(status, details, requestId, false);
}

// SSE parsing for Anthropic (same framing as OpenAI but event names matter)
async function* sseEvents(body: ReadableStream<Uint8Array>): AsyncGenerator<{ event: string; data: string }> {
  const reader = body.getReader();
  const decoder = new TextDecoder("utf-8", { fatal: true });
  let line = "";
  let event = "";
  let data: string[] = [];
  let skipLF = false;
  try {
    while (true) {
      const { value, done } = await reader.read();
      let text: string;
      try { text = decoder.decode(value, { stream: !done }); } catch { protocol("Malformed or incomplete Anthropic stream."); }
      for (const char of text) {
        if (skipLF && char === "\n") { skipLF = false; continue; }
        skipLF = char === "\r";
        if (char !== "\r" && char !== "\n") { line += char; continue; }
        if (!line) {
          if (data.length || event) {
            const joined = data.join("\n");
            const ev = event || "";
            event = "";
            data = [];
            if (joined || ev) yield { event: ev, data: joined };
          }
          // else empty line without data: ignore
        } else {
          const colon = line.indexOf(":");
          const field = colon < 0 ? line : line.slice(0, colon);
          let val = colon < 0 ? "" : line.slice(colon + 1);
          if (val.startsWith(" ")) val = val.slice(1);
          if (field === "event") event = val;
          else if (field === "data") data.push(val);
          // ignore other fields like id, retry
        }
        line = "";
      }
      if (done) {
        // SSE does not dispatch unterminated events at EOF
        return;
      }
    }
  } finally {
    try { await reader.cancel(); } catch { }
    reader.releaseLock();
  }
}

export async function* anthropicStream(
  body: ReadableStream<Uint8Array>,
  requestId: string | undefined,
  redact: (text: string) => string,
): AsyncGenerator<StreamEvent> {
  let started = false;
  let id: string | undefined;
  let model: string | undefined;
  let stopReason: string | undefined;
  let usage: Usage | undefined;
  const toolAccum = new Map<number, { id?: string; name?: string; inputJson: string }>();

  // For final accumulation we need to build content blocks in order
  const finalBlocks: Map<number, Record<string, unknown>> = new Map();

  for await (const { event, data } of sseEvents(body)) {
    if (!data) {
      // ping events may have no data? ignore
      continue;
    }
    let value: unknown;
    try { value = JSON.parse(data); } catch { protocol("Malformed or incomplete Anthropic stream."); }
    if (!object(value)) protocol("Malformed or incomplete Anthropic stream.");

    // Handle error events (event: error) whose data type is "error"
    if (value.type === "error" || event === "error") {
      const errBody = JSON.stringify(value);
      // Throw as provider error with 200 status (in-band)
      throw anthropicError(200, errBody, requestId, redact);
    }
    const type = value.type as string;

    if (type === "message_start") {
      if (started) protocol("Malformed or incomplete Anthropic stream.");
      const msg = (value as Record<string, unknown>).message as Record<string, unknown> | undefined;
      if (!object(msg) || typeof msg.id !== "string" || typeof msg.model !== "string") protocol("Malformed or incomplete Anthropic stream.");
      id = msg.id as string;
      model = msg.model as string;
      // usage
      if (object(msg.usage)) {
        const u = usageOf(msg.usage as Record<string, unknown>);
        if (u) usage = { ...usage, ...u };
      }
      started = true;
      yield { type: "start", ...(id !== undefined && { id: redact(id) }), ...(model !== undefined && { model: redact(model) }) };
      if (usage) yield { type: "usage", usage: { ...usage } };
      continue;
    }
    if (!started && type !== "ping") {
      // Must start with message_start
      protocol("Malformed or incomplete Anthropic stream.");
    }
    if (type === "ping") {
      continue;
    }
    if (type === "content_block_start") {
      const idx = (value as Record<string, unknown>).index as unknown;
      if (typeof idx !== "number" || !Number.isInteger(idx) || idx < 0) protocol("Malformed or incomplete Anthropic stream.");
      const block = (value as Record<string, unknown>).content_block as unknown;
      if (!object(block) || typeof block.type !== "string") protocol("Malformed or incomplete Anthropic stream.");
      if (block.type === "text") {
        finalBlocks.set(idx as number, { type: "text", text: (typeof block.text === "string" ? block.text as string : "") });
      } else if (block.type === "tool_use") {
        if (typeof block.id !== "string" || typeof block.name !== "string") protocol("Malformed or incomplete Anthropic stream.");
        toolAccum.set(idx as number, { id: block.id as string, name: block.name as string, inputJson: "" });
        finalBlocks.set(idx as number, { type: "tool_use", id: block.id as string, name: block.name as string, input: {} });
        // Emit tool_call_delta with id/name upfront
        yield { type: "tool_call_delta", index: idx as number, id: block.id as string, name: block.name as string };
      } else if (block.type === "thinking" || block.type === "redacted_thinking") {
        // Provider-native thinking: validate and preserve, do not fabricate normalized events
        if (block.type === "thinking") {
          if (typeof block.thinking !== "string") protocol("Malformed or incomplete Anthropic stream.");
          if (block.signature !== undefined && typeof block.signature !== "string") protocol("Malformed or incomplete Anthropic stream.");
        } else {
          if (typeof block.data !== "string") protocol("Malformed or incomplete Anthropic stream.");
        }
        try { JSON.stringify(block); } catch { protocol("Malformed or incomplete Anthropic stream."); }
        finalBlocks.set(idx as number, { type: block.type as string, ...(block.type === "thinking" ? { thinking: block.thinking as string, signature: block.signature as string | undefined } : { data: block.data as string }) } as Record<string, unknown> as any );
      } else {
        protocol("Malformed or incomplete Anthropic stream.");
      }
      continue;
    }
    if (type === "content_block_delta") {
      const idx = (value as Record<string, unknown>).index as unknown;
      if (typeof idx !== "number" || !Number.isInteger(idx) || idx < 0) protocol("Malformed or incomplete Anthropic stream.");
      const delta = (value as Record<string, unknown>).delta as unknown;
      if (!object(delta) || typeof delta.type !== "string") protocol("Malformed or incomplete Anthropic stream.");
      const dType = delta.type as string;
      if (dType === "text_delta") {
        if (typeof delta.text !== "string") protocol("Malformed or incomplete Anthropic stream.");
        const text = delta.text as string;
        const existing = finalBlocks.get(idx as number);
        if (existing && existing.type === "text") existing.text = (existing.text ?? "") + text;
        else finalBlocks.set(idx as number, { type: "text", text });
        yield { type: "text_delta", index: 0, text };
      } else if (dType === "input_json_delta") {
        if (typeof delta.partial_json !== "string") protocol("Malformed or incomplete Anthropic stream.");
        const frag = delta.partial_json as string;
        const cur = toolAccum.get(idx as number);
        if (!cur) protocol("Malformed or incomplete Anthropic stream.");
        cur.inputJson += frag;
        // Update finalBlocks input accumulation (keep string for later JSON parse validation at done)
        const blk = finalBlocks.get(idx as number);
        if (blk) (blk as Record<string, unknown>).inputJson = cur.inputJson;
        yield { type: "tool_call_delta", index: idx as number, argumentsDelta: frag };
      } else if (dType === "thinking_delta") {
        if (typeof delta.thinking !== "string") protocol("Malformed or incomplete Anthropic stream.");
        // preserve in thinking block
        const blk = finalBlocks.get(idx as number);
        if (!blk || blk.type !== "thinking") protocol("Malformed or incomplete Anthropic stream.");
        blk.thinking = (blk.thinking ?? "") + (delta.thinking as string);
        try { JSON.stringify(blk); } catch { protocol("Malformed or incomplete Anthropic stream."); }
        // do not fabricate normalized text/tool events
        continue;
      } else if (dType === "signature_delta") {
        if (typeof delta.signature !== "string") protocol("Malformed or incomplete Anthropic stream.");
        const blk = finalBlocks.get(idx as number);
        if (!blk || blk.type !== "thinking") protocol("Malformed or incomplete Anthropic stream.");
        blk.signature = delta.signature as string;
        try { JSON.stringify(blk); } catch { protocol("Malformed or incomplete Anthropic stream."); }
        continue;
      } else {
        protocol("Malformed or incomplete Anthropic stream.");
      }
      continue;
    }
    if (type === "content_block_stop") {
      const idx = (value as Record<string, unknown>).index as unknown;
      if (typeof idx !== "number" || !Number.isInteger(idx) || idx < 0) protocol("Malformed or incomplete Anthropic stream.");
      // Optionally validate tool JSON completeness here, but defer to final assembly
      continue;
    }
    if (type === "message_delta") {
      const delta = (value as Record<string, unknown>).delta as unknown;
      if (!object(delta)) protocol("Malformed or incomplete Anthropic stream.");
      if (delta.stop_reason !== null && delta.stop_reason !== undefined && typeof delta.stop_reason !== "string") protocol("Malformed or incomplete Anthropic stream.");
      if (typeof delta.stop_reason === "string") stopReason = delta.stop_reason as string;
      const u = (value as Record<string, unknown>).usage as unknown;
      if (object(u)) {
        const parsed = usageOf(u as Record<string, unknown>);
        if (parsed) {
          usage = { ...usage, ...parsed };
          yield { type: "usage", usage: { ...usage } };
        }
      }
      continue;
    }
    if (type === "message_stop") {
      if (stopReason === undefined) {
        // Anthropic may send stop_reason via message_delta, but if missing, protocol error
        protocol("Malformed or incomplete Anthropic stream.");
      }
      // Build final response
      // Sort blocks by index
      const sorted = [...finalBlocks.entries()].sort((a, b) => a[0] - b[0]);
      const contentBlocks: unknown[] = [];
      for (const [, blk] of sorted) {
        if (blk.type === "text") {
          contentBlocks.push({ type: "text", text: blk.text ?? "" });
        } else if (blk.type === "tool_use") {
          const acc = toolAccum.get(sorted.find(([k]) => finalBlocks.get(k) === blk)?.[0] ?? -1) ?? toolAccum.get([...toolAccum.keys()][0] as number);
          // Find index
          const idxEntry = [...finalBlocks.entries()].find(([, v]) => v === blk);
          const idx = idxEntry ? idxEntry[0] : 0;
          const cur = toolAccum.get(idx);
          const inputStr = cur?.inputJson ?? "";
          let input: unknown = {};
          if (inputStr) {
            try { input = JSON.parse(inputStr); } catch { protocol("Malformed or incomplete Anthropic stream."); }
          }
          contentBlocks.push({ type: "tool_use", id: cur?.id ?? blk.id, name: cur?.name ?? blk.name, input });
        } else if (blk.type === "thinking" || blk.type === "redacted_thinking") {
          // Preserve native thinking blocks for anthropicResponse metadata
          if (blk.type === "thinking") {
            contentBlocks.push({ type: "thinking", thinking: blk.thinking ?? "", ...(blk.signature !== undefined && { signature: blk.signature }) });
          } else {
            contentBlocks.push({ type: "redacted_thinking", data: blk.data ?? "" });
          }
        }
      }
      // If no blocks observed, keep empty
      const responseInput: Record<string, unknown> = {
        type: "message",
        role: "assistant",
        id: id ?? "msg_unknown",
        model: model ?? "unknown",
        content: contentBlocks,
        stop_reason: stopReason,
        stop_sequence: null,
        usage: usage ? { input_tokens: usage.inputTokens ?? 0, output_tokens: usage.outputTokens ?? 0 } : { input_tokens: 0, output_tokens: 0 },
      };
      const response = anthropicResponse(responseInput, requestId, redact);
      yield { type: "done", response };
      return;
    }
    // Unknown types should be ignored or protocol error? Docs say handle unknown gracefully; ignore
    // But if unknown type is not ping/error, we ignore
  }
  protocol("Malformed or incomplete Anthropic stream.");
}
