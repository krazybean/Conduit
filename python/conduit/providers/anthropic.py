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
    return [{"name": t["name"], **({"description": t["description"]} if t.get("description") is not None else {}), "input_schema": t["input_schema"]} for t in tools]


def _encode_tool_choice(choice: Any) -> Any:
    if choice is None:
        return None
    if choice == "auto":
        return {"type": "auto"}
    if choice == "none":
        return {"type": "none"}
    if choice == "required":
        return {"type": "any"}
    if isinstance(choice, str):
        return {"type": "tool", "name": choice}
    if isinstance(choice, dict) and isinstance(choice.get("name"), str):
        return {"type": "tool", "name": choice["name"]}
    return choice


def _anthropic_error(status: int, body: str, request_id: Optional[str], redact) -> ConduitError:
    details: Dict[str, str] = {}
    try:
        v = json.loads(body)
        if isinstance(v, dict):
            err_obj = v.get("error") if isinstance(v.get("error"), dict) else v
            if isinstance(err_obj, dict):
                if isinstance(err_obj.get("type"), str):
                    details["type"] = redact(err_obj["type"])
                if isinstance(err_obj.get("message"), str):
                    details["message"] = redact(err_obj["message"])
                if isinstance(err_obj.get("code"), str):
                    details["code"] = redact(err_obj["code"])
            if isinstance(v.get("request_id"), str) and request_id is None:
                request_id = redact(v["request_id"])
    except Exception:
        pass
    return http_failure(status, details, request_id, False)
def _anthropic_request(model: str, messages: List[Dict[str, Any]], request: Dict[str, Any], streaming: bool, wire_tools: Any, wire_tool_choice: Any) -> str:
    native = request.get("provider_options") or {}
    if not isinstance(native, dict):
        _invalid("providerOptions must be an object.")
    owned = ["model", "messages", "system", "stream", "max_tokens", "temperature", "top_p", "top_k", "stop_sequences", "tools", "tool_choice"]
    if any(k in native for k in owned):
        _invalid("providerOptions conflicts with a Conduit-owned field.")
    try:
        json.dumps(native)
    except Exception:
        _invalid("Invalid providerOptions.")
    system_parts: List[str] = []
    anthropic_messages: List[Any] = []
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
            tool_results = []
            for p in parts:
                if p["type"] != "tool_result":
                    _invalid("Tool messages must contain tool_result parts.")
                if not isinstance(p.get("call_id"), str) or not p["call_id"].strip():
                    _invalid("Tool result callId is required for Anthropic.")
                inner = p["content"]
                content_value = inner if isinstance(inner, str) else "".join(q.get("text", "") for q in inner)
                tool_results.append({"type": "tool_result", "tool_use_id": p["call_id"], "content": content_value})
            anthropic_messages.append({"role": "user", "content": tool_results})
            continue
        blocks: List[Any] = []
        for p in parts:
            if p["type"] == "text":
                blocks.append({"type": "text", "text": p["text"]})
            elif p["type"] == "tool_call":
                if role != "assistant":
                    _invalid("Only assistant messages may contain tool_call parts.")
                if not isinstance(p.get("id"), str) or not p["id"].strip():
                    _invalid("Tool call id is required for Anthropic.")
                blocks.append({"type": "tool_use", "id": p["id"], "name": p["name"], "input": p["arguments"]})
            else:
                _invalid("Only tool messages may contain tool_result parts.")
        has_tool_use = any(b.get("type") == "tool_use" for b in blocks)
        if not has_tool_use and len(blocks) == 1 and blocks[0].get("type") == "text":
            anthropic_messages.append({"role": role, "content": blocks[0]["text"]})
        else:
            anthropic_messages.append({"role": role, "content": blocks})
    system = None
    if len(system_parts) == 1:
        system = system_parts[0]
    elif len(system_parts) > 1:
        system = [{"type": "text", "text": t} for t in system_parts]
    max_tokens = request.get("max_output_tokens")
    if max_tokens is None:
        _invalid("maxOutputTokens is required for Anthropic.")
    body: Dict[str, Any] = {**native, "model": model, "messages": anthropic_messages, "stream": streaming, "max_tokens": max_tokens}
    if system is not None:
        body["system"] = system
    if request.get("temperature") is not None:
        body["temperature"] = request["temperature"]
    if request.get("top_p") is not None:
        body["top_p"] = request["top_p"]
    if request.get("stop") is not None:
        body["stop_sequences"] = list(request["stop"])
    if wire_tools is not None:
        body["tools"] = wire_tools
    if wire_tool_choice is not None:
        body["tool_choice"] = wire_tool_choice
    return json.dumps(body)
def _anthropic_response(value: Any, request_id: Optional[str], redact) -> GenerationResponse:
    if not isinstance(value, dict):
        _protocol("Malformed or unsupported Anthropic response.")
    if isinstance(value.get("error"), dict):
        raise _anthropic_error(200, json.dumps(value), request_id, redact)
    if value.get("type") != "message" or value.get("role") != "assistant":
        _protocol("Malformed or unsupported Anthropic response.")
    if not isinstance(value.get("id"), str) or not isinstance(value.get("model"), str):
        _protocol("Malformed or unsupported Anthropic response.")
    if not isinstance(value.get("content"), list):
        _protocol("Malformed or unsupported Anthropic response.")
    content: List[Dict[str, Any]] = []
    has_tool = False
    for block in value["content"]:
        if not isinstance(block, dict) or not isinstance(block.get("type"), str):
            _protocol("Malformed or unsupported Anthropic response.")
        if block["type"] == "text":
            if not isinstance(block.get("text"), str):
                _protocol("Malformed or unsupported Anthropic response.")
            content.append({"type": "text", "text": block["text"]})
        elif block["type"] == "tool_use":
            if not isinstance(block.get("id"), str) or not isinstance(block.get("name"), str) or block.get("input") is None:
                _protocol("Malformed or unsupported Anthropic response.")
            has_tool = True
            content.append({"type": "tool_call", "id": block["id"], "name": block["name"], "arguments": block["input"]})
        elif block["type"] in ("thinking", "redacted_thinking"):
            continue
        else:
            _protocol("Malformed or unsupported Anthropic response.")
    usage = None
    if isinstance(value.get("usage"), dict):
        u = value["usage"]
        usage = {}
        if isinstance(u.get("input_tokens"), int) and u["input_tokens"] >= 0:
            usage["input_tokens"] = u["input_tokens"]
        if isinstance(u.get("output_tokens"), int) and u["output_tokens"] >= 0:
            usage["output_tokens"] = u["output_tokens"]
    # finish reason
    stop = value.get("stop_reason")
    def _finish(s: Any) -> str:
        if s in ("end_turn", "stop_sequence"):
            return "stop"
        if s == "max_tokens":
            return "length"
        if s == "tool_use":
            return "tool_call"
        if s == "refusal":
            return "content_filter"
        return "other"
    fr = _finish(stop)
    if has_tool:
        fr = "tool_call"
    metadata: Dict[str, Any] = {}
    if request_id:
        metadata["request_id"] = request_id
    if isinstance(stop, str):
        metadata["finish_reason"] = redact(stop)
    return text_response({"id": redact(value["id"]), "model": redact(value["model"]), "content": content, "finish_reason": fr, **({"usage": usage} if usage else {}), "provider_metadata": metadata})
def _sse_events(chunks: Iterator[bytes]) -> Iterator[Tuple[str, str]]:
    """Incremental SSE — yields (event, data) per blank-line dispatch, no buffering of whole body."""
    decoder = codecs.getincrementaldecoder("utf-8")(errors="strict")
    line = ""
    event = ""
    data: List[str] = []
    skip_lf = False
    try:
        for chunk in chunks:
            try:
                text = decoder.decode(chunk, final=False)
            except UnicodeDecodeError:
                _protocol("Malformed or incomplete Anthropic stream.")
            for ch in text:
                if skip_lf and ch == "\n":
                    skip_lf = False
                    continue
                skip_lf = (ch == "\r")
                if ch not in ("\r", "\n"):
                    line += ch
                    continue
                if not line:
                    if data or event:
                        joined = "\n".join(data)
                        ev = event
                        event = ""
                        data = []
                        if joined or ev:
                            yield (ev, joined)
                else:
                    colon = line.find(":")
                    field = line if colon < 0 else line[:colon]
                    val = "" if colon < 0 else line[colon + 1 :]
                    if val.startswith(" "):
                        val = val[1:]
                    if field == "event":
                        event = val
                    elif field == "data":
                        data.append(val)
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
                if data or event:
                    joined = "\n".join(data)
                    ev = event
                    event = ""
                    data = []
                    if joined or ev:
                        yield (ev, joined)
            else:
                colon = line.find(":")
                field = line if colon < 0 else line[:colon]
                val = "" if colon < 0 else line[colon + 1 :]
                if val.startswith(" "):
                    val = val[1:]
                if field == "event":
                    event = val
                elif field == "data":
                    data.append(val)
            line = ""
    except GeneratorExit:
        try:
            if hasattr(chunks, "close"):
                chunks.close()  # type: ignore[attr-defined]
        except Exception:
            pass
        raise

class AnthropicProvider:
    stream_content_types = ("text/event-stream",)

    def validate_request(self, request, streaming):
        max_output = request.get("max_output_tokens")
        if max_output is not None and (not isinstance(max_output, int) or isinstance(max_output, bool) or max_output < 0):
            _invalid("maxOutputTokens must be a nonnegative safe integer.")
        temp = request.get("temperature")
        if temp is not None and (not isinstance(temp, (int, float)) or isinstance(temp, bool) or temp != temp or temp in (float("inf"), float("-inf")) or temp < 0 or temp > 1):
            _invalid("temperature must be between 0 and 1.")
        if request.get("response_format") is not None:
            raise ConduitError("UnsupportedCapabilityError", "responseFormat is not supported for Anthropic.")

    def encode_request(self, model, messages, request, streaming):
        return _anthropic_request(model, messages, request, streaming, _encode_tools(request.get("tools")), _encode_tool_choice(request.get("tool_choice")))

    def decode_response(self, value, request_id, redact):
        return _anthropic_response(value, request_id, redact)

    def http_error(self, status, body, request_id, redact, model=None):
        return _anthropic_error(status, body, request_id, redact)

    def parse_stream(self, chunks, request_id, redact, model_id=None):
        return _parse_anthropic_stream_incremental(chunks, request_id, redact)

    def normalize_models(self, value, redact):
        if not isinstance(value, dict) or not isinstance(value.get("data"), list):
            _protocol()
        infos = []
        for entry in value["data"]:
            if not isinstance(entry, dict) or not isinstance(entry.get("id"), str) or not entry["id"].strip():
                _protocol()
            metadata = {}
            for key, data in entry.items():
                if key in ("id", "display_name"):
                    continue
                try:
                    json.dumps(data)
                except Exception:
                    _protocol()
                if data is not None:
                    metadata[key] = data
            display = entry.get("display_name") if isinstance(entry.get("display_name"), str) and entry["display_name"].strip() else None
            infos.append(ModelInfo(id=redact(entry["id"]), name=redact(display) if display else None, provider_metadata=metadata or None))
        return infos

    def model_page(self, value, redact):
        models = self.normalize_models(value, redact)
        has_more = value.get("has_more")
        if has_more is not None and not isinstance(has_more, bool):
            _protocol()
        token = value.get("last_id")
        if has_more and (not isinstance(token, str) or not token.strip()):
            _protocol("Malformed pagination: last_id required when has_more true.")
        if not has_more and token is not None and not isinstance(token, str):
            _protocol("Malformed last_id.")
        if has_more and not value["data"]:
            _protocol("Malformed pagination: has_more true with empty data.")
        return models, token, bool(has_more)

    def model_page_url(self, base_url, token):
        return base_url if token is None else f"{base_url}?after_id={urllib.parse.quote(token, safe='')}"
def _parse_anthropic_stream_incremental(chunks: Iterator[bytes], request_id: Optional[str], redact) -> Iterator[Dict[str, Any]]:
    """Port of typescript/src/anthropic.ts anthropicStream — incremental, no whole-body buffering."""
    def proto(msg="Malformed or incomplete Anthropic stream."):
        raise ConduitError("ProtocolError", msg)

    def usage_of(v: Dict[str, Any]) -> Optional[Dict[str, int]]:
        if not isinstance(v, dict):
            return None
        u: Dict[str, int] = {}
        if isinstance(v.get("input_tokens"), int) and v["input_tokens"] >= 0:
            u["input_tokens"] = v["input_tokens"]
        if isinstance(v.get("output_tokens"), int) and v["output_tokens"] >= 0:
            u["output_tokens"] = v["output_tokens"]
        return u if u else None

    started = False
    anth_id: Optional[str] = None
    anth_model: Optional[str] = None
    stop_reason: Optional[str] = None
    usage: Optional[Dict[str, int]] = None
    tool_accum: Dict[int, Dict[str, str]] = {}
    final_blocks: Dict[int, Dict[str, Any]] = {}
    try:
        for event, data in _sse_events(chunks):
            if not data:
                continue
            try:
                value = json.loads(data)
            except Exception:
                proto()
            if not isinstance(value, dict):
                proto()
            # in-band error
            if value.get("type") == "error" or event == "error":
                raise _anthropic_error(200, json.dumps(value), request_id, redact)
            t = value.get("type")
            if t == "message_start":
                if started:
                    proto()
                msg = value.get("message")
                if not isinstance(msg, dict) or not isinstance(msg.get("id"), str) or not isinstance(msg.get("model"), str):
                    proto()
                anth_id = msg["id"]
                anth_model = msg["model"]
                if isinstance(msg.get("usage"), dict):
                    u = usage_of(msg["usage"])
                    if u:
                        usage = {**(usage or {}), **u}
                started = True
                yield {"type": "start", **({"id": redact(anth_id)} if anth_id else {}), **({"model": redact(anth_model)} if anth_model else {})}
                if usage:
                    yield {"type": "usage", "usage": dict(usage)}
                continue
            if not started and t != "ping":
                proto()
            if t == "ping":
                continue
            if t == "content_block_start":
                idx = value.get("index")
                if not isinstance(idx, int) or idx < 0:
                    proto()
                block = value.get("content_block")
                if not isinstance(block, dict) or not isinstance(block.get("type"), str):
                    proto()
                btype = block["type"]
                if btype == "text":
                    final_blocks[idx] = {"type": "text", "text": block.get("text") if isinstance(block.get("text"), str) else ""}
                elif btype == "tool_use":
                    if not isinstance(block.get("id"), str) or not isinstance(block.get("name"), str):
                        proto()
                    tool_accum[idx] = {"id": block["id"], "name": block["name"], "inputJson": ""}
                    final_blocks[idx] = {"type": "tool_use", "id": block["id"], "name": block["name"], "input": {}}
                    yield {"type": "tool_call_delta", "index": idx, "id": block["id"], "name": block["name"]}
                elif btype in ("thinking", "redacted_thinking"):
                    if btype == "thinking":
                        if not isinstance(block.get("thinking"), str):
                            proto()
                        if block.get("signature") is not None and not isinstance(block.get("signature"), str):
                            proto()
                    else:
                        if not isinstance(block.get("data"), str):
                            proto()
                    try:
                        json.dumps(block)
                    except Exception:
                        proto()
                    final_blocks[idx] = dict(block)
                else:
                    proto()
                continue
            if t == "content_block_delta":
                idx = value.get("index")
                if not isinstance(idx, int) or idx < 0:
                    proto()
                delta = value.get("delta")
                if not isinstance(delta, dict) or not isinstance(delta.get("type"), str):
                    proto()
                dtype = delta["type"]
                if dtype == "text_delta":
                    if not isinstance(delta.get("text"), str):
                        proto()
                    text = delta["text"]
                    blk = final_blocks.get(idx)
                    if blk and blk.get("type") == "text":
                        blk["text"] = (blk.get("text") or "") + text
                    else:
                        final_blocks[idx] = {"type": "text", "text": text}
                    yield {"type": "text_delta", "index": 0, "text": text}
                elif dtype == "input_json_delta":
                    if not isinstance(delta.get("partial_json"), str):
                        proto()
                    frag = delta["partial_json"]
                    cur = tool_accum.get(idx)
                    if cur is None:
                        proto()
                    cur["inputJson"] = cur.get("inputJson", "") + frag
                    blk = final_blocks.get(idx)
                    if blk is not None:
                        blk["inputJson"] = cur["inputJson"]
                    yield {"type": "tool_call_delta", "index": idx, "argumentsDelta": frag}
                elif dtype == "thinking_delta":
                    if not isinstance(delta.get("thinking"), str):
                        proto()
                    blk = final_blocks.get(idx)
                    if not blk or blk.get("type") != "thinking":
                        proto()
                    blk["thinking"] = (blk.get("thinking") or "") + delta["thinking"]
                    try:
                        json.dumps(blk)
                    except Exception:
                        proto()
                elif dtype == "signature_delta":
                    if not isinstance(delta.get("signature"), str):
                        proto()
                    blk = final_blocks.get(idx)
                    if not blk or blk.get("type") != "thinking":
                        proto()
                    blk["signature"] = delta["signature"]
                    try:
                        json.dumps(blk)
                    except Exception:
                        proto()
                else:
                    proto()
                continue
            if t == "content_block_stop":
                idx = value.get("index")
                if not isinstance(idx, int) or idx < 0:
                    proto()
                continue
            if t == "message_delta":
                delta = value.get("delta")
                if not isinstance(delta, dict):
                    proto()
                if delta.get("stop_reason") is not None and not isinstance(delta.get("stop_reason"), str):
                    proto()
                if isinstance(delta.get("stop_reason"), str):
                    stop_reason = delta["stop_reason"]
                u = value.get("usage")
                if isinstance(u, dict):
                    pu = usage_of(u)
                    if pu:
                        usage = {**(usage or {}), **pu}
                        yield {"type": "usage", "usage": dict(usage)}
                continue
            if t == "message_stop":
                if stop_reason is None:
                    proto()
                # build final response
                sorted_blocks = sorted(final_blocks.items())
                content_blocks: List[Dict[str, Any]] = []
                for idx, blk in sorted_blocks:
                    if blk.get("type") == "text":
                        content_blocks.append({"type": "text", "text": blk.get("text") or ""})
                    elif blk.get("type") == "tool_use":
                        cur = tool_accum.get(idx, {})
                        input_str = cur.get("inputJson") or ""
                        inp: Any = {}
                        if input_str:
                            try:
                                inp = json.loads(input_str)
                            except Exception:
                                proto()
                        content_blocks.append({"type": "tool_use", "id": cur.get("id") or blk.get("id"), "name": cur.get("name") or blk.get("name"), "input": inp})
                    elif blk.get("type") in ("thinking", "redacted_thinking"):
                        if blk.get("type") == "thinking":
                            content_blocks.append({"type": "thinking", "thinking": blk.get("thinking") or "", **({"signature": blk["signature"]} if blk.get("signature") is not None else {})})
                        else:
                            content_blocks.append({"type": "redacted_thinking", "data": blk.get("data") or ""})
                response_input: Dict[str, Any] = {"type": "message", "role": "assistant", "id": anth_id or "msg_unknown", "model": anth_model or "unknown", "content": content_blocks, "stop_reason": stop_reason, "stop_sequence": None, "usage": {"input_tokens": (usage or {}).get("input_tokens", 0), "output_tokens": (usage or {}).get("output_tokens", 0)}}
                resp = _anthropic_response(response_input, request_id, redact)
                yield {"type": "done", "response": resp}
                return
            # unknown type — ignore per TS but if needed protocol
        proto()
    except GeneratorExit:
        try:
            if hasattr(chunks, "close"):
                chunks.close()  # type: ignore[attr-defined]
        except Exception:
            pass
        raise
