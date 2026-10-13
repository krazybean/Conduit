from __future__ import annotations

import codecs
import json
import urllib.parse
from typing import Any, Dict, Iterator, List, Optional, Tuple

from ..errors import ConduitError, http_failure
from ..types import GenerationResponse, ModelInfo, text_response


def _invalid(msg: str) -> None:
    raise ConduitError("InvalidRequestError", msg)


def _protocol(msg: str = "Malformed or unsupported response.") -> None:
    raise ConduitError("ProtocolError", msg)


def _object(value: Any) -> bool:
    return isinstance(value, dict)


def _encode_tools(tools: Optional[List[Dict[str, Any]]]) -> Optional[List[Dict[str, Any]]]:
    if tools is None:
        return None
    return [{"functionDeclarations": [{"name": t["name"], **({"description": t["description"]} if t.get("description") else {}), "parametersJsonSchema": t["input_schema"]} for t in tools]}]


def _encode_tool_choice(choice: Any) -> Any:
    if choice is None:
        return None
    if choice == "auto":
        return {"functionCallingConfig": {"mode": "AUTO"}}
    if choice == "none":
        return {"functionCallingConfig": {"mode": "NONE"}}
    if choice == "required":
        return {"functionCallingConfig": {"mode": "ANY"}}
    if isinstance(choice, str):
        return {"functionCallingConfig": {"mode": "ANY", "allowedFunctionNames": [choice]}}
    if isinstance(choice, dict) and isinstance(choice.get("name"), str):
        return {"functionCallingConfig": {"mode": "ANY", "allowedFunctionNames": [choice["name"]]}}
    return choice


def _encode_format(value: Any) -> Any:
    if value is None or value.get("type") == "text":
        return None
    if value.get("type") == "json":
        return {"responseMimeType": "application/json"}
    return {"responseMimeType": "application/json", "responseJsonSchema": value.get("schema")}


def _gemini_error(status: int, body: str, request_id: Optional[str], redact) -> ConduitError:
    details: Dict[str, str] = {}
    try:
        v = json.loads(body)
        if isinstance(v, dict):
            err = v.get("error") if isinstance(v.get("error"), dict) else v
            if isinstance(err, dict):
                if isinstance(err.get("message"), str):
                    details["message"] = redact(err["message"])
                if isinstance(err.get("status"), str):
                    details["type"] = redact(err["status"])
                if isinstance(err.get("code"), (int, float)):
                    details["code"] = str(int(err["code"]))
                if isinstance(err.get("reason"), str):
                    details["type"] = redact(err["reason"])
    except Exception:
        pass
    return http_failure(status, details, request_id, False)


# ---------------------------------------------------------------------------
# Encode helpers
# ---------------------------------------------------------------------------
def _gemini_request(messages: List[Dict[str, Any]], request: Dict[str, Any], wire_tools: Any, wire_tool_choice: Any, wire_format: Any) -> str:
    native = request.get("provider_options") or {}
    if not isinstance(native, dict):
        _invalid("providerOptions must be an object.")
    owned = ["contents", "systemInstruction", "generationConfig", "tools", "toolConfig", "safetySettings", "cachedContent"]
    if any(k in native for k in owned):
        _invalid("providerOptions conflicts with a Conduit-owned field.")
    if isinstance(native.get("generationConfig"), dict):
        gc = native["generationConfig"]
        if any(k in gc for k in ["maxOutputTokens", "temperature", "topP", "top_p", "stopSequences", "responseMimeType", "responseSchema", "responseJsonSchema", "candidateCount"]):
            _invalid("providerOptions.generationConfig conflicts with Conduit-owned field.")
    system_parts: List[str] = []
    contents: List[Any] = []
    seen_non_system = False
    for m in messages:
        role = m["role"]
        parts = m["content"]
        if role == "system":
            if seen_non_system:
                _invalid("System messages must be leading.")
            for p in parts:
                if p["type"] == "text":
                    system_parts.append(p["text"])
                else:
                    _invalid("System messages must not contain tool content.")
            continue
        seen_non_system = True
        if role == "tool":
            function_responses = []
            for p in parts:
                if p["type"] != "tool_result":
                    _invalid("Tool messages must contain tool_result parts.")
                if not isinstance(p.get("name"), str) or not p["name"].strip():
                    _invalid("Tool result name is required for Gemini.")
                inner = p["content"]
                text = inner if isinstance(inner, str) else "".join(q.get("text", "") for q in inner)
                try:
                    parsed = json.loads(text)
                    response_obj = parsed if isinstance(parsed, dict) else {"result": text}
                except Exception:
                    response_obj = {"result": text}
                function_responses.append({"functionResponse": {**( {"id": p["call_id"]} if p.get("call_id") else {}), "name": p["name"], "response": response_obj}})
            contents.append({"role": "user", "parts": function_responses})
            continue
        parts_out: List[Any] = []
        for p in parts:
            if p["type"] == "text":
                parts_out.append({"text": p["text"]})
            elif p["type"] == "tool_call":
                if role != "assistant":
                    _invalid("Only assistant messages may contain tool_call parts.")
                if not isinstance(p.get("name"), str) or not p["name"].strip():
                    _invalid("Tool call name required.")
                args = p["arguments"]
                parts_out.append({"functionCall": {**( {"id": p["id"]} if p.get("id") else {}), "name": p["name"], "args": args}})
            else:
                _invalid("Only tool messages may contain tool_result parts.")
        gemini_role = "model" if role == "assistant" else role
        if gemini_role not in ("user", "model"):
            _protocol("Invalid role for Gemini.")
        contents.append({"role": gemini_role, "parts": parts_out})
    body: Dict[str, Any] = {**native, "contents": contents}
    if system_parts:
        body["systemInstruction"] = {"parts": [{"text": t} for t in system_parts]}
    generation_config: Dict[str, Any] = {}
    if request.get("max_output_tokens") is not None:
        generation_config["maxOutputTokens"] = request["max_output_tokens"]
    if request.get("temperature") is not None:
        generation_config["temperature"] = request["temperature"]
    if request.get("top_p") is not None:
        generation_config["topP"] = request["top_p"]
    if request.get("stop") is not None:
        generation_config["stopSequences"] = list(request["stop"])
    if wire_format is not None:
        if wire_format.get("responseMimeType"):
            generation_config["responseMimeType"] = wire_format["responseMimeType"]
        if wire_format.get("responseJsonSchema"):
            generation_config["responseJsonSchema"] = wire_format["responseJsonSchema"]
    if generation_config:
        body["generationConfig"] = generation_config
    if wire_tools is not None:
        body["tools"] = wire_tools
    if wire_tool_choice is not None:
        body["toolConfig"] = wire_tool_choice
    return json.dumps(body)
def _gemini_response(value: Any, request_id: Optional[str], redact) -> GenerationResponse:
    if not isinstance(value, dict):
        _protocol("Malformed or unsupported Gemini response.")
    if isinstance(value.get("error"), dict):
        raise _gemini_error(200, json.dumps(value), request_id, redact)
    cands = value.get("candidates")
    if not isinstance(cands, list) or len(cands) == 0:
        pf = value.get("promptFeedback")
        if isinstance(pf, dict) and isinstance(pf.get("blockReason"), str):
            usage = None
            if isinstance(value.get("usageMetadata"), dict):
                um = value["usageMetadata"]
                usage = {}
                if isinstance(um.get("promptTokenCount"), int):
                    usage["input_tokens"] = um["promptTokenCount"]
                if isinstance(um.get("candidatesTokenCount"), int):
                    usage["output_tokens"] = um["candidatesTokenCount"]
                if isinstance(um.get("totalTokenCount"), int):
                    usage["total_tokens"] = um["totalTokenCount"]
            return text_response({"content": [], "finish_reason": "content_filter", **({"usage": usage} if usage else {}), "provider_metadata": {"finish_reason": redact(pf["blockReason"]), **({"request_id": request_id} if request_id else {})}})
        _protocol("Malformed or unsupported Gemini response.")
    cand = cands[0]
    if not isinstance(cand, dict) or not isinstance(cand.get("content"), dict):
        _protocol("Malformed or unsupported Gemini response.")
    parts = cand["content"].get("parts")
    if not isinstance(parts, list):
        _protocol("Malformed or unsupported Gemini response.")
    content: List[Dict[str, Any]] = []
    has_fc = False
    for part in parts:
        if not isinstance(part, dict):
            _protocol("Malformed or unsupported Gemini response.")
        if isinstance(part.get("text"), str):
            content.append({"type": "text", "text": part["text"]})
        elif isinstance(part.get("functionCall"), dict):
            fc = part["functionCall"]
            if not isinstance(fc.get("name"), str) or not fc["name"].strip():
                _protocol("Malformed or unsupported Gemini response.")
            has_fc = True
            args = fc.get("args") if fc.get("args") is not None else {}
            content.append({"type": "tool_call", **({"id": fc["id"]} if isinstance(fc.get("id"), str) and fc["id"] else {}), "name": fc["name"], "arguments": args})
        elif part.get("thought") is True or isinstance(part.get("thoughtSignature"), str):
            continue
        elif len(part) == 0:
            _protocol("Malformed or unsupported Gemini response.")
        else:
            if part.get("thought") is True:
                continue
            _protocol("Malformed or unsupported Gemini response.")
    raw_fr = cand.get("finishReason")
    def _map_fr(fr: Any) -> str:
        if fr == "STOP":
            return "stop"
        if fr == "MAX_TOKENS":
            return "length"
        if fr in ("SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII", "IMAGE_SAFETY"):
            return "content_filter"
        return "other"
    finish = _map_fr(raw_fr) if raw_fr else ("stop" if content else "other")
    if has_fc and finish != "content_filter":
        finish = "tool_call"
    usage = None
    if isinstance(value.get("usageMetadata"), dict):
        um = value["usageMetadata"]
        usage = {}
        if isinstance(um.get("promptTokenCount"), int) and um["promptTokenCount"] >= 0:
            usage["input_tokens"] = um["promptTokenCount"]
        if isinstance(um.get("candidatesTokenCount"), int) and um["candidatesTokenCount"] >= 0:
            usage["output_tokens"] = um["candidatesTokenCount"]
        if isinstance(um.get("totalTokenCount"), int) and um["totalTokenCount"] >= 0:
            usage["total_tokens"] = um["totalTokenCount"]
        if not usage:
            usage = None
    metadata: Dict[str, Any] = {}
    if request_id:
        metadata["request_id"] = request_id
    if isinstance(raw_fr, str):
        metadata["finish_reason"] = redact(raw_fr)
    if isinstance(value.get("modelVersion"), str):
        metadata["modelVersion"] = redact(value["modelVersion"])
    if isinstance(value.get("responseId"), str):
        metadata["responseId"] = redact(value["responseId"])
    return text_response({
        **({"id": redact(value["responseId"])} if isinstance(value.get("responseId"), str) else {}),
        **({"model": redact(value["modelVersion"])} if isinstance(value.get("modelVersion"), str) else {}),
        "content": content, "finish_reason": finish, **({"usage": usage} if usage else {}), "provider_metadata": metadata,
    })
def _parse_gemini_stream_incremental(chunks: Iterator[bytes], request_id: Optional[str], redact) -> Iterator[Dict[str, Any]]:
    """Port of typescript/src/gemini.ts geminiStream SSE — incremental."""
    def proto(msg="Malformed or incomplete Gemini stream."):
        raise ConduitError("ProtocolError", msg)

    def usage_from_meta(meta: Optional[Dict[str, Any]]) -> Optional[Dict[str, int]]:
        if not isinstance(meta, dict):
            return None
        u: Dict[str, int] = {}
        if isinstance(meta.get("promptTokenCount"), int) and meta["promptTokenCount"] >= 0:
            u["input_tokens"] = meta["promptTokenCount"]
        if isinstance(meta.get("candidatesTokenCount"), int) and meta["candidatesTokenCount"] >= 0:
            u["output_tokens"] = meta["candidatesTokenCount"]
        if isinstance(meta.get("totalTokenCount"), int) and meta["totalTokenCount"] >= 0:
            u["total_tokens"] = meta["totalTokenCount"]
        return u if u else None

    decoder = codecs.getincrementaldecoder("utf-8")(errors="strict")
    line = ""
    data_buf: List[str] = []
    skip_lf = False
    started = False
    first_rid: Optional[str] = None
    first_mid: Optional[str] = None
    usage: Optional[Dict[str, int]] = None
    aggregated: List[Dict[str, Any]] = []
    last_raw: Optional[Dict[str, Any]] = None
    finish_reason: Optional[str] = None

    # inline SSE loop — gemini-specific delta handling
    # We will iterate chunks incrementally and yield as soon as each SSE data completes
    try:
        for chunk in chunks:
            try:
                text = decoder.decode(chunk, final=False)
            except UnicodeDecodeError:
                proto()
            for ch in text:
                if skip_lf and ch == "\n":
                    skip_lf = False
                    continue
                skip_lf = (ch == "\r")
                if ch not in ("\r", "\n"):
                    line += ch
                    continue
                if not line:
                    if data_buf:
                        joined = "\n".join(data_buf)
                        data_buf = []
                        if joined and joined != "[DONE]" and joined.strip():
                            try:
                                obj = json.loads(joined)
                            except Exception:
                                proto()
                            if not isinstance(obj, dict):
                                proto()
                            if isinstance(obj.get("error"), dict):
                                raise _gemini_error(200, json.dumps(obj), request_id, redact)
                            cands = obj.get("candidates")
                            if cands is not None and not isinstance(cands, list):
                                proto()
                            if not started:
                                started = True
                                mid = obj.get("modelVersion")
                                rid = obj.get("responseId")
                                if isinstance(rid, str):
                                    first_rid = rid
                                if isinstance(mid, str):
                                    first_mid = mid
                                yield {"type": "start", **({"id": redact(rid)} if isinstance(rid, str) else {}), **({"model": redact(mid)} if isinstance(mid, str) else {})}
                            if isinstance(cands, list) and cands:
                                cand = cands[0]
                                if not isinstance(cand, dict):
                                    proto()
                                if cand.get("finishReason"):
                                    finish_reason = cand["finishReason"]
                                content = cand.get("content")
                                parts = content.get("parts") if isinstance(content, dict) else None
                                if isinstance(parts, list):
                                    for part in parts:
                                        if not isinstance(part, dict):
                                            proto()
                                        if isinstance(part.get("text"), str) and part["text"]:
                                            aggregated.append(part)
                                            yield {"type": "text_delta", "index": 0, "text": part["text"]}
                                        elif isinstance(part.get("functionCall"), dict):
                                            fc = part["functionCall"]
                                            if not isinstance(fc.get("name"), str):
                                                proto()
                                            if fc.get("id") is not None and not isinstance(fc.get("id"), str):
                                                proto()
                                            # validate args JSON-serializable
                                            try:
                                                json.dumps(fc.get("args", {}))
                                            except Exception:
                                                proto()
                                            args_str = json.dumps(fc.get("args") or {})
                                            aggregated.append(part)
                                            idx = len([p for p in aggregated if isinstance(p.get("functionCall"), dict)]) - 1
                                            yield {"type": "tool_call_delta", "index": idx, **({"id": fc["id"]} if isinstance(fc.get("id"), str) and fc["id"] else {}), "name": fc["name"], "argumentsDelta": args_str}
                                        elif part.get("thought") is True or isinstance(part.get("thoughtSignature"), str) or isinstance(part.get("executableCode"), dict):
                                            aggregated.append(part)
                                            continue
                                        elif len(part) == 0:
                                            proto()
                                        else:
                                            # preserve thought metadata as native part
                                            if part.get("thought") is True or isinstance(part.get("thoughtSignature"), str):
                                                aggregated.append(part)
                                                continue
                                            proto()
                            um = obj.get("usageMetadata")
                            if isinstance(um, dict):
                                u = usage_from_meta(um)
                                if u:
                                    usage = {**(usage or {}), **u}
                                    yield {"type": "usage", "usage": dict(usage)}
                            last_raw = obj
                else:
                    colon = line.find(":")
                    field = line if colon < 0 else line[:colon]
                    val = "" if colon < 0 else line[colon + 1 :]
                    if val.startswith(" "):
                        val = val[1:]
                    if field == "data":
                        data_buf.append(val)
                line = ""
        # flush tail
        tail = decoder.decode(b"", final=True)
        for ch in tail:
            if skip_lf and ch == "\n":
                skip_lf = False
                continue
            skip_lf = (ch == "\r")
            if ch not in ("\r", "\n"):
                line += ch
                continue
            if not line:
                if data_buf:
                    joined = "\n".join(data_buf)
                    data_buf = []
                    if joined and joined != "[DONE]" and joined.strip():
                        try:
                            obj = json.loads(joined)
                        except Exception:
                            proto()
                        if isinstance(obj.get("error"), dict):
                            raise _gemini_error(200, json.dumps(obj), request_id, redact)
                        cands = obj.get("candidates")
                        if cands is not None and not isinstance(cands, list):
                            proto()
                        if not started:
                            started = True
                            mid = obj.get("modelVersion")
                            rid = obj.get("responseId")
                            if isinstance(rid, str):
                                first_rid = rid
                            if isinstance(mid, str):
                                first_mid = mid
                            yield {"type": "start", **({"id": redact(rid)} if isinstance(rid, str) else {}), **({"model": redact(mid)} if isinstance(mid, str) else {})}
                        if isinstance(cands, list) and cands:
                            cand = cands[0]
                            if cand.get("finishReason"):
                                finish_reason = cand["finishReason"]
                            parts = cand.get("content", {}).get("parts") if isinstance(cand.get("content"), dict) else None
                            if isinstance(parts, list):
                                for part in parts:
                                    if isinstance(part.get("text"), str) and part["text"]:
                                        aggregated.append(part)
                                        yield {"type": "text_delta", "index": 0, "text": part["text"]}
                                    elif isinstance(part.get("functionCall"), dict):
                                        fc = part["functionCall"]
                                        if not isinstance(fc.get("name"), str):
                                            proto()
                                        if fc.get("id") is not None and not isinstance(fc.get("id"), str):
                                            proto()
                                        args_str = json.dumps(fc.get("args") or {})
                                        aggregated.append(part)
                                        idx = len([p for p in aggregated if isinstance(p.get("functionCall"), dict)]) - 1
                                        yield {"type": "tool_call_delta", "index": idx, **({"id": fc["id"]} if isinstance(fc.get("id"), str) and fc["id"] else {}), "name": fc["name"], "argumentsDelta": args_str}
                                    elif part.get("thought") is True or isinstance(part.get("thoughtSignature"), str):
                                        aggregated.append(part)
                        um = obj.get("usageMetadata")
                        if isinstance(um, dict):
                            u = usage_from_meta(um)
                            if u:
                                usage = {**(usage or {}), **u}
                                yield {"type": "usage", "usage": dict(usage)}
                        last_raw = obj
            else:
                colon = line.find(":")
                field = line if colon < 0 else line[:colon]
                val = "" if colon < 0 else line[colon + 1 :]
                if val.startswith(" "):
                    val = val[1:]
                if field == "data":
                    data_buf.append(val)
            line = ""
        if data_buf:
            joined = "\n".join(data_buf)
            if joined and joined != "[DONE]" and joined.strip():
                try:
                    obj = json.loads(joined)
                except Exception:
                    proto()
                if isinstance(obj.get("error"), dict):
                    raise _gemini_error(200, json.dumps(obj), request_id, redact)
                if not started:
                    started = True
                    mid = obj.get("modelVersion")
                    rid = obj.get("responseId")
                    if isinstance(rid, str):
                        first_rid = rid
                    if isinstance(mid, str):
                        first_mid = mid
                    yield {"type": "start", **({"id": redact(rid)} if isinstance(rid, str) else {}), **({"model": redact(mid)} if isinstance(mid, str) else {})}
                cands = obj.get("candidates")
                if isinstance(cands, list) and cands:
                    cand = cands[0]
                    if cand.get("finishReason"):
                        finish_reason = cand["finishReason"]
                    parts = cand.get("content", {}).get("parts") if isinstance(cand.get("content"), dict) else None
                    if isinstance(parts, list):
                        for part in parts:
                            if isinstance(part.get("text"), str) and part["text"]:
                                aggregated.append(part)
                                yield {"type": "text_delta", "index": 0, "text": part["text"]}
                            elif isinstance(part.get("functionCall"), dict):
                                fc = part["functionCall"]
                                args_str = json.dumps(fc.get("args") or {})
                                aggregated.append(part)
                                idx = len([p for p in aggregated if isinstance(p.get("functionCall"), dict)]) - 1
                                yield {"type": "tool_call_delta", "index": idx, **({"id": fc["id"]} if isinstance(fc.get("id"), str) and fc["id"] else {}), "name": fc["name"], "argumentsDelta": args_str}
                    last_raw = obj
        if not started:
            proto()
        # build final response from aggregated — preserve first seen ids if last lacks
        rid_final = (last_raw.get("responseId") if last_raw and isinstance(last_raw.get("responseId"), str) else None) or first_rid
        mid_final = (last_raw.get("modelVersion") if last_raw and isinstance(last_raw.get("modelVersion"), str) else None) or first_mid
        final_input: Dict[str, Any] = {
            "candidates": [{"content": {"parts": aggregated}, "finishReason": finish_reason or "STOP"}],
            "usageMetadata": {"promptTokenCount": (usage or {}).get("input_tokens", 0), "candidatesTokenCount": (usage or {}).get("output_tokens", 0), "totalTokenCount": (usage or {}).get("total_tokens", (usage or {}).get("input_tokens", 0) + (usage or {}).get("output_tokens", 0))} if usage else None,
            **({"responseId": rid_final} if rid_final else {}),
            **({"modelVersion": mid_final} if mid_final else {}),
        }
        # preserve safetyRatings if present
        if last_raw and isinstance(last_raw.get("candidates"), list) and last_raw["candidates"]:
            cand0 = last_raw["candidates"][0]
            if isinstance(cand0, dict) and cand0.get("safetyRatings"):
                final_input["safetyRatings"] = cand0["safetyRatings"]  # type: ignore
        # Clean None usage
        if final_input.get("usageMetadata") is None:
            final_input.pop("usageMetadata", None)
        # geminiResponse expects top-level candidates etc.
        resp = _gemini_response(final_input, request_id, redact)
        yield {"type": "done", "response": resp}
    except GeneratorExit:
        try:
            if hasattr(chunks, "close"):
                chunks.close()  # type: ignore[attr-defined]
        except Exception:
            pass
        raise


class GeminiProvider:
    stream_content_types = ("text/event-stream",)

    def validate_request(self, request, streaming):
        max_output = request.get("max_output_tokens")
        if max_output is not None and (not isinstance(max_output, int) or isinstance(max_output, bool) or max_output < 1):
            _invalid("maxOutputTokens must be a positive safe integer.")
        temp = request.get("temperature")
        if temp is not None and (not isinstance(temp, (int, float)) or isinstance(temp, bool) or temp != temp or temp in (float("inf"), float("-inf")) or temp < 0):
            _invalid("temperature must be between 0 and 2.")

    def encode_request(self, model, messages, request, streaming):
        return _gemini_request(messages, request, _encode_tools(request.get("tools")), _encode_tool_choice(request.get("tool_choice")), _encode_format(request.get("response_format")))

    def decode_response(self, value, request_id, redact):
        return _gemini_response(value, request_id, redact)

    def http_error(self, status, body, request_id, redact, model=None):
        return _gemini_error(status, body, request_id, redact)

    def parse_stream(self, chunks, request_id, redact, model_id=None):
        return _parse_gemini_stream_incremental(chunks, request_id, redact)

    def normalize_models(self, value, redact):
        if not isinstance(value, dict) or not isinstance(value.get("models"), list):
            _protocol()
        infos = []
        for entry in value["models"]:
            if not isinstance(entry, dict) or not isinstance(entry.get("name"), str) or not entry["name"].strip():
                _protocol()
            raw_name = entry["name"]
            model_id = raw_name[len("models/"):] if raw_name.startswith("models/") else raw_name
            if not model_id.strip():
                _protocol()
            metadata = {}
            for key, data in entry.items():
                if key in ("name", "displayName"):
                    continue
                try:
                    json.dumps(data)
                except Exception:
                    _protocol()
                if data is not None:
                    metadata[key] = data
            if raw_name != model_id:
                metadata["name"] = raw_name
            display = entry.get("displayName") if isinstance(entry.get("displayName"), str) and entry["displayName"].strip() else None
            infos.append(ModelInfo(id=redact(model_id), name=redact(display) if display else None, provider_metadata=metadata or None))
        return infos

    def model_page(self, value, redact):
        models = self.normalize_models(value, redact)
        token = value.get("nextPageToken")
        if token is not None and not isinstance(token, str):
            _protocol("Malformed nextPageToken.")
        return models, token, bool(token)

    def model_page_url(self, base_url, token):
        return base_url if token is None else f"{base_url}?pageToken={urllib.parse.quote(token, safe='')}"


# ---------------------------------------------------------------------------
# Client / Model
# ---------------------------------------------------------------------------
