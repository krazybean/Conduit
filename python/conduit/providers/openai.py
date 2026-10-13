from __future__ import annotations

import codecs
import json
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
    return [{"type": "function", "function": {"name": t["name"], **({"description": t["description"]} if t.get("description") is not None else {}), "parameters": t["input_schema"]}} for t in tools]


def _encode_tool_choice(choice: Any) -> Any:
    if choice is None:
        return None
    if isinstance(choice, str):
        if choice in ("auto", "none", "required"):
            return choice
        return {"type": "function", "function": {"name": choice}}
    if isinstance(choice, dict) and isinstance(choice.get("name"), str):
        return {"type": "function", "function": {"name": choice["name"]}}
    return choice


def _encode_response_format(value: Any) -> Any:
    if value is None or value.get("type") == "text":
        return None
    if value.get("type") == "json":
        return {"type": "json_object"}
    return {"type": "json_schema", "json_schema": {"name": "response", "strict": True, "schema": value.get("schema")}}


def _encode_request(model: str, messages: List[Dict[str, Any]], request: Dict[str, Any], streaming: bool) -> str:
    wire_messages = []
    for message in messages:
        role = message["role"]
        parts = message["content"]
        if role == "tool":
            result = next((part for part in parts if part["type"] == "tool_result"), None)
            if result is None:
                _invalid("Tool message must contain tool_result.")
            inner = result["content"]
            inner_text = inner if isinstance(inner, str) else "".join(q.get("text", "") for q in inner)
            wire_messages.append({"role": "tool", "content": inner_text, "tool_call_id": result.get("call_id") or result.get("name") or "tool"})
        elif any(part["type"] == "tool_call" for part in parts):
            texts = [part["text"] for part in parts if part["type"] == "text"]
            tool_calls = []
            for part in parts:
                if part["type"] == "tool_call":
                    tool_calls.append({"id": part.get("id") or f"call_{len(tool_calls)}", "type": "function", "function": {"name": part["name"], "arguments": json.dumps(part["arguments"])}})
            wire_messages.append({"role": role, "content": "".join(texts) if texts else None, "tool_calls": tool_calls})
        else:
            wire_messages.append({"role": role, "content": [{"type": "text", "text": part["text"]} for part in parts if part["type"] == "text"]})
    body: Dict[str, Any] = {"model": model, "messages": wire_messages, "stream": streaming}
    if request.get("max_output_tokens") is not None:
        body["max_tokens"] = request["max_output_tokens"]
    if request.get("temperature") is not None:
        body["temperature"] = request["temperature"]
    if request.get("top_p") is not None:
        body["top_p"] = request["top_p"]
    if request.get("stop") is not None:
        body["stop"] = list(request["stop"])
    tools = _encode_tools(request.get("tools"))
    choice = _encode_tool_choice(request.get("tool_choice"))
    response_format = _encode_response_format(request.get("response_format"))
    if tools is not None:
        body["tools"] = tools
    if choice is not None:
        body["tool_choice"] = choice
    if response_format is not None:
        body["response_format"] = response_format
    for key, value in (request.get("provider_options") or {}).items():
        if key not in body or (key == "stream_options" and streaming):
            body[key] = value
    return json.dumps(body)


def _normalize_usage_openai(value: Any) -> Optional[Dict[str, int]]:
    if value is None:
        return None
    if not isinstance(value, dict):
        _protocol("Malformed or unsupported Chat Completions response.")
    usage: Dict[str, int] = {}
    for wire, norm in (("prompt_tokens", "input_tokens"), ("completion_tokens", "output_tokens"), ("total_tokens", "total_tokens")):
        cnt = value.get(wire)
        if cnt is not None:
            if not isinstance(cnt, int) or isinstance(cnt, bool) or cnt < 0:
                _protocol("Malformed or unsupported Chat Completions response.")
            usage[norm] = cnt
    return usage if usage else None
def _decode_openai(value: Any, request_id: Optional[str], redact) -> GenerationResponse:
    def protocol() -> None:
        _protocol("Malformed or unsupported Chat Completions response.")
    if not isinstance(value, dict) or not isinstance(value.get("choices"), list) or len(value["choices"]) != 1:
        protocol()
    choice = value["choices"][0]
    if not isinstance(choice, dict) or not isinstance(choice.get("message"), dict) or choice["message"].get("role") != "assistant" or not isinstance(choice.get("finish_reason"), str):
        protocol()
    message = choice["message"]
    has_tool_calls = isinstance(message.get("tool_calls"), list) and len(message["tool_calls"]) > 0
    if isinstance(message.get("tool_calls"), list):
        for tc in message["tool_calls"]:
            if not isinstance(tc, dict) or not isinstance(tc.get("id"), str) or tc.get("type") != "function" or not isinstance(tc.get("function"), dict) or not isinstance(tc["function"].get("name"), str):
                protocol()
            args = tc["function"].get("arguments")
            if isinstance(args, str):
                if args:
                    try:
                        json.loads(args)
                    except Exception:
                        protocol()
            elif not isinstance(args, dict):
                protocol()
    else:
        for k, d in message.items():
            if k not in ("role", "content", "reasoning_content") and d is not None and not (k == "tool_calls" and isinstance(d, list) and len(d) == 0):
                protocol()
    if message.get("reasoning_content") is not None and not isinstance(message.get("reasoning_content"), str):
        protocol()
    content_val = message.get("content")
    if not (isinstance(content_val, str) or (content_val is None and (choice["finish_reason"] == "content_filter" or has_tool_calls))):
        protocol()
    if value.get("id") is not None and not isinstance(value.get("id"), str):
        protocol()
    if value.get("model") is not None and not isinstance(value.get("model"), str):
        protocol()
    usage = _normalize_usage_openai(value.get("usage"))
    has_tool = has_tool_calls
    fr = choice["finish_reason"]
    if has_tool:
        finish_reason = "tool_call"
    elif fr in ("stop", "length", "content_filter"):
        finish_reason = fr
    elif fr == "tool_calls":
        finish_reason = "tool_call"
    else:
        finish_reason = "other"
    content: List[Dict[str, Any]] = []
    if isinstance(content_val, str):
        if content_val != "" or not has_tool:
            content.append({"type": "text", "text": content_val})
    if has_tool:
        for tc in message["tool_calls"]:
            fn = tc["function"]
            args_s = fn["arguments"]
            if isinstance(args_s, str):
                try:
                    args = json.loads(args_s) if args_s else {}
                except Exception:
                    protocol()
            else:
                args = args_s
            content.append({"type": "tool_call", "id": tc["id"], "name": fn["name"], "arguments": args})
    return text_response({
        **({"id": redact(value["id"])} if isinstance(value.get("id"), str) else {}),
        **({"model": redact(value["model"])} if isinstance(value.get("model"), str) else {}),
        "content": content,
        "finish_reason": finish_reason,
        **({"usage": usage} if usage is not None else {}),
        "provider_metadata": {"finish_reason": redact(fr), **({"request_id": request_id} if request_id else {})},
    })
def _http_error_openai(status: int, body: str, request_id: Optional[str], redact) -> ConduitError:
    details: Dict[str, str] = {}
    model_not_found = False
    try:
        v = json.loads(body)
        if isinstance(v, dict) and isinstance(v.get("error"), dict):
            err = v["error"]
            model_not_found = err.get("code") == "model_not_found"
            for f in ("message", "type", "code"):
                if isinstance(err.get(f), str):
                    details[f] = redact(err[f])
    except Exception:
        pass
    return http_failure(status, details, request_id, model_not_found)
def _parse_openai_stream_bytes(all_bytes: bytes, request_id: Optional[str], redact) -> Iterator[Dict[str, Any]]:
    text = all_bytes.decode("utf-8", errors="strict")
    # split by lines handling \r\n, \r, \n
    # Use state machine similar to TS but simpler: split on blank line events
    # Normalize line endings to \n
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    events_raw: List[str] = []
    # SSE: blank line delimits event; collect data lines between blanks
    data_buf: List[str] = []
    started = False
    cur_id: Optional[str] = None
    cur_model: Optional[str] = None
    finish: Optional[str] = None
    usage: Optional[Dict[str, int]] = None
    message_content = ""
    has_content = False
    tool_accum: Dict[int, Dict[str, str]] = {}

    def protocol():
        raise ConduitError("ProtocolError", "Malformed or incomplete Chat Completions stream.")

    lines = text.split("\n")
    # We need to simulate SSE framing: each event is separated by blank line
    # So we iterate lines and treat blank line as dispatch
    pending_data: List[str] = []
    for line in lines + ["", ""]:  # ensure final dispatch
        if line == "":
            if pending_data:
                data_str = "\n".join(pending_data)
                pending_data = []
                if data_str == "[DONE]":
                    if not started or finish is None:
                        protocol()
                    msg: Dict[str, Any] = {"role": "assistant", "content": message_content if has_content else None}
                    if tool_accum:
                        msg["tool_calls"] = [{"id": v.get("id", f"call_{k}"), "type": "function", "function": {"name": v.get("name", ""), "arguments": v.get("arguments", "")}} for k, v in sorted(tool_accum.items())]
                        if not has_content:
                            msg["content"] = None
                    else:
                        if not has_content and finish == "content_filter":
                            msg["content"] = None
                        elif not has_content:
                            # distinguish empty vs absent: if never had content, set to None
                            msg["content"] = None
                            # but if has_content was set via empty delta, we keep ""
                            # our has_content tracks even empty string deltas
                            if has_content:
                                msg["content"] = message_content
                    body: Dict[str, Any] = {"id": cur_id, "model": cur_model, "choices": [{"message": msg, "finish_reason": finish}]}
                    if usage is not None:
                        body["usage"] = {"prompt_tokens": usage.get("input_tokens"), "completion_tokens": usage.get("output_tokens"), "total_tokens": usage.get("total_tokens")}
                    resp = _decode_openai(body, request_id, redact)
                    yield {"type": "done", "response": resp}
                    return
                try:
                    v = json.loads(data_str)
                except Exception:
                    protocol()
                if not isinstance(v, dict):
                    protocol()
                if isinstance(v.get("error"), dict):
                    raise _http_error_openai(200, data_str, request_id, redact)
                if not isinstance(v.get("choices"), list) or len(v["choices"]) > 1:
                    protocol()
                for field in ("id", "model"):
                    prev = cur_id if field == "id" else cur_model
                    if v.get(field) is not None and (not isinstance(v.get(field), str) or (prev is not None and prev != v.get(field))):
                        protocol()
                if isinstance(v.get("id"), str):
                    cur_id = v["id"]
                if isinstance(v.get("model"), str):
                    cur_model = v["model"]
                reported = None
                if v.get("usage") is not None:
                    try:
                        reported = _normalize_usage_openai(v["usage"])
                    except ConduitError:
                        protocol()
                content_delta = ""
                tool_deltas: List[Dict[str, Any]] = []
                if len(v.get("choices", [])) == 0:
                    if not started or not reported:
                        protocol()
                else:
                    choice = v["choices"][0]
                    if not isinstance(choice, dict) or choice.get("index") != 0 or not isinstance(choice.get("delta"), dict):
                        protocol()
                    delta = choice["delta"]
                    if delta.get("role") is not None and delta.get("role") != "assistant":
                        protocol()
                    if delta.get("content") is not None and not isinstance(delta.get("content"), str):
                        protocol()
                    if delta.get("tool_calls") is not None:
                        if not isinstance(delta["tool_calls"], list):
                            protocol()
                    if delta.get("reasoning_content") is not None and not isinstance(delta.get("reasoning_content"), str):
                        protocol()
                    for k, d in delta.items():
                        if k not in ("role", "content", "reasoning_content", "tool_calls") and d is not None:
                            protocol()
                    has_tc = isinstance(delta.get("tool_calls"), list) and len(delta["tool_calls"]) > 0
                    if finish is not None and (delta.get("content") or has_tc or choice.get("finish_reason") is not None):
                        protocol()
                    if choice.get("finish_reason") is not None and not isinstance(choice.get("finish_reason"), str):
                        protocol()
                    if isinstance(choice.get("finish_reason"), str):
                        finish = choice["finish_reason"]
                    if isinstance(delta.get("content"), str):
                        content_delta = delta["content"]
                        has_content = True
                    if isinstance(delta.get("tool_calls"), list):
                        for tc in delta["tool_calls"]:
                            idx = tc.get("index")
                            fn = tc.get("function") or {}
                            cur = tool_accum.get(idx, {"arguments": ""})
                            if isinstance(tc.get("id"), str):
                                cur["id"] = tc["id"]
                            if isinstance(fn, dict) and "arguments" in fn and not isinstance(fn["arguments"], str):
                                protocol()
                            if isinstance(fn.get("name"), str):
                                cur["name"] = fn["name"]
                            if isinstance(fn.get("arguments"), str):
                                cur["arguments"] = cur.get("arguments", "") + fn["arguments"]
                            tool_accum[idx] = cur
                            tool_deltas.append({"index": idx, **({"id": tc["id"]} if isinstance(tc.get("id"), str) else {}), **({"name": fn["name"]} if isinstance(fn.get("name"), str) else {}), **({"argumentsDelta": fn["arguments"]} if isinstance(fn.get("arguments"), str) and fn["arguments"] else {})})
                if not started:
                    started = True
                    ev: Dict[str, Any] = {"type": "start"}
                    if cur_id is not None:
                        ev["id"] = redact(cur_id)
                    if cur_model is not None:
                        ev["model"] = redact(cur_model)
                    yield ev
                if content_delta:
                    message_content += content_delta
                    yield {"type": "text_delta", "index": 0, "text": content_delta}
                elif isinstance(delta.get("content"), str) and delta.get("content") == "":
                    message_content += ""
                for d in tool_deltas:
                    if d.get("id") is not None or d.get("name") is not None or d.get("argumentsDelta") is not None:
                        yield {"type": "tool_call_delta", **d}
                if reported is not None:
                    if usage is None:
                        usage = {}
                    usage.update(reported)
                    yield {"type": "usage", "usage": dict(usage)}
            continue
        # non-blank line
        if line.startswith("data:"):
            val = line[5:]
            if val.startswith(" "):
                val = val[1:]
            pending_data.append(val)
        elif line.startswith("data"):
            # field "data" without colon
            pending_data.append("")
        else:
            # ignore other fields/comments
            continue
    # if we exit loop without DONE, protocol
    raise ConduitError("ProtocolError", "Malformed or incomplete Chat Completions stream.")

class OpenAIProvider:
    stream_content_types = ("text/event-stream",)

    def encode_request(self, model, messages, request, streaming):
        return _encode_request(model, messages, request, streaming)

    def decode_response(self, value, request_id, redact):
        return _decode_openai(value, request_id, redact)

    def http_error(self, status, body, request_id, redact, model=None):
        return _http_error_openai(status, body, request_id, redact)

    def parse_stream(self, chunks, request_id, redact, model_id=None):
        return _parse_openai_stream_incremental(chunks, request_id, redact)

    def normalize_models(self, value, redact):
        if not isinstance(value, dict) or not isinstance(value.get("data"), list):
            _protocol()
        infos = []
        for entry in value["data"]:
            if not isinstance(entry, dict) or not isinstance(entry.get("id"), str) or not entry["id"].strip():
                _protocol()
            metadata = {}
            for key, data in entry.items():
                if key == "id":
                    continue
                try:
                    json.dumps(data)
                except Exception:
                    _protocol()
                if data is not None:
                    metadata[key] = data
            infos.append(ModelInfo(id=redact(entry["id"]), provider_metadata=metadata or None))
        return infos

    def model_page(self, value, redact):
        return self.normalize_models(value, redact), None, False

    def model_page_url(self, base_url, token):
        return base_url
def _parse_openai_stream_incremental(chunks: Iterator[bytes], request_id: Optional[str], redact) -> Iterator[Dict[str, Any]]:
    """Incremental SSE parser — yields events as soon as each SSE event completes, not after EOF."""
    decoder = codecs.getincrementaldecoder("utf-8")(errors="strict")
    line = ""
    data_buf: List[str] = []
    started = False
    cur_id: Optional[str] = None
    cur_model: Optional[str] = None
    finish: Optional[str] = None
    usage: Optional[Dict[str, int]] = None
    message_content = ""
    has_content = False
    tool_accum: Dict[int, Dict[str, str]] = {}

    def protocol():
        raise ConduitError("ProtocolError", "Malformed or incomplete Chat Completions stream.")

    def _handle_data(data_str: str):
        nonlocal started, cur_id, cur_model, finish, usage, message_content, has_content
        if data_str == "[DONE]":
            if not started or finish is None:
                protocol()
            msg: Dict[str, Any] = {"role": "assistant", "content": message_content if has_content else None}
            if tool_accum:
                msg["tool_calls"] = [{"id": v.get("id", f"call_{k}"), "type": "function", "function": {"name": v.get("name", ""), "arguments": v.get("arguments", "")}} for k, v in sorted(tool_accum.items())]
                if not has_content:
                    msg["content"] = None
            else:
                if not has_content and finish == "content_filter":
                    msg["content"] = None
                elif not has_content:
                    msg["content"] = None
                    if has_content:
                        msg["content"] = message_content
            body: Dict[str, Any] = {"id": cur_id, "model": cur_model, "choices": [{"message": msg, "finish_reason": finish}]}
            if usage is not None:
                body["usage"] = {"prompt_tokens": usage.get("input_tokens"), "completion_tokens": usage.get("output_tokens"), "total_tokens": usage.get("total_tokens")}
            resp = _decode_openai(body, request_id, redact)
            return ("done", {"type": "done", "response": resp})
        try:
            v = json.loads(data_str)
        except Exception:
            protocol()
        if not isinstance(v, dict):
            protocol()
        if isinstance(v.get("error"), dict):
            raise _http_error_openai(200, data_str, request_id, redact)
        if not isinstance(v.get("choices"), list) or len(v["choices"]) > 1:
            protocol()
        for field in ("id", "model"):
            prev = cur_id if field == "id" else cur_model
            if v.get(field) is not None and (not isinstance(v.get(field), str) or (prev is not None and prev != v.get(field))):
                protocol()
        if isinstance(v.get("id"), str):
            cur_id = v["id"]
        if isinstance(v.get("model"), str):
            cur_model = v["model"]
        reported = None
        if v.get("usage") is not None:
            try:
                reported = _normalize_usage_openai(v["usage"])
            except ConduitError:
                protocol()
        content_delta = ""
        tool_deltas: List[Dict[str, Any]] = []
        if len(v.get("choices", [])) == 0:
            if not started or not reported:
                protocol()
        else:
            choice = v["choices"][0]
            if not isinstance(choice, dict) or choice.get("index") != 0 or not isinstance(choice.get("delta"), dict):
                protocol()
            delta = choice["delta"]
            if delta.get("role") is not None and delta.get("role") != "assistant":
                protocol()
            if delta.get("content") is not None and not isinstance(delta.get("content"), str):
                protocol()
            if delta.get("tool_calls") is not None:
                if not isinstance(delta["tool_calls"], list):
                    protocol()
            if delta.get("reasoning_content") is not None and not isinstance(delta.get("reasoning_content"), str):
                protocol()
            for k, d in delta.items():
                if k not in ("role", "content", "reasoning_content", "tool_calls") and d is not None:
                    protocol()
            has_tc = isinstance(delta.get("tool_calls"), list) and len(delta["tool_calls"]) > 0
            if finish is not None and (delta.get("content") or has_tc or choice.get("finish_reason") is not None):
                protocol()
            if choice.get("finish_reason") is not None and not isinstance(choice.get("finish_reason"), str):
                protocol()
            if isinstance(choice.get("finish_reason"), str):
                finish = choice["finish_reason"]
            if isinstance(delta.get("content"), str):
                content_delta = delta["content"]
                has_content = True
            if isinstance(delta.get("tool_calls"), list):
                for tc in delta["tool_calls"]:
                    idx = tc.get("index")
                    fn = tc.get("function") or {}
                    cur = tool_accum.get(idx, {"arguments": ""})
                    if isinstance(tc.get("id"), str):
                        cur["id"] = tc["id"]
                    if isinstance(fn, dict) and "arguments" in fn and not isinstance(fn["arguments"], str):
                        protocol()
                    if isinstance(fn.get("name"), str):
                        cur["name"] = fn["name"]
                    if isinstance(fn.get("arguments"), str):
                        cur["arguments"] = cur.get("arguments", "") + fn["arguments"]
                    tool_accum[idx] = cur
                    tool_deltas.append({"index": idx, **({"id": tc["id"]} if isinstance(tc.get("id"), str) else {}), **({"name": fn["name"]} if isinstance(fn.get("name"), str) else {}), **({"argumentsDelta": fn["arguments"]} if isinstance(fn.get("arguments"), str) and fn["arguments"] else {})})
        events: List[Dict[str, Any]] = []
        if not started:
            started = True
            ev: Dict[str, Any] = {"type": "start"}
            if cur_id is not None:
                ev["id"] = redact(cur_id)
            if cur_model is not None:
                ev["model"] = redact(cur_model)
            events.append(ev)
        if content_delta:
            message_content += content_delta
            events.append({"type": "text_delta", "index": 0, "text": content_delta})
        elif False:
            pass
        for d in tool_deltas:
            if d.get("id") is not None or d.get("name") is not None or d.get("argumentsDelta") is not None:
                events.append({"type": "tool_call_delta", **d})
        if reported is not None:
            if usage is None:
                usage = {}
            usage.update(reported)
            events.append({"type": "usage", "usage": dict(usage)})
        return ("events", events)

    # incremental line handling with skipLF
    skip_lf = False
    pending_data: List[str] = []
    try:
        for chunk in chunks:
            try:
                text = decoder.decode(chunk, final=False)
            except UnicodeDecodeError:
                protocol()
            for ch in text:
                if skip_lf and ch == "\n":
                    skip_lf = False
                    continue
                skip_lf = (ch == "\r")
                if ch not in ("\r", "\n"):
                    line += ch
                    continue
                # line terminator
                if not line:
                    if pending_data:
                        data_str = "\n".join(pending_data)
                        pending_data = []
                        kind, payload = _handle_data(data_str)
                        if kind == "done":
                            yield payload
                            return
                        for ev in payload:
                            yield ev
                else:
                    if line.startswith("data:"):
                        val = line[5:]
                        if val.startswith(" "):
                            val = val[1:]
                        pending_data.append(val)
                    elif line.startswith("data"):
                        pending_data.append("")
                    # ignore other fields
                line = ""
        # flush decoder tail
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
                if pending_data:
                    data_str = "\n".join(pending_data)
                    pending_data = []
                    kind, payload = _handle_data(data_str)
                    if kind == "done":
                        yield payload
                        return
                    for ev in payload:
                        yield ev
            else:
                if line.startswith("data:"):
                    val = line[5:]
                    if val.startswith(" "):
                        val = val[1:]
                    pending_data.append(val)
                elif line.startswith("data"):
                    pending_data.append("")
            line = ""
        # flush pending without trailing blank line? spec needs blank line, but handle if pending_data remains
        if pending_data:
            data_str = "\n".join(pending_data)
            kind, payload = _handle_data(data_str)
            if kind == "done":
                yield payload
                return
            for ev in payload:
                yield ev
    except GeneratorExit:
        # caller closed early — propagate close to byte stream if possible
        try:
            if hasattr(chunks, "close"):
                chunks.close()  # type: ignore[attr-defined]
        except Exception:
            pass
        raise
    # if we exit without DONE
    raise ConduitError("ProtocolError", "Malformed or incomplete Chat Completions stream.")
