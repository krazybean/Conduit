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


def _encode_response_format(value: Any) -> Any:
    if value is None or value.get("type") == "text":
        return None
    if value.get("type") == "json":
        return "json"
    return value.get("schema")


def _ollama_error(status: int, body: str, request_id: Optional[str], redact, model: Optional[str] = None) -> ConduitError:
    msg: Optional[str] = None
    try:
        v = json.loads(body)
        if isinstance(v, dict) and isinstance(v.get("error"), str):
            msg = v["error"]
    except Exception:
        pass
    missing = False
    if model is not None and msg is not None:
        missing = msg in (f"model '{model}' not found", f'model "{model}" not found', f'model "{model}" not found, try pulling it first')
    details: Dict[str, str] = {}
    if msg is not None:
        details["message"] = redact(msg)
    return http_failure(status, details, request_id, missing)
def _ollama_request(model: str, messages: List[Dict[str, Any]], request: Dict[str, Any], stream: bool, wire_tools: Any, wire_format: Any) -> str:
    native = request.get("provider_options") or {}
    if not isinstance(native, dict):
        _invalid("providerOptions must be an object.")
    raw_options = native.get("options")
    if (raw_options is not None and not isinstance(raw_options, dict)) or any(k in native for k in ("model", "messages", "stream", "tools", "format", "tool_choice")) or (isinstance(raw_options, dict) and any(k in raw_options for k in ("num_predict", "temperature", "top_p", "stop"))):
        _invalid("Invalid Ollama options or conflict with a Conduit-owned field.")
    options = dict(raw_options) if isinstance(raw_options, dict) else {}
    mapped = {**options, "num_predict": request.get("max_output_tokens"), "temperature": request.get("temperature"), "top_p": request.get("top_p"), "stop": list(request["stop"]) if request.get("stop") is not None else None}
    # strip None
    wire_messages = []
    for m in messages:
        role = m["role"]
        parts = m["content"]
        texts: List[str] = []
        tool_calls: List[Any] = []
        tool_result = None
        for p in parts:
            if p["type"] == "text":
                texts.append(p["text"])
            elif p["type"] == "tool_call":
                tool_calls.append({"function": {"name": p["name"], "arguments": p["arguments"]}})
            elif p["type"] == "tool_result":
                inner = p["content"]
                if isinstance(inner, str):
                    content_str = inner
                else:
                    content_str = "".join(q.get("text", "") for q in inner)
                tool_result = {"content": content_str}
                if p.get("name"):
                    tool_result["tool_name"] = p["name"]
                elif p.get("call_id"):
                    tool_result["tool_name"] = p["call_id"]
        if role == "tool":
            if not tool_result:
                _invalid("Tool message must contain tool_result.")
            wire_messages.append({"role": role, "content": tool_result["content"], **({"tool_name": tool_result["tool_name"]} if tool_result.get("tool_name") else {})})
        elif tool_calls:
            wire_messages.append({"role": role, "content": "".join(texts), "tool_calls": tool_calls})
        else:
            wire_messages.append({"role": role, "content": "".join(texts)})
    body: Dict[str, Any] = {**native, "model": model, "messages": wire_messages, "stream": stream}
    if wire_tools is not None:
        body["tools"] = wire_tools
    if wire_format is not None:
        body["format"] = wire_format
    if any(v is not None for v in mapped.values()):
        body["options"] = {k: v for k, v in mapped.items() if v is not None}
    return json.dumps(body)
def _ollama_response(value: Any, request_id: Optional[str], redact) -> GenerationResponse:
    if isinstance(value, dict) and isinstance(value.get("error"), str):
        raise _ollama_error(200, json.dumps(value), request_id, redact)
    if not isinstance(value, dict) or not isinstance(value.get("done"), bool):
        _protocol("Malformed or unsupported Ollama chat response.")
    if not value["done"]:
        _protocol("Malformed or unsupported Ollama chat response.")
    msg = value.get("message")
    if not isinstance(msg, dict) or msg.get("role") != "assistant" or not isinstance(msg.get("content"), str):
        _protocol("Malformed or unsupported Ollama chat response.")
    text = msg["content"]
    has_tool = isinstance(msg.get("tool_calls"), list) and len(msg["tool_calls"]) > 0
    content: List[Dict[str, Any]] = []
    if text != "" or not has_tool:
        content.append({"type": "text", "text": text})
    if has_tool:
        for tc in msg["tool_calls"]:
            if not isinstance(tc, dict) or not isinstance(tc.get("function"), dict) or not isinstance(tc["function"].get("name"), str):
                _protocol("Malformed or unsupported Ollama chat response.")
            fn = tc["function"]
            args = fn.get("arguments") if fn.get("arguments") is not None else {}
            content.append({"type": "tool_call", "name": fn["name"], "arguments": args})
    finish = "tool_call" if has_tool else ("stop" if value.get("done_reason") in (None, "stop") else ("length" if value.get("done_reason") == "length" else "other"))
    if isinstance(value.get("done_reason"), str) and value["done_reason"] not in ("stop", "length"):
        # keep other
        pass
    usage = None
    if isinstance(value.get("prompt_eval_count"), int) or isinstance(value.get("eval_count"), int):
        usage = {}
        if isinstance(value.get("prompt_eval_count"), int) and value["prompt_eval_count"] >= 0:
            usage["input_tokens"] = value["prompt_eval_count"]
        if isinstance(value.get("eval_count"), int) and value["eval_count"] >= 0:
            usage["output_tokens"] = value["eval_count"]
    metadata: Dict[str, Any] = {}
    if request_id:
        metadata["request_id"] = request_id
    if isinstance(value.get("done_reason"), str):
        metadata["finish_reason"] = redact(value["done_reason"])
    return text_response({**({"model": redact(value["model"])} if isinstance(value.get("model"), str) else {}), "content": content, "finish_reason": finish, **({"usage": usage} if usage else {}), "provider_metadata": metadata})


# ---------------------------------------------------------------------------
# Streaming parsers (sync, reading bytes incrementally)
# ---------------------------------------------------------------------------

# Streaming parsers (sync, reading bytes incrementally)
def _parse_ollama_stream_incremental(chunks: Iterator[bytes], request_id: Optional[str], redact, model_id: Optional[str] = None) -> Iterator[Dict[str, Any]]:
    """Incremental NDJSON — yields start/text/usage as soon as each line arrives."""
    buf = b""
    started = False
    model_seen: Optional[str] = None
    usage: Optional[Dict[str, int]] = None
    content_acc = ""
    tool_calls: List[Any] = []
    last_val = None
    emitted_tool = 0
    def protocol():
        raise ConduitError("ProtocolError", "Malformed or incomplete Ollama stream.")


    try:
        for chunk in chunks:
            buf += chunk
            # process complete lines
            while b"\n" in buf:
                line_bytes, buf = buf.split(b"\n", 1)
                # strip \r if CRLF
                if line_bytes.endswith(b"\r"):
                    line_bytes = line_bytes[:-1]
                if not line_bytes.strip():
                    continue
                try:
                    rec = json.loads(line_bytes.decode("utf-8"))
                except Exception:
                    protocol()
                if isinstance(rec, dict) and isinstance(rec.get("error"), str):
                    raise _ollama_error(200, json.dumps(rec), request_id, redact)
                if not isinstance(rec, dict) or not isinstance(rec.get("done"), bool):
                    protocol()
                last_val = rec
                if isinstance(rec.get("model"), str):
                    if model_seen is not None and model_seen != rec["model"]:
                        protocol()
                    model_seen = rec["model"]
                u: Dict[str, int] = {}
                if isinstance(rec.get("prompt_eval_count"), int) and rec["prompt_eval_count"] >= 0:
                    u["input_tokens"] = rec["prompt_eval_count"]
                if isinstance(rec.get("eval_count"), int) and rec["eval_count"] >= 0:
                    u["output_tokens"] = rec["eval_count"]
                if u:
                    usage = {**(usage or {}), **u}
                msg = rec.get("message") if isinstance(rec.get("message"), dict) else None
                text_part = ""
                if isinstance(msg, dict) and isinstance(msg.get("content"), str):
                    text_part = msg["content"]
                    content_acc += text_part
                if isinstance(msg, dict) and isinstance(msg.get("tool_calls"), list):
                    for tc in msg["tool_calls"]:
                        if not isinstance(tc, dict):
                            protocol()
                        fn = tc.get("function")
                        if not isinstance(fn, dict) or not isinstance(fn.get("name"), str):
                            protocol()
                        # avoid duplicate tool calls across records (ollama repeats?)
                        # For incremental, emit only new tool calls
                        tool_calls.append({"name": fn["name"], "arguments": fn.get("arguments")})
                if not started:
                    started = True
                    yield {"type": "start", **({"model": redact(model_seen)} if model_seen else {})}
                if text_part:
                    yield {"type": "text_delta", "index": 0, "text": text_part}
                if u:
                    yield {"type": "usage", "usage": dict(usage or {})}
                # emit new tool deltas incrementally
                while emitted_tool < len(tool_calls):
                    tc = tool_calls[emitted_tool]
                    args_str = tc["arguments"] if isinstance(tc["arguments"], str) else json.dumps(tc["arguments"]) if tc["arguments"] is not None else ""
                    if args_str == "{}":
                        args_str = ""
                    ev: Dict[str, Any] = {"type": "tool_call_delta", "index": emitted_tool, "name": tc["name"]}
                    if args_str:
                        ev["argumentsDelta"] = args_str
                    yield ev
                    emitted_tool += 1
                if rec.get("done"):
                    # buffer remainder for final done outside loop
                    break
            else:
                continue
            break
        # handle trailing buf without newline at EOF
        if buf.strip():
            try:
                rec = json.loads(buf.decode("utf-8"))
            except Exception:
                protocol()
            if isinstance(rec, dict) and isinstance(rec.get("error"), str):
                raise _ollama_error(200, json.dumps(rec), request_id, redact)
            if not isinstance(rec, dict) or not isinstance(rec.get("done"), bool):
                protocol()
            last_val = rec
            if isinstance(rec.get("model"), str):
                if model_seen is not None and model_seen != rec["model"]:
                    protocol()
                model_seen = rec["model"]
            u = {}
            if isinstance(rec.get("prompt_eval_count"), int): u["input_tokens"] = rec["prompt_eval_count"]
            if isinstance(rec.get("eval_count"), int): u["output_tokens"] = rec["eval_count"]
            if u:
                usage = {**(usage or {}), **u}
                yield {"type": "usage", "usage": dict(usage or {})}
            msg = rec.get("message") if isinstance(rec.get("message"), dict) else None
            if isinstance(msg, dict) and isinstance(msg.get("content"), str) and msg["content"]:
                content_acc += msg["content"]
                if not started:
                    started = True
                    yield {"type": "start", **({"model": redact(model_seen)} if model_seen else {})}
                yield {"type": "text_delta", "index": 0, "text": msg["content"]}
            if isinstance(msg, dict) and isinstance(msg.get("tool_calls"), list):
                for tc in msg["tool_calls"]:
                    fn = tc.get("function")
                    tool_calls.append({"name": fn["name"], "arguments": fn.get("arguments")})
                    ev = {"type": "tool_call_delta", "index": len(tool_calls)-1, "name": fn["name"]}
                    args_str = fn.get("arguments") if isinstance(fn.get("arguments"), str) else ""
                    if args_str and args_str != "{}":
                        ev["argumentsDelta"] = args_str
                    yield ev
        if last_val is not None and last_val.get("done"):
            final_msg = {"role": "assistant", "content": content_acc}
            if tool_calls:
                final_msg["tool_calls"] = [{"function": {"name": tc["name"], "arguments": tc["arguments"]}} for tc in tool_calls]
            final_val = {**last_val, "message": final_msg, "model": model_seen, "prompt_eval_count": (usage or {}).get("input_tokens"), "eval_count": (usage or {}).get("output_tokens")}
            resp = _ollama_response(final_val, request_id, redact)
            yield {"type": "done", "response": resp}
        else:
            # no done or empty
            if not started:
                protocol()
            raise ConduitError("ProtocolError", "Malformed or incomplete Ollama stream.")
    except GeneratorExit:
        try:
            if hasattr(chunks, "close"):
                chunks.close()  # type: ignore[attr-defined]
        except Exception:
            pass
        raise

class OllamaProvider:
    stream_content_types = ("application/x-ndjson", "application/ndjson", "application/json")

    def encode_request(self, model, messages, request, streaming):
        tools = _encode_tools(request.get("tools"))
        response_format = _encode_response_format(request.get("response_format"))
        if request.get("tool_choice") is not None:
            raise ConduitError("UnsupportedCapabilityError", "toolChoice is not supported for Ollama; omit toolChoice or use providerOptions for native fields.")
        return _ollama_request(model, messages, request, streaming, tools, response_format)

    def decode_response(self, value, request_id, redact):
        return _ollama_response(value, request_id, redact)

    def http_error(self, status, body, request_id, redact, model=None):
        return _ollama_error(status, body, request_id, redact, model)

    def parse_stream(self, chunks, request_id, redact, model_id=None):
        return _parse_ollama_stream_incremental(chunks, request_id, redact, model_id)

    def normalize_models(self, value, redact):
        if not isinstance(value, dict) or not isinstance(value.get("models"), list):
            _protocol()
        infos = []
        for entry in value["models"]:
            if not isinstance(entry, dict):
                _protocol()
            model_id = entry.get("name") if isinstance(entry.get("name"), str) else entry.get("model") if isinstance(entry.get("model"), str) else None
            if not isinstance(model_id, str) or not model_id.strip():
                _protocol()
            metadata = {}
            for key, data in entry.items():
                if key in ("name", "model"):
                    continue
                try:
                    json.dumps(data)
                except Exception:
                    _protocol()
                if data is not None:
                    metadata[key] = data
            if isinstance(entry.get("model"), str) and entry["model"] != model_id:
                metadata["model"] = entry["model"]
            display = entry.get("name") if isinstance(entry.get("name"), str) and entry["name"].strip() else None
            infos.append(ModelInfo(id=redact(model_id), name=redact(display) if display else None, provider_metadata=metadata or None))
        return infos

    def model_page(self, value, redact):
        return self.normalize_models(value, redact), None, False

    def model_page_url(self, base_url, token):
        return base_url
