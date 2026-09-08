import { ConduitError, httpFailure } from "./errors.js";
import { object, textResponse } from "./response.js";
import type { GenerationRequest, GenerationResponse, StreamEvent, Usage } from "./types.js";

function protocol(msg = "Malformed or unsupported Gemini response."): never {
  throw new ConduitError("ProtocolError", msg);
}

export function encodeGeminiTools(tools: readonly import("./types.js").ToolDefinition[] | undefined): unknown[] | undefined {
  if (tools === undefined) return undefined;
  return [{ functionDeclarations: tools.map(t => ({ name: t.name, ...(t.description !== undefined && { description: t.description }), parametersJsonSchema: t.inputSchema })) }];
}

export function encodeGeminiToolChoice(choice: unknown): unknown {
  if (choice === undefined) return undefined;
  if (choice === "auto") return { functionCallingConfig: { mode: "AUTO" } };
  if (choice === "none") return { functionCallingConfig: { mode: "NONE" } };
  if (choice === "required") return { functionCallingConfig: { mode: "ANY" } };
  if (typeof choice === "string") return { functionCallingConfig: { mode: "ANY", allowedFunctionNames: [choice] } };
  if (object(choice) && typeof choice.name === "string") return { functionCallingConfig: { mode: "ANY", allowedFunctionNames: [choice.name] } };
  return choice;
}

export function encodeGeminiFormat(value: unknown): unknown | undefined {
  if (value === undefined) return undefined;
  const fmt = value as Record<string, unknown>;
  if (fmt.type === "text") return undefined;
  if (fmt.type === "json") return { responseMimeType: "application/json" };
  return { responseMimeType: "application/json", responseJsonSchema: fmt.schema };
}

export function geminiRequest(
  messages: { role: string; content: unknown[] }[],
  request: GenerationRequest,
  wireTools?: unknown,
  wireToolChoice?: unknown,
  wireFormat?: unknown,
): string {
  const native = (request.providerOptions ?? {}) as Record<string, unknown>;
  const owned = ["contents", "systemInstruction", "generationConfig", "tools", "toolConfig", "safetySettings", "cachedContent"];
  if (owned.some(k => Object.hasOwn(native, k))) throw new ConduitError("InvalidRequestError", "providerOptions conflicts with a Conduit-owned field.");
  if (object(native.generationConfig)) {
    const gc = native.generationConfig as Record<string, unknown>;
    if (["maxOutputTokens","temperature","topP","top_p","stopSequences","responseMimeType","responseSchema","responseJsonSchema","candidateCount"].some(k=> Object.hasOwn(gc,k)))
      throw new ConduitError("InvalidRequestError", "providerOptions.generationConfig conflicts with Conduit-owned field.");
  }
  const systemParts: string[] = [];
  const contents: unknown[] = [];
  let seenNonSystem = false;
  for (const m of messages) {
    const role = m.role as string;
    const parts = m.content as unknown[];
    if (role === "system") {
      if (seenNonSystem) throw new ConduitError("InvalidRequestError", "System messages must be leading.");
      for (const p of parts) {
        const c = p as Record<string, unknown>;
        if (c.type === "text") systemParts.push(c.text as string);
        else throw new ConduitError("InvalidRequestError", "System messages must not contain tool content.");
      }
      continue;
    }
    seenNonSystem = true;
    if (role === "tool") {
      const functionResponses: unknown[] = [];
      for (const p of parts) {
        const c = p as Record<string, unknown>;
        if (c.type !== "tool_result") throw new ConduitError("InvalidRequestError", "Tool messages must contain tool_result parts.");
        if (typeof c.name !== "string" || !c.name.trim()) throw new ConduitError("InvalidRequestError", "Tool result name is required for Gemini.");
        const inner = c.content as unknown;
        const text = typeof inner === "string" ? inner as string : ((inner as unknown[]).map((q:unknown)=> (q as Record<string,unknown>).text as string).join(""));
        let responseObj: unknown;
        try {
          const parsed = JSON.parse(text);
          responseObj = typeof parsed === "object" && parsed !== null ? parsed : { result: text };
        } catch {
          responseObj = { result: text };
        }
        functionResponses.push({ functionResponse: { ...(c.callId ? { id: c.callId as string } : {}), name: c.name, response: responseObj } });
      }
      contents.push({ role: "user", parts: functionResponses });
      continue;
    }
    const partsOut: unknown[] = [];
    for (const p of parts) {
      const c = p as Record<string, unknown>;
      if (c.type === "text") {
        partsOut.push({ text: c.text });
      } else if (c.type === "tool_call") {
        if (role !== "assistant") throw new ConduitError("InvalidRequestError", "Only assistant messages may contain tool_call parts.");
        if (typeof c.name !== "string" || !c.name.trim()) throw new ConduitError("InvalidRequestError", "Tool call name required.");
        let args = c.arguments;
        if (args === null || typeof args !== "object") {
          try { args = JSON.parse(String(args)); } catch { args = {}; }
        }
        partsOut.push({ functionCall: { ...(c.id ? { id: c.id as string } : {}), name: c.name, args } });
      } else if (c.type === "tool_result") {
        throw new ConduitError("InvalidRequestError", "Only tool messages may contain tool_result parts.");
      }
    }
    const geminiRole = role === "assistant" ? "model" : role;
    if (geminiRole !== "user" && geminiRole !== "model") protocol("Invalid role for Gemini.");
    contents.push({ role: geminiRole, parts: partsOut });
  }

  const body: Record<string, unknown> = { ...native, contents };
  if (systemParts.length) body.systemInstruction = { parts: systemParts.map(t=> ({text:t})) };
  const generationConfig: Record<string, unknown> = {};
  if (request.maxOutputTokens !== undefined) generationConfig.maxOutputTokens = request.maxOutputTokens;
  if (request.temperature !== undefined) generationConfig.temperature = request.temperature;
  if (request.topP !== undefined) generationConfig.topP = request.topP;
  if (request.stop !== undefined) generationConfig.stopSequences = Array.from(request.stop);
  if (wireFormat !== undefined) {
    const fmt = wireFormat as Record<string, unknown>;
    if (fmt.responseMimeType) generationConfig.responseMimeType = fmt.responseMimeType;
    if (fmt.responseJsonSchema) generationConfig.responseJsonSchema = fmt.responseJsonSchema;
  }
  if (Object.keys(generationConfig).length) body.generationConfig = generationConfig;
  if (wireTools !== undefined) body.tools = wireTools;
  if (wireToolChoice !== undefined) body.toolConfig = wireToolChoice;
  return JSON.stringify(body);
}

function usageFromMetadata(meta: Record<string, unknown> | undefined): Usage | undefined {
  if (!meta) return undefined;
  const usage: Usage = {};
  if (typeof meta.promptTokenCount === "number" && Number.isSafeInteger(meta.promptTokenCount) && meta.promptTokenCount>=0) usage.inputTokens = meta.promptTokenCount;
  if (typeof meta.candidatesTokenCount === "number" && Number.isSafeInteger(meta.candidatesTokenCount) && meta.candidatesTokenCount>=0) usage.outputTokens = meta.candidatesTokenCount;
  if (typeof meta.totalTokenCount === "number" && Number.isSafeInteger(meta.totalTokenCount) && meta.totalTokenCount>=0) usage.totalTokens = meta.totalTokenCount;
  return Object.keys(usage).length ? usage : undefined;
}

function finishReasonOfGemini(fr: unknown): GenerationResponse["finishReason"] {
  if (fr === "STOP") return "stop";
  if (fr === "MAX_TOKENS") return "length";
  if (fr === "SAFETY" || fr === "RECITATION" || fr === "BLOCKLIST" || fr === "PROHIBITED_CONTENT" || fr === "SPII" || fr === "IMAGE_SAFETY") return "content_filter";
  return "other";
}

export function geminiResponse(input: unknown, requestId: string | undefined, redact: (t:string)=>string): GenerationResponse {
  if (!object(input)) protocol();
  if (object((input as Record<string,unknown>).error)) throw geminiError(200, JSON.stringify(input), requestId, redact);
  const v = input as Record<string, unknown>;
  const candidates = v.candidates as unknown;
  if (!Array.isArray(candidates) || candidates.length === 0) {
    const pf = v.promptFeedback as Record<string,unknown>|undefined;
    if (pf && typeof pf.blockReason === "string") {
      const usage = usageFromMetadata(v.usageMetadata as Record<string,unknown>|undefined);
      const metadata: GenerationResponse["providerMetadata"] = {};
      if (requestId) metadata.requestId = requestId;
      if (typeof v.modelVersion === "string") metadata.modelVersion = redact(v.modelVersion);
      if (typeof v.responseId === "string") metadata.responseId = redact(v.responseId);
      metadata.finishReason = redact(pf.blockReason as string);
      return textResponse({ content: [], finishReason: "content_filter", ...(usage&&{usage}), providerMetadata: metadata });
    }
    protocol();
  }
  const candidate = candidates[0] as Record<string,unknown>;
  if (!object(candidate) || !object(candidate.content)) protocol();
  const content = candidate.content as Record<string,unknown>;
  const parts = content.parts as unknown;
  if (!Array.isArray(parts)) protocol();
  const normalized: GenerationResponse["content"] = [];
  let hasFunctionCall = false;
  const nativeParts: unknown[] = [];
  for (const part of parts) {
    if (!object(part)) protocol();
    if (typeof part.text === "string") {
      normalized.push({ type: "text", text: part.text });
    } else if (object(part.functionCall)) {
      const fc = part.functionCall as Record<string,unknown>;
      if (typeof fc.name !== "string" || !fc.name.trim()) protocol();
      if (fc.id !== undefined && typeof fc.id !== "string") protocol();
      let args: unknown = fc.args ?? {};
      try { JSON.stringify(args); } catch { protocol(); }
      hasFunctionCall = true;
      normalized.push({ type: "tool_call", ...(typeof fc.id === "string" && fc.id ? { id: fc.id } : {}), name: fc.name as string, arguments: args as import("./types.js").JsonValue });
    } else if (object(part.functionResponse)) {
      continue;
    } else if (part.thought === true || typeof part.thoughtSignature === "string" || object(part.executableCode) || object(part.codeExecutionResult)) {
      // Native thinking/code fields are metadata on Part – preserve without turning into text
      nativeParts.push(part);
      continue;
    } else if (Object.keys(part).length===0) {
      protocol();
    } else {
      // Unknown but valid native field containing thought metadata – preserve
      if (part.thought === true || typeof (part as Record<string,unknown>).thoughtSignature === "string") {
        nativeParts.push(part);
        continue;
      }
      protocol();
    }
  }
  const finishRaw = candidate.finishReason as unknown;
  let finish: GenerationResponse["finishReason"] = finishRaw ? finishReasonOfGemini(finishRaw) : (normalized.length ? "stop" : "other");
  if (hasFunctionCall && finish !== "content_filter") finish = "tool_call";
  const usage = usageFromMetadata(v.usageMetadata as Record<string,unknown>|undefined);
  const metadata: GenerationResponse["providerMetadata"] = {};
  if (requestId) metadata.requestId = requestId;
  if (typeof finishRaw === "string") metadata.finishReason = redact(finishRaw);
  if (typeof v.modelVersion === "string") metadata.modelVersion = redact(v.modelVersion);
  if (typeof v.responseId === "string") metadata.responseId = redact(v.responseId);
  if (object(v.usageMetadata)) {
    const um = v.usageMetadata as Record<string,unknown>;
    for (const k of ["cachedContentTokenCount","thoughtsTokenCount","toolUsePromptTokenCount"] ) {
      if (um[k] !== undefined && typeof um[k]==="number" && Number.isSafeInteger(um[k] as number) && (um[k] as number)>=0) metadata[k] = um[k] as import("./types.js").JsonValue;
    }
  }
  if (candidate.safetyRatings !== undefined) {
    try { JSON.stringify(candidate.safetyRatings); metadata.safetyRatings = candidate.safetyRatings as import("./types.js").JsonValue; } catch {}
  }
  if (nativeParts.length) metadata.thinking = nativeParts as import("./types.js").JsonValue;
  return textResponse({
    ...(typeof v.responseId === "string" && { id: redact(v.responseId) }),
    ...(typeof v.modelVersion === "string" && { model: redact(v.modelVersion) }),
    content: normalized,
    finishReason: finish,
    ...(usage && { usage }),
    providerMetadata: metadata,
  });
}

export function geminiError(status:number, body:string, requestId:string|undefined, redact:(t:string)=>string): ConduitError {
  const details: NonNullable<import("./errors.js").ErrorDetails["providerDetails"]> = {};
  try {
    const value:unknown = JSON.parse(body);
    if (object(value)) {
      const err = object(value.error) ? value.error as Record<string,unknown> : value as Record<string,unknown>;
      if (typeof err.message === "string") details.message = redact(err.message);
      if (typeof err.status === "string") details.type = redact(err.status);
      if (typeof err.code === "number") details.code = String(err.code);
      if (typeof (err as Record<string,unknown>).reason === "string") details.type = redact((err as Record<string,unknown>).reason as string);
    }
  } catch {}
  return httpFailure(status, details, requestId, false);
}

async function* sseData(body: ReadableStream<Uint8Array>): AsyncGenerator<string> {
  const reader = body.getReader();
  const decoder = new TextDecoder("utf-8", { fatal: true });
  let line = "";
  let data: string[] = [];
  let skipLF = false;
  try {
    while (true) {
      const {value, done} = await reader.read();
      let text: string;
      try { text = decoder.decode(value, {stream: !done}); } catch { throw new ConduitError("ProtocolError","Malformed or incomplete Gemini stream."); }
      for (const ch of text) {
        if (skipLF && ch === "\n") { skipLF=false; continue; }
        skipLF = ch === "\r";
        if (ch !== "\r" && ch !== "\n") { line += ch; continue; }
        if (!line) {
          if (data.length) {
            const joined = data.join("\n");
            data = [];
            if (joined) yield joined;
          }
        } else {
          const colon = line.indexOf(":");
          const field = colon<0 ? line : line.slice(0,colon);
          let val = colon<0 ? "" : line.slice(colon+1);
          if (val.startsWith(" ")) val = val.slice(1);
          if (field === "data") data.push(val);
        }
        line = "";
      }
      if (done) return;
    }
  } finally {
    try { await reader.cancel(); } catch {}
    reader.releaseLock();
  }
}

export async function* geminiStream(body: ReadableStream<Uint8Array>, requestId:string|undefined, redact:(t:string)=>string, contentType:string|null): AsyncGenerator<StreamEvent> {
  const isSSE = contentType ? contentType.includes("text/event-stream") : false;
  let started = false;
  let usage: Usage|undefined;
  let aggregated: unknown[] = [];
  let lastRaw: Record<string,unknown> | null = null;
  let finishReason: string | undefined;

  if (isSSE) {
    for await (const data of sseData(body)) {
      if (!data || data === "[DONE]" || data.trim()==="") continue;
      let obj: unknown;
      try { obj = JSON.parse(data); } catch { throw new ConduitError("ProtocolError","Malformed or incomplete Gemini stream."); }
      if (!object(obj)) throw new ConduitError("ProtocolError","Malformed or incomplete Gemini stream.");
      if (object((obj as Record<string,unknown>).error)) throw geminiError(200, JSON.stringify(obj), requestId, redact);
      const candidates = (obj as Record<string,unknown>).candidates as unknown;
      if (candidates !== undefined && !Array.isArray(candidates)) throw new ConduitError("ProtocolError","Malformed or incomplete Gemini stream.");
      if (!started) {
        started = true;
        const mid = (obj as Record<string,unknown>).modelVersion as string|undefined;
        const rid = (obj as Record<string,unknown>).responseId as string|undefined;
        yield { type: "start", ...(rid!==undefined && {id: redact(rid)}), ...(mid!==undefined && {model: redact(mid)}) } as StreamEvent;
      }
      if (Array.isArray(candidates) && candidates.length) {
        const cand = candidates[0] as Record<string,unknown>;
        if (cand.finishReason) finishReason = cand.finishReason as string;
        const content = cand.content as Record<string,unknown>|undefined;
        const parts = content?.parts as unknown[]|undefined;
        if (Array.isArray(parts)) {
          for (const part of parts) {
            if (!object(part)) throw new ConduitError("ProtocolError","Malformed or incomplete Gemini stream.");
            if (typeof part.text === "string" && part.text) {
              aggregated.push(part);
              yield { type: "text_delta", index: 0, text: part.text } as StreamEvent;
            } else if (object(part.functionCall)) {
              const fc = part.functionCall as Record<string,unknown>;
              if (typeof fc.name !== "string") throw new ConduitError("ProtocolError","Malformed or incomplete Gemini stream.");
              if (fc.id !== undefined && typeof fc.id !== "string") throw new ConduitError("ProtocolError","Malformed or incomplete Gemini stream.");
              const argsStr = (()=>{ try { return JSON.stringify(fc.args ?? {});} catch { return "{}"; } })();
              aggregated.push(part);
              const idx = aggregated.filter(p=> object(p) && object((p as Record<string,unknown>).functionCall)).length -1;
              yield { type: "tool_call_delta", index: idx, ...(typeof fc.id === "string" && fc.id ? { id: fc.id } : {}), name: fc.name as string, argumentsDelta: argsStr } as StreamEvent;
            } else if (part.thought === true || typeof part.thoughtSignature === "string" || object(part.executableCode)) {
              aggregated.push(part);
              continue;
            } else if (Object.keys(part).length===0) {
              throw new ConduitError("ProtocolError","Malformed or incomplete Gemini stream.");
            } else {
              aggregated.push(part);
            }
          }
        }
      }
      const um = (obj as Record<string,unknown>).usageMetadata as Record<string,unknown>|undefined;
      if (object(um)) {
        const u = usageFromMetadata(um);
        if (u) {
          usage = { ...usage, ...u };
          yield { type: "usage", usage: { ...usage } } as StreamEvent;
        }
      }
      lastRaw = obj as Record<string,unknown>;
      if (finishReason) {
        // Check if this is final chunk – Gemini stream ends with finishReason; we can wait for EOF and then emit done
        // But we emit done only when stream ends (EOF) to allow more chunks; continue loop
      }
    }
    if (!started) throw new ConduitError("ProtocolError","Malformed or incomplete Gemini stream.");
    // Build final response from aggregated
    const finalParts = aggregated;
    const finalInput: Record<string,unknown> = {
      candidates: [{ content: { parts: finalParts }, finishReason: finishReason ?? "STOP" }],
      usageMetadata: usage ? { promptTokenCount: usage.inputTokens ?? 0, candidatesTokenCount: usage.outputTokens ?? 0, totalTokenCount: usage.totalTokens ?? ((usage.inputTokens??0)+(usage.outputTokens??0)) } : undefined,
      ...(lastRaw?.responseId ? { responseId: lastRaw.responseId } : {}),
      ...(lastRaw?.modelVersion ? { modelVersion: lastRaw.modelVersion } : {}),
    };
    // Preserve safetyRatings if present in lastRaw
    if (lastRaw && (lastRaw as Record<string,unknown>).candidates) {
      const cand = ((lastRaw as Record<string,unknown>).candidates as unknown[]|undefined)?.[0] as Record<string,unknown>|undefined;
      if (cand?.safetyRatings) (finalInput as Record<string,unknown>).safetyRatings = cand.safetyRatings;
    }
    const response = geminiResponse(finalInput, requestId, redact);
    yield { type: "done", response } as StreamEvent;
    return;
  } else {
    // Fallback: read as JSON lines or single JSON (non-SSE)
    const reader = body.getReader();
    const decoder = new TextDecoder("utf-8", { fatal: true });
    let buf = "";
    let chunks: unknown[] = [];
    try {
      while (true) {
        const {value, done} = await reader.read();
        let text: string;
        try { text = decoder.decode(value, {stream: !done}); } catch { throw new ConduitError("ProtocolError","Malformed or incomplete Gemini stream."); }
        buf += text;
        if (done) break;
        // Try to parse incremental JSON? For chunked JSON, we look for balanced braces?
        // Simpler: if buf contains newline, parse lines
        let idx;
        while ((idx = buf.indexOf("\n")) >= 0) {
          const line = buf.slice(0, idx).trim();
          buf = buf.slice(idx+1);
          if (!line) continue;
          try { chunks.push(JSON.parse(line)); } catch { throw new ConduitError("ProtocolError","Malformed or incomplete Gemini stream."); }
        }
      }
      const rem = buf.trim();
      if (rem) {
        // Could be single JSON or array
        try {
          const parsed = JSON.parse(rem);
          if (Array.isArray(parsed)) chunks.push(...parsed);
          else chunks.push(parsed);
        } catch { throw new ConduitError("ProtocolError","Malformed or incomplete Gemini stream."); }
      }
    } finally {
      try { await reader.cancel(); } catch {}
      reader.releaseLock();
    }
    if (chunks.length===0) throw new ConduitError("ProtocolError","Malformed or incomplete Gemini stream.");
    for (const obj of chunks) {
      if (!object(obj)) throw new ConduitError("ProtocolError","Malformed or incomplete Gemini stream.");
      if (object((obj as Record<string,unknown>).error)) throw geminiError(200, JSON.stringify(obj), requestId, redact);
      const candidates = (obj as Record<string,unknown>).candidates as unknown;
      if (candidates !== undefined && !Array.isArray(candidates)) throw new ConduitError("ProtocolError","Malformed or incomplete Gemini stream.");
      if (!started) {
        started = true;
        const mid = (obj as Record<string,unknown>).modelVersion as string|undefined;
        const rid = (obj as Record<string,unknown>).responseId as string|undefined;
        yield { type: "start", ...(rid!==undefined && {id: redact(rid)}), ...(mid!==undefined && {model: redact(mid)}) } as StreamEvent;
      }
      if (Array.isArray(candidates) && candidates.length) {
        const cand = candidates[0] as Record<string,unknown>;
        if (cand.finishReason) finishReason = cand.finishReason as string;
        const content = cand.content as Record<string,unknown>|undefined;
        const parts = content?.parts as unknown[]|undefined;
        if (Array.isArray(parts)) {
          for (const part of parts) {
            if (!object(part)) throw new ConduitError("ProtocolError","Malformed or incomplete Gemini stream.");
            if (typeof part.text === "string" && part.text) {
              aggregated.push(part);
              yield { type: "text_delta", index: 0, text: part.text } as StreamEvent;
            } else if (object(part.functionCall)) {
              const fc = part.functionCall as Record<string,unknown>;
              if (typeof fc.name !== "string") throw new ConduitError("ProtocolError","Malformed or incomplete Gemini stream.");
              if (fc.id !== undefined && typeof fc.id !== "string") throw new ConduitError("ProtocolError","Malformed or incomplete Gemini stream.");
              const argsStr = (()=>{ try { return JSON.stringify(fc.args ?? {});} catch { return "{}"; } })();
              aggregated.push(part);
              const idx = aggregated.filter(p=> object(p) && object((p as Record<string,unknown>).functionCall)).length -1;
              yield { type: "tool_call_delta", index: idx, ...(typeof fc.id === "string" && fc.id ? { id: fc.id } : {}), name: fc.name as string, argumentsDelta: argsStr } as StreamEvent;
            } else if (typeof part.thought === "string" || typeof part.thoughtSignature === "string" || object(part.executableCode)) {
              aggregated.push(part);
            }
          }
        }
      }
      const um = (obj as Record<string,unknown>).usageMetadata as Record<string,unknown>|undefined;
      if (object(um)) {
        const u = usageFromMetadata(um);
        if (u) {
          usage = { ...usage, ...u };
          yield { type: "usage", usage: { ...usage } } as StreamEvent;
        }
      }
      lastRaw = obj as Record<string,unknown>;
    }
    const finalInput: Record<string,unknown> = {
      candidates: [{ content: { parts: aggregated }, finishReason: finishReason ?? "STOP" }],
      usageMetadata: usage ? { promptTokenCount: usage.inputTokens ?? 0, candidatesTokenCount: usage.outputTokens ?? 0, totalTokenCount: usage.totalTokens } : undefined,
      ...(lastRaw?.responseId ? { responseId: lastRaw.responseId } : {}),
      ...(lastRaw?.modelVersion ? { modelVersion: lastRaw.modelVersion } : {}),
    };
    const response = geminiResponse(finalInput, requestId, redact);
    yield { type: "done", response } as StreamEvent;
    return;
  }
}
