"""Conduit — provider-neutral Python driver (stdlib HTTP/JSON, sync).

Single-file port of the TypeScript reference (spec + typescript/src/*).
Drivers: openai-compatible, ollama, anthropic, gemini.
HTTP: stdlib http.client / http / json / ssl / urllib.
Timeout: integer milliseconds 1..2147483647 covering the whole operation.
Redaction: credentials and custom header values (and their URL encodings)
are replaced with [REDACTED] in diagnostic strings, never in semantic content.
"""
from __future__ import annotations

import codecs
import http.client
import json
import re
import socket
import threading
import time
import urllib.parse
from typing import Any, Dict, Iterator, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

ErrorCode = str  # keep union as runtime check

ERROR_CODES = {
    "AuthenticationError",
    "AuthorizationError",
    "ConnectionError",
    "TimeoutError",
    "RateLimitError",
    "InvalidRequestError",
    "UnsupportedCapabilityError",
    "ModelNotFoundError",
    "ProviderError",
    "ProtocolError",
    "CancelledError",
}


class ConduitError(Exception):
    """Normalized error contract.  ``name`` is the stable category."""

    def __init__(
        self,
        name: str,
        message: str,
        *,
        status_code: Optional[int] = None,
        provider_code: Optional[str] = None,
        request_id: Optional[str] = None,
        provider_details: Optional[Dict[str, str]] = None,
        cause: Optional[Dict[str, str]] = None,
    ) -> None:
        super().__init__(message)
        self.name: str = name  # type: ignore[assignment]
        self.status_code = status_code
        self.provider_code = provider_code
        self.request_id = request_id
        self.provider_details = provider_details
        self.cause = cause  # type: ignore[assignment]

    def __str__(self) -> str:
        return f"{self.name}: {self.args[0]}"

    def __repr__(self) -> str:
        return f"ConduitError({self.name!r}, {self.args[0]!r}, status_code={self.status_code!r})"


def http_failure(
    status: int,
    details: Dict[str, str],
    request_id: Optional[str] = None,
    model_not_found: bool = False,
) -> ConduitError:
    codes: Dict[int, str] = {400: "InvalidRequestError", 401: "AuthenticationError", 403: "AuthorizationError", 408: "TimeoutError", 422: "InvalidRequestError", 429: "RateLimitError"}
    name = "ModelNotFoundError" if status == 404 and model_not_found else codes.get(status, "ProviderError")
    msg = details.get("message") or f"Provider returned HTTP {status}."
    kwargs: Dict[str, Any] = {"status_code": status}
    if request_id is not None:
        kwargs["request_id"] = request_id
    if details.get("code") is not None:
        kwargs["provider_code"] = details["code"]
    if details:
        kwargs["provider_details"] = dict(details)
    return ConduitError(name, msg, **kwargs)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_owned_fields = {"model", "messages", "stream", "stream_options", "max_tokens", "max_completion_tokens", "temperature", "top_p", "stop", "tools", "tool_choice", "functions", "function_call", "response_format", "n", "modalities"}
_protected_headers = {"authorization", "proxy-authorization", "cookie", "host", "content-type", "content-length", "connection", "transfer-encoding", "upgrade", "trailer", "te", "keep-alive", "x-api-key", "anthropic-version", "x-goog-api-key"}
_request_fields = {"messages", "max_output_tokens", "temperature", "top_p", "stop", "tools", "tool_choice", "response_format", "provider_options", "signal", "timeout"}
_unsupported_fields = {"stream", "reasoning", "vision"}


def _invalid(msg: str) -> None:
    raise ConduitError("InvalidRequestError", msg)


def _protocol(msg: str = "Malformed or unsupported response.") -> None:
    raise ConduitError("ProtocolError", msg)


def _timeout_value(v: Any) -> None:
    if v is not None and (not isinstance(v, int) or isinstance(v, bool) or v < 1 or v > 2147483647):
        _invalid("timeout must be an integer from 1 through 2147483647 milliseconds.")


def _is_aborted(sig: Any) -> bool:
    if sig is None:
        return False
    # TS AbortSignal: {aborted: bool}, Python Event: is_set(), generic
    try:
        if getattr(sig, "aborted", False):
            return True
        if callable(getattr(sig, "is_set", None)) and sig.is_set():
            return True
        if getattr(sig, "cancelled", False):
            return True
    except Exception:
        return False
    return False


def _object(v: Any) -> bool:
    return isinstance(v, dict)


def _keys(value: dict, allowed: set) -> None:
    for k in value.keys():
        if not isinstance(k, str) or k not in allowed:
            _invalid("Unrecognized field; use providerOptions for native request settings.")


def _json_validate(value: Any, parents: Optional[set] = None) -> None:
    if parents is None:
        parents = set()
    if value is None or isinstance(value, (str, bool)):
        return
    if isinstance(value, (int, float)):
        if isinstance(value, bool):
            return
        if not _is_finite(value):
            _invalid("providerOptions must contain acyclic JSON data.")
        return
    if not isinstance(value, (dict, list)):
        _invalid("providerOptions must contain plain JSON data.")
    oid = id(value)
    if oid in parents:
        _invalid("providerOptions must contain acyclic JSON data.")
    parents.add(oid)
    if isinstance(value, list):
        for item in value:
            _json_validate(item, parents)
        # check sparse / extra props
        if len(value) != len(list(value)):
            _invalid("Invalid JSON array in providerOptions.")
    else:
        for k, vv in value.items():
            if not isinstance(k, str):
                _invalid("providerOptions must contain plain JSON data.")
            _json_validate(vv, parents)
    parents.remove(oid)


def _is_finite(v: Any) -> bool:
    if isinstance(v, bool):
        return True
    if isinstance(v, int):
        return True
    if isinstance(v, float):
        return v != float("inf") and v != float("-inf") and v == v
    return False


def _validate_tools(tools: Any) -> None:
    if tools is None:
        return
    if not isinstance(tools, list) or len(tools) == 0:
        _invalid("tools must be a nonempty array.")
    names: set = set()
    for tool in tools:
        if not _object(tool):
            _invalid("Each tool must be an object.")
        _keys(tool, {"name", "description", "input_schema"})
        name = tool.get("name")
        if not isinstance(name, str) or not name.strip():
            _invalid("Tool name must be a nonempty string.")
        if name in names:
            _invalid("Tool names must be unique.")
        names.add(name)
        desc = tool.get("description")
        if desc is not None and not isinstance(desc, str):
            _invalid("Tool description must be a string.")
        schema = tool.get("input_schema")
        try:
            _json_validate(schema)
        except ConduitError:
            raise
        except Exception:
            _invalid("Tool inputSchema must be JSON-compatible.")
        # also ensure JSON serializable
        try:
            json.dumps(schema)
        except Exception:
            _invalid("Tool inputSchema must be JSON-compatible.")


def _validate_tool_choice(choice: Any, tools: Any) -> None:
    if choice is None:
        return
    if isinstance(choice, str):
        if choice in ("auto", "none", "required"):
            return
        if not choice.strip():
            _invalid("toolChoice name must be a nonempty string.")
        if not tools or not any(isinstance(t, dict) and t.get("name") == choice for t in tools):
            _invalid("toolChoice name must match a supplied tool.")
        return
    if not _object(choice):
        _invalid('toolChoice must be "auto", "none", "required", or { name }.')
    _keys(choice, {"name"})
    name = choice.get("name")
    if not isinstance(name, str) or not name.strip():
        _invalid("toolChoice name must be a nonempty string.")
    if not tools or not any(isinstance(t, dict) and t.get("name") == name for t in tools):
        _invalid("toolChoice name must match a supplied tool.")


def _validate_response_format(v: Any) -> None:
    if v is None:
        return
    if not _object(v):
        _invalid("responseFormat must be an object.")
    t = v.get("type")
    if not isinstance(t, str):
        _invalid('responseFormat.type must be "text", "json", or "json_schema".')
    if t not in ("text", "json", "json_schema"):
        _invalid('responseFormat.type must be "text", "json", or "json_schema".')
    if t in ("text", "json"):
        _keys(v, {"type"})
        return
    _keys(v, {"type", "schema"})
    if "schema" not in v:
        _invalid("responseFormat.schema is required for json_schema.")
    try:
        _json_validate(v.get("schema"))
        json.dumps(v.get("schema"))
    except ConduitError:
        raise
    except Exception:
        _invalid("responseFormat.schema must be JSON-compatible.")


# ---------------------------------------------------------------------------
# Response types
# ---------------------------------------------------------------------------

class GenerationResponse:
    def __init__(self, *, id: Optional[str] = None, model: Optional[str] = None, content: List[Dict[str, Any]], finish_reason: str, usage: Optional[Dict[str, int]] = None, provider_metadata: Optional[Dict[str, Any]] = None):
        self.id = id
        self.model = model
        self.content = content
        self.finish_reason = finish_reason
        self.usage = usage
        self.provider_metadata: Dict[str, Any] = provider_metadata or {}

    @property
    def text(self) -> str:
        return "".join(p.get("text", "") for p in self.content if p.get("type") == "text")

    @property
    def tool_calls(self) -> List[Dict[str, Any]]:
        return [p for p in self.content if p.get("type") == "tool_call"]

    def __getitem__(self, key: str) -> Any:
        if key == "id": return self.id
        if key == "model": return self.model
        if key == "content": return self.content
        if key == "finish_reason": return self.finish_reason
        if key == "usage": return self.usage
        if key == "provider_metadata": return self.provider_metadata
        if key == "text": return self.text
        if key == "tool_calls": return self.tool_calls
        raise KeyError(key)

    def __contains__(self, key: object) -> bool:
        return key in ("id", "model", "content", "finish_reason", "usage", "provider_metadata", "text", "tool_calls")

    def get(self, key: str, default: Any = None) -> Any:
        try:
            return self.__getitem__(key)
        except KeyError:
            return default

    def keys(self):
        return ("id", "model", "content", "finish_reason", "usage", "provider_metadata", "text", "tool_calls")

    def __iter__(self):
        return iter(self.keys())

    def __repr__(self) -> str:
        return f"GenerationResponse(id={self.id!r}, finish_reason={self.finish_reason!r}, text={self.text!r})"


def _text_response(fields: Dict[str, Any]) -> GenerationResponse:
    return GenerationResponse(
        id=fields.get("id"),
        model=fields.get("model"),
        content=fields.get("content", []),
        finish_reason=fields["finish_reason"],
        usage=fields.get("usage"),
        provider_metadata=fields.get("provider_metadata", {}),
    )


class ModelInfo:
    def __init__(self, id: str, name: Optional[str] = None, provider_metadata: Optional[Dict[str, Any]] = None):
        self.id = id
        self.name = name
        self.provider_metadata = provider_metadata

    def __repr__(self) -> str:
        return f"ModelInfo(id={self.id!r})"


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------

def _make_redactor(secrets: List[str]):
    uniq = sorted(set(s for s in secrets if s), key=len, reverse=True)
    # also url-encoded and stripped forms (header " synthetic-header-83 " must not leak "synthetic-header-83")
    expanded: List[str] = []
    seen: set = set()
    for s in uniq:
        variants = [s, urllib.parse.quote(s, safe="")]
        stripped = s.strip()
        if stripped and stripped != s:
            variants.extend([stripped, urllib.parse.quote(stripped, safe="")])
        for v in variants:
            if v and v not in seen:
                seen.add(v)
                expanded.append(v)
    expanded.sort(key=len, reverse=True)

    def redact(text: str) -> str:
        for sec in expanded:
            if sec:
                text = text.replace(sec, "[REDACTED]")
        return text

    return redact


# ---------------------------------------------------------------------------
# HTTP via stdlib
# ---------------------------------------------------------------------------

def _do_http(method: str, url: str, headers: Dict[str, str], body: Optional[str], timeout_ms: Optional[int], redact=None) -> Tuple[int, Dict[str, str], bytes]:
    """Sync HTTP using http.client, with operation-level timeout.

    timeout_ms covers the whole operation (connect + read). We implement it
    via a deadline timer that closes the socket on expiry, mapping to TimeoutError.
    Redirects are not followed (3xx returned as-is for caller to map).
    """
    parsed = urllib.parse.urlparse(url)
    host = parsed.hostname or ""
    port = parsed.port
    is_https = parsed.scheme == "https"
    path = parsed.path or "/"
    if parsed.query:
        path += "?" + parsed.query

    deadline: Optional[float] = None
    if timeout_ms is not None:
        deadline = time.monotonic() + timeout_ms / 1000.0

    # socket timeout: remaining time or None
    def _remaining() -> Optional[float]:
        if deadline is None:
            return None
        rem = deadline - time.monotonic()
        return max(0.001, rem) if rem > 0 else 0.001

    # create connection
    if is_https:
        conn: http.client.HTTPConnection = http.client.HTTPSConnection(host, port, timeout=_remaining())  # type: ignore[assignment]
    else:
        conn = http.client.HTTPConnection(host, port, timeout=_remaining())

    timer: Optional[threading.Timer] = None
    timed_out = threading.Event()

    def _on_timeout() -> None:
        timed_out.set()
        try:
            conn.close()
        except Exception:
            pass
        try:
            sock = getattr(conn, "sock", None)
            if sock is not None:
                try:
                    sock.shutdown(2)  # type: ignore[attr-defined]
                except Exception:
                    pass
        except Exception:
            pass

    if deadline is not None:
        delay = max(0.001, deadline - time.monotonic())
        timer = threading.Timer(delay, _on_timeout)
        timer.daemon = True
        timer.start()

    try:
        if timed_out.is_set():
            raise ConduitError("TimeoutError", "Request deadline exceeded.")
        # need to ensure timeout not exceeded before sending
        if deadline is not None and time.monotonic() >= deadline:
            raise ConduitError("TimeoutError", "Request deadline exceeded.")
        # update socket timeout to remaining
        try:
            if deadline is not None:
                rem = deadline - time.monotonic()
                if rem <= 0:
                    raise ConduitError("TimeoutError", "Request deadline exceeded.")
                conn.timeout = rem  # type: ignore[attr-defined]
        except ConduitError:
            raise
        except Exception:
            pass
        conn.request(method, path, body=body.encode("utf-8") if body is not None else None, headers=headers)
        resp = conn.getresponse()
        status = resp.status
        # collect headers case-insensitive lower
        resp_headers: Dict[str, str] = {}
        for k, v in resp.getheaders():
            lk = k.lower()
            # keep first; also need get for request-id
            if lk not in resp_headers:
                resp_headers[lk] = v
            # also store original lower
            resp_headers[k.lower()] = v
        # read body with deadline checks
        chunks: List[bytes] = []
        while True:
            if timed_out.is_set():
                raise ConduitError("TimeoutError", "Request deadline exceeded.")
            if deadline is not None and time.monotonic() >= deadline:
                _on_timeout()
                raise ConduitError("TimeoutError", "Request deadline exceeded.")
            # read with small timeout remaining
            try:
                if deadline is not None:
                    rem = deadline - time.monotonic()
                    if rem <= 0:
                        raise ConduitError("TimeoutError", "Request deadline exceeded.")
                    # set socket timeout to remaining for this read
                    try:
                        sock = getattr(conn, "sock", None)
                        if sock is not None:
                            sock.settimeout(rem)  # type: ignore[union-attr]
                    except Exception:
                        pass
            except ConduitError:
                raise
            data = resp.read(8192)
            if not data:
                break
            chunks.append(data)
            if timed_out.is_set():
                raise ConduitError("TimeoutError", "Request deadline exceeded.")
        body_bytes = b"".join(chunks)
        return status, resp_headers, body_bytes
    except ConduitError:
        raise
    except (TimeoutError, socket.timeout) as e:
        if deadline is not None:
            raise ConduitError("TimeoutError", "Request deadline exceeded.") from e
        raise ConduitError("ConnectionError", "Provider connection failed.", cause={"name": type(e).__name__, "message": "[REDACTED]"}) from e
    except OSError as e:
        if timed_out.is_set():
            raise ConduitError("TimeoutError", "Request deadline exceeded.") from e
        # socket-level timeout that subclasses OSError but wasn't caught above (e.g. string check)
        if isinstance(e, TimeoutError) or "timed out" in str(e).lower():
            if deadline is not None:
                raise ConduitError("TimeoutError", "Request deadline exceeded.") from e
        raise ConduitError("ConnectionError", "Provider connection failed.", cause={"name": type(e).__name__, "message": "[REDACTED]"}) from e
    except Exception as e:
        if isinstance(e, ConduitError):
            raise
        if timed_out.is_set():
            raise ConduitError("TimeoutError", "Request deadline exceeded.") from e
        if isinstance(e, TimeoutError) or "timed out" in str(type(e).__name__).lower():
            if deadline is not None:
                raise ConduitError("TimeoutError", "Request deadline exceeded.") from e
        raise ConduitError("ConnectionError", "Provider connection failed.", cause={"name": type(e).__name__, "message": "[REDACTED]"}) from e
    finally:
        if timer is not None:
            timer.cancel()
        try:
            conn.close()
        except Exception:
            pass


def _do_http_stream(method: str, url: str, headers: Dict[str, str], body: Optional[str], timeout_ms: Optional[int], redact=None) -> Iterator[bytes]:
    """Yield raw bytes incrementally for streaming, with deadline enforcement.

    Caller must exhaust or close. We return a generator that manages the connection.
    """
    parsed = urllib.parse.urlparse(url)
    host = parsed.hostname or ""
    port = parsed.port
    is_https = parsed.scheme == "https"
    path = parsed.path or "/"
    if parsed.query:
        path += "?" + parsed.query

    deadline: Optional[float] = None
    if timeout_ms is not None:
        deadline = time.monotonic() + timeout_ms / 1000.0

    def _remaining() -> Optional[float]:
        if deadline is None:
            return None
        rem = deadline - time.monotonic()
        return max(0.05, rem) if rem > 0 else 0.05

    if is_https:
        conn = http.client.HTTPSConnection(host, port, timeout=_remaining())
    else:
        conn = http.client.HTTPConnection(host, port, timeout=_remaining())

    timed_out = threading.Event()
    timer: Optional[threading.Timer] = None

    def _on_timeout() -> None:
        timed_out.set()
        try:
            conn.close()
        except Exception:
            pass
        try:
            sock = getattr(conn, "sock", None)
            if sock is not None:
                try:
                    sock.shutdown(2)  # type: ignore[attr-defined]
                except Exception:
                    pass
        except Exception:
            pass

    if deadline is not None:
        delay = max(0.001, deadline - time.monotonic())
        timer = threading.Timer(delay, _on_timeout)
        timer.daemon = True
        timer.start()

    try:
        if deadline is not None and time.monotonic() >= deadline:
            raise ConduitError("TimeoutError", "Request deadline exceeded.")
        conn.request(method, path, body=body.encode("utf-8") if body is not None else None, headers=headers)
        resp = conn.getresponse()
        status = resp.status
        resp_headers = {k.lower(): v for k, v in resp.getheaders()}
        # check status before streaming
        if status < 200 or status >= 300:
            # read body for error
            body_bytes = resp.read()
            text = body_bytes.decode("utf-8", errors="replace")
            # map to error later via caller - but we need to raise here with info
            # Store for caller to raise
            raise _StreamHttpError(status, resp_headers, text)
        # validate content-type for streaming done by caller; here just yield
        # stream loop
        while True:
            if timed_out.is_set():
                raise ConduitError("TimeoutError", "Request deadline exceeded.")
            if deadline is not None and time.monotonic() >= deadline:
                _on_timeout()
                raise ConduitError("TimeoutError", "Request deadline exceeded.")
            if deadline is not None:
                rem = deadline - time.monotonic()
                if rem <= 0:
                    raise ConduitError("TimeoutError", "Request deadline exceeded.")
                try:
                    sock = getattr(conn, "sock", None)
                    if sock is not None:
                        sock.settimeout(rem)
                except Exception:
                    pass
            chunk = resp.read(4096)
            if not chunk:
                break
            if timed_out.is_set():
                raise ConduitError("TimeoutError", "Request deadline exceeded.")
            yield chunk
    except _StreamHttpError:
        raise
    except ConduitError:
        raise
    except (TimeoutError, socket.timeout) as e:
        if deadline is not None:
            raise ConduitError("TimeoutError", "Request deadline exceeded.") from e
        raise ConduitError("ConnectionError", "Provider connection failed.", cause={"name": type(e).__name__, "message": "[REDACTED]"}) from e
    except OSError as e:
        if timed_out.is_set():
            raise ConduitError("TimeoutError", "Request deadline exceeded.") from e
        if isinstance(e, TimeoutError) or "timed out" in str(e).lower():
            if deadline is not None:
                raise ConduitError("TimeoutError", "Request deadline exceeded.") from e
        raise ConduitError("ConnectionError", "Provider connection failed.", cause={"name": type(e).__name__, "message": "[REDACTED]"}) from e
    except Exception as e:
        if isinstance(e, ConduitError):
            raise
        if timed_out.is_set():
            raise ConduitError("TimeoutError", "Request deadline exceeded.") from e
        if isinstance(e, TimeoutError) or "timed out" in str(type(e).__name__).lower():
            if deadline is not None:
                raise ConduitError("TimeoutError", "Request deadline exceeded.") from e
        raise ConduitError("ConnectionError", "Provider connection failed.", cause={"name": type(e).__name__, "message": "[REDACTED]"}) from e
    finally:
        if timer is not None:
            timer.cancel()
        try:
            conn.close()
        except Exception:
            pass


class _StreamHttpError(Exception):
    def __init__(self, status: int, headers: Dict[str, str], body_text: str):
        super().__init__(f"HTTP {status}")
        self.status = status
        self.headers = headers
        self.body_text = body_text


# ---------------------------------------------------------------------------
# Driver helpers (mirrors TS)
# ---------------------------------------------------------------------------

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
    has_tool_calls = isinstance(message.get("tool_calls"), list)
    if has_tool_calls:
        if len(message["tool_calls"]) == 0:
            protocol()
        for tc in message["tool_calls"]:
            if not isinstance(tc, dict) or not isinstance(tc.get("id"), str) or tc.get("type") != "function" or not isinstance(tc.get("function"), dict) or not isinstance(tc["function"].get("name"), str) or not isinstance(tc["function"].get("arguments"), str):
                protocol()
            if tc["function"]["arguments"] != "":
                try:
                    json.loads(tc["function"]["arguments"])
                except Exception:
                    protocol()
    else:
        for k, d in message.items():
            if k not in ("role", "content") and d is not None and not (k == "tool_calls" and isinstance(d, list) and len(d) == 0):
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
            try:
                args = json.loads(args_s) if args_s != "" else {}
            except Exception:
                protocol()
            content.append({"type": "tool_call", "id": tc["id"], "name": fn["name"], "arguments": args})
    return _text_response({
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

def _encode_tools(tools: Optional[List[Dict[str, Any]]]) -> Optional[List[Dict[str, Any]]]:
    if tools is None:
        return None
    return [{"type": "function", "function": {"name": t["name"], **({"description": t["description"]} if t.get("description") is not None else {}), "parameters": t["input_schema"]}} for t in tools]


def _encode_tool_choice(choice: Any, driver: Optional[str] = None) -> Any:
    if choice is None:
        return None
    if driver == "anthropic":
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
    if isinstance(choice, str):
        if choice in ("auto", "none", "required"):
            return choice
        return {"type": "function", "function": {"name": choice}}
    if isinstance(choice, dict) and isinstance(choice.get("name"), str):
        return {"type": "function", "function": {"name": choice["name"]}}
    return choice


def _encode_tools_anthropic(tools: Optional[List[Dict[str, Any]]]) -> Optional[List[Dict[str, Any]]]:
    if tools is None:
        return None
    return [{"name": t["name"], **({"description": t["description"]} if t.get("description") is not None else {}), "input_schema": t["input_schema"]} for t in tools]


def _validate_response_format_wrap(v: Any) -> None:
    _validate_response_format(v)


def _encode_response_format(value: Any, driver: str) -> Any:
    if value is None:
        return None
    fmt = value
    if fmt.get("type") == "text":
        return None
    if fmt.get("type") == "json":
        return "json" if driver == "ollama" else {"type": "json_object"}
    schema = fmt.get("schema")
    if driver == "ollama":
        return schema
    return {"type": "json_schema", "json_schema": {"name": "response", "strict": True, "schema": schema}}


def _encode_gemini_tools(tools: Optional[List[Dict[str, Any]]]) -> Optional[List[Dict[str, Any]]]:
    if tools is None:
        return None
    return [{"functionDeclarations": [{"name": t["name"], **({"description": t["description"]} if t.get("description") else {}), "parametersJsonSchema": t["input_schema"]} for t in tools]}]


def _encode_gemini_tool_choice(choice: Any) -> Any:
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


def _encode_gemini_format(value: Any) -> Any:
    if value is None:
        return None
    if value.get("type") == "text":
        return None
    if value.get("type") == "json":
        return {"responseMimeType": "application/json"}
    return {"responseMimeType": "application/json", "responseJsonSchema": value.get("schema")}


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


def _encode_request(model: str, request: Dict[str, Any], streaming: bool, driver: str) -> str:
    if not isinstance(request, dict):
        _invalid("A generation request is required.")
    for k in list(request.keys()):
        if k in _unsupported_fields:
            raise ConduitError("UnsupportedCapabilityError", "Only text streaming is implemented." if streaming else "Only non-streaming text generation is implemented.")
    # validate allowed keys
    for k in request.keys():
        if k not in _request_fields:
            _invalid("Unrecognized field; use providerOptions for native request settings.")
    msgs = request.get("messages")
    if not isinstance(msgs, list) or len(msgs) == 0:
        _invalid("messages must be a nonempty array.")
    _validate_tools(request.get("tools"))
    _validate_tool_choice(request.get("tool_choice"), request.get("tools"))
    _validate_response_format_wrap(request.get("response_format"))

    # normalize messages
    messages: List[Dict[str, Any]] = []
    for msg in msgs:
        if not isinstance(msg, dict):
            _invalid("Each message must be an object.")
        for k in msg.keys():
            if k not in ("role", "content"):
                _invalid("Unrecognized field; use providerOptions for native request settings.")
        role = msg.get("role")
        if role not in ("system", "user", "assistant", "tool"):
            _invalid("Invalid message role.")
        raw = msg.get("content")
        raw_parts = [{"type": "text", "text": raw}] if isinstance(raw, str) else raw
        if not isinstance(raw_parts, list):
            _invalid("Message content must be a string or text-part array.")
        if len(raw_parts) == 0:
            _invalid("Message content must be nonempty.")
        content: List[Dict[str, Any]] = []
        has_tool_call = False
        has_tool_result = False
        has_text = False
        for part in raw_parts:
            if not isinstance(part, dict):
                _invalid("Invalid content part.")
            t = part.get("type")
            if t == "text":
                for k in part.keys():
                    if k not in ("type", "text"):
                        _invalid("Unrecognized field; use providerOptions for native request settings.")
                if not isinstance(part.get("text"), str):
                    _invalid("Invalid text content part.")
                has_text = True
                content.append({"type": "text", "text": part["text"]})
            elif t == "tool_call":
                for k in part.keys():
                    if k not in ("type", "id", "name", "arguments"):
                        _invalid("Unrecognized field; use providerOptions for native request settings.")
                if not isinstance(part.get("name"), str) or not part["name"].strip():
                    _invalid("Tool call name must be a nonempty string.")
                if part.get("id") is not None and not isinstance(part.get("id"), str):
                    _invalid("Tool call id must be a string.")
                try:
                    _json_validate(part.get("arguments"))
                    json.dumps(part.get("arguments"))
                except ConduitError:
                    raise
                except Exception:
                    _invalid("Tool call arguments must be JSON-compatible.")
                has_tool_call = True
                content.append({"type": "tool_call", **({"id": part["id"]} if part.get("id") is not None else {}), "name": part["name"], "arguments": part["arguments"]})
            elif t == "tool_result":
                for k in part.keys():
                    if k not in ("type", "call_id", "name", "content"):
                        _invalid("Unrecognized field; use providerOptions for native request settings.")
                if part.get("call_id") is not None and not isinstance(part.get("call_id"), str):
                    _invalid("Tool result callId must be a string.")
                if part.get("name") is not None and not isinstance(part.get("name"), str):
                    _invalid("Tool result name must be a string.")
                inner = part.get("content")
                if not (isinstance(inner, str) or isinstance(inner, list)):
                    _invalid("Tool result content must be a string or text-part array.")
                inner_list = [{"type": "text", "text": inner}] if isinstance(inner, str) else inner
                if not isinstance(inner_list, list):
                    _invalid("Tool result content must be a string or text-part array.")
                for q in inner_list:
                    if not isinstance(q, dict):
                        _invalid("Invalid tool result content part.")
                    for k in q.keys():
                        if k not in ("type", "text"):
                            _invalid("Unrecognized field; use providerOptions for native request settings.")
                    if q.get("type") != "text" or not isinstance(q.get("text"), str):
                        _invalid("Tool result content must be text.")
                has_tool_result = True
                content.append({"type": "tool_result", **({"call_id": part["call_id"]} if part.get("call_id") is not None else {}), **({"name": part["name"]} if part.get("name") is not None else {}), "content": part["content"]})
            elif t == "image":
                raise ConduitError("UnsupportedCapabilityError", "Image content is not implemented.")
            else:
                _invalid("Invalid content part type.")
        if role == "tool" and not has_tool_result:
            _invalid("Tool messages must contain a tool_result part.")
        if role != "tool" and has_tool_result:
            _invalid("Only tool messages may contain tool_result parts.")
        if has_tool_call and role != "assistant":
            _invalid("Only assistant messages may contain tool_call parts.")
        if role == "tool" and has_tool_call:
            _invalid("Tool messages must not contain tool_call parts.")
        if role == "tool" and has_text:
            _invalid("Tool messages must not contain text parts; use tool_result.")
        messages.append({"role": role, "content": content})

    max_output = request.get("max_output_tokens")
    if max_output is not None and (not isinstance(max_output, int) or isinstance(max_output, bool) or (driver == "anthropic" and max_output < 0) or (driver != "anthropic" and max_output < 1)):
        _invalid("maxOutputTokens must be a nonnegative safe integer." if driver == "anthropic" else "maxOutputTokens must be a positive safe integer.")
    temp = request.get("temperature")
    if temp is not None:
        if not isinstance(temp, (int, float)) or isinstance(temp, bool) or temp != temp or temp == float("inf") or temp == float("-inf") or temp < 0 or (driver == "openai-compatible" and temp > 2) or (driver == "anthropic" and temp > 1):
            if driver == "anthropic":
                _invalid("temperature must be between 0 and 1.")
            _invalid("temperature must be between 0 and 2." if driver != "ollama" else "temperature must be finite and nonnegative.")
    top_p = request.get("top_p")
    if top_p is not None and (not isinstance(top_p, (int, float)) or isinstance(top_p, bool) or top_p != top_p or top_p == float("inf") or top_p == float("-inf") or top_p < 0 or top_p > 1):
        _invalid("topP must be between 0 and 1.")
    stop = request.get("stop")
    if stop is not None and (not isinstance(stop, list) or any(not isinstance(v, str) for v in stop)):
        _invalid("stop must be an array of strings.")
    provider_options = request.get("provider_options")
    if provider_options is not None:
        if not isinstance(provider_options, dict):
            _invalid("providerOptions must be an object.")
        if driver == "openai-compatible" and any(k in provider_options for k in _owned_fields if not (streaming and k == "stream_options")):
            _invalid("providerOptions conflicts with a Conduit-owned field.")
        if driver == "openai-compatible" and streaming and provider_options.get("stream_options") is not None and not isinstance(provider_options["stream_options"], dict):
            _invalid("stream_options must be a JSON object.")
        try:
            _json_validate(provider_options)
            json.dumps(provider_options)
        except ConduitError:
            raise
        except Exception:
            _invalid("providerOptions must contain plain JSON data.")
    if driver == "anthropic" and request.get("response_format") is not None:
        raise ConduitError("UnsupportedCapabilityError", "responseFormat is not supported for Anthropic.")

    wire_tools_gemini = _encode_gemini_tools(request.get("tools"))
    wire_choice_gemini = _encode_gemini_tool_choice(request.get("tool_choice"))
    wire_format_gemini = _encode_gemini_format(request.get("response_format"))
    wire_tools = _encode_tools_anthropic(request.get("tools")) if driver == "anthropic" else (wire_tools_gemini if driver == "gemini" else _encode_tools(request.get("tools")))
    wire_choice = wire_choice_gemini if driver == "gemini" else _encode_tool_choice(request.get("tool_choice"), driver)
    wire_format = None if driver == "anthropic" else (wire_format_gemini if driver == "gemini" else _encode_response_format(request.get("response_format"), driver))

    if driver == "ollama":
        if wire_choice is not None:
            raise ConduitError("UnsupportedCapabilityError", "toolChoice is not supported for Ollama; omit toolChoice or use providerOptions for native fields.")
        return _ollama_request(model, messages, request, streaming, wire_tools, wire_format)
    if driver == "anthropic":
        return _anthropic_request(model, messages, request, streaming, wire_tools, wire_choice)
    if driver == "gemini":
        return _gemini_request(messages, request, wire_tools, wire_choice, wire_format)
    # openai-compatible
    wire_messages = []
    for m in messages:
        role = m["role"]
        parts = m["content"]
        if role == "tool":
            tr = next((p for p in parts if p["type"] == "tool_result"), None)
            if tr is None:
                _invalid("Tool message must contain tool_result.")
            inner = tr["content"]
            inner_text = inner if isinstance(inner, str) else "".join(q.get("text", "") for q in inner)
            wire_messages.append({"role": "tool", "content": inner_text, "tool_call_id": tr.get("call_id") or tr.get("name") or "tool"})
        elif any(p["type"] == "tool_call" for p in parts):
            texts = [p["text"] for p in parts if p["type"] == "text"]
            tcs = []
            for p in parts:
                if p["type"] == "tool_call":
                    tcs.append({"id": p.get("id") or f"call_{len(tcs)}", "type": "function", "function": {"name": p["name"], "arguments": json.dumps(p["arguments"])}})
            wire_messages.append({"role": role, "content": "".join(texts) if texts else None, "tool_calls": tcs})
        else:
            texts = [p["text"] for p in parts if p["type"] == "text"]
            # spec: text parts as array of text objects
            # openai chat completions expects content as array of text parts or string; we use array form
            if len(texts) == 1 and len(parts) == 1:
                # keep minimal but conformance expects array form? Use array for parity
                wire_messages.append({"role": role, "content": [{"type": "text", "text": texts[0]}]})
            else:
                wire_messages.append({"role": role, "content": [{"type": "text", "text": t} for t in texts]})
    body: Dict[str, Any] = {"model": model, "messages": wire_messages, "stream": streaming}
    if request.get("max_output_tokens") is not None:
        body["max_tokens"] = request["max_output_tokens"]
    if request.get("temperature") is not None:
        body["temperature"] = request["temperature"]
    if request.get("top_p") is not None:
        body["top_p"] = request["top_p"]
    if request.get("stop") is not None:
        body["stop"] = list(request["stop"])
    if wire_tools is not None:
        body["tools"] = wire_tools
    if wire_choice is not None:
        body["tool_choice"] = wire_choice
    if wire_format is not None:
        body["response_format"] = wire_format
    if provider_options:
        for k, v in provider_options.items():
            if k not in body:
                body[k] = v
            elif k == "stream_options" and streaming:
                body[k] = v
    return json.dumps(body)


# ---------------------------------------------------------------------------
# Response decoders for other drivers (simplified but spec-compliant enough)
# ---------------------------------------------------------------------------

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
    return _text_response({"id": redact(value["id"]), "model": redact(value["model"]), "content": content, "finish_reason": fr, **({"usage": usage} if usage else {}), "provider_metadata": metadata})


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
            return _text_response({"content": [], "finish_reason": "content_filter", **({"usage": usage} if usage else {}), "provider_metadata": {"finish_reason": redact(pf["blockReason"]), **({"request_id": request_id} if request_id else {})}})
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
    return _text_response({
        **({"id": redact(value["responseId"])} if isinstance(value.get("responseId"), str) else {}),
        **({"model": redact(value["modelVersion"])} if isinstance(value.get("modelVersion"), str) else {}),
        "content": content, "finish_reason": finish, **({"usage": usage} if usage else {}), "provider_metadata": metadata,
    })


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
    return _text_response({**({"model": redact(value["model"])} if isinstance(value.get("model"), str) else {}), "content": content, "finish_reason": finish, **({"usage": usage} if usage else {}), "provider_metadata": metadata})


# ---------------------------------------------------------------------------
# Streaming parsers (sync, reading bytes incrementally)
# ---------------------------------------------------------------------------

# Streaming parsers (sync, reading bytes incrementally)
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
                    for k, d in delta.items():
                        if k not in ("role", "content", "tool_calls") and d is not None:
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
            for k, d in delta.items():
                if k not in ("role", "content", "tool_calls") and d is not None:
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


# ---------------------------------------------------------------------------
# Client / Model
# ---------------------------------------------------------------------------

class Model:
    def __init__(self, client: "Client", model_id: str):
        self._client = client
        self._model_id = model_id

    def _norm(self, request: Any, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        if isinstance(request, str):
            if kwargs:
                _invalid("A generation request is required.")
            return {"messages": [{"role": "user", "content": request}]}
        if request is None:
            return dict(kwargs) if kwargs else {}
        if not isinstance(request, dict):
            _invalid("A generation request is required.")
        if kwargs:
            request = {**request, **kwargs}
        return request

    def generate(self, request: Optional[Any] = None, **kwargs: Any) -> GenerationResponse:
        return self._client._generate(self._model_id, self._norm(request, kwargs), False)

    def stream(self, request: Optional[Any] = None, **kwargs: Any) -> Iterator[Dict[str, Any]]:
        return self._client._stream(self._model_id, self._norm(request, kwargs))

    def __repr__(self) -> str:
        return f"Model(id={self._model_id!r})"


class Client:
    def __init__(self, *, driver: str, endpoint: str, url: str, list_url: str, headers: Dict[str, str], redact, default_timeout: Optional[int]):
        self._driver = driver
        self._endpoint = endpoint
        self._url = url
        self._list_url = list_url
        self._headers = headers
        self._redact = redact
        self._default_timeout = default_timeout

    def model(self, model_id: str) -> Model:
        if not isinstance(model_id, str) or not model_id.strip():
            _invalid("model must be a nonempty string.")
        return Model(self, model_id)

    def list_models(self, options: Optional[Dict[str, Any]] = None) -> List[ModelInfo]:
        if options is not None and not isinstance(options, dict):
            _invalid("listModels options must be an object.")
        if _is_aborted(options.get("signal") if isinstance(options, dict) else None):
            raise ConduitError("CancelledError", "Request cancelled by caller.")
        raw_timeout = options.get("timeout") if isinstance(options, dict) else None
        _timeout_value(raw_timeout)
        timeout = raw_timeout if raw_timeout is not None else self._default_timeout

        def protocol_models(msg: str = "Malformed or unsupported model listing response.") -> None:
            raise ConduitError("ProtocolError", msg)

        if self._driver == "anthropic":
            all_models: List[ModelInfo] = []
            cursor: Optional[str] = None
            seen: set = set()
            for _ in range(100):
                page_url = self._list_url if cursor is None else f"{self._list_url}?after_id={urllib.parse.quote(cursor, safe='')}"
                if cursor is not None and cursor in seen:
                    protocol_models("Malformed pagination cursor.")
                if cursor is not None:
                    seen.add(cursor)
                status, resp_headers, body_bytes = _do_http("GET", page_url, self._headers, None, timeout, self._redact)
                req_id = resp_headers.get("x-request-id") or resp_headers.get("request-id")
                req_id = self._redact(req_id) if req_id else None
                if status < 200 or status >= 300:
                    raise _anthropic_error(status, body_bytes.decode("utf-8", errors="replace"), req_id, self._redact)
                try:
                    val = json.loads(body_bytes.decode("utf-8"))
                except Exception:
                    protocol_models("Provider returned invalid JSON.")
                if not isinstance(val, dict) or not isinstance(val.get("data"), list):
                    protocol_models()
                has_more = val.get("has_more")
                if has_more is not None and not isinstance(has_more, bool):
                    protocol_models()
                raw_last = val.get("last_id")
                if has_more:
                    if not isinstance(raw_last, str) or not raw_last.strip():
                        protocol_models("Malformed pagination: last_id required when has_more true.")
                elif raw_last is not None and not isinstance(raw_last, str):
                    protocol_models("Malformed last_id.")
                # normalize page
                for entry in val["data"]:
                    if not isinstance(entry, dict) or not isinstance(entry.get("id"), str) or not entry["id"].strip():
                        protocol_models()
                    pid = entry["id"]
                    pm: Dict[str, Any] = {}
                    display = None
                    for k, d in entry.items():
                        if k in ("id", "display_name"):
                            continue
                        try:
                            json.dumps(d)
                        except Exception:
                            protocol_models()
                        if d is not None:
                            pm[k] = d
                    if isinstance(entry.get("display_name"), str) and entry["display_name"].strip():
                        display = entry["display_name"]
                    info = ModelInfo(id=self._redact(pid), name=self._redact(display) if display else None, provider_metadata=pm if pm else None)
                    all_models.append(info)
                if not has_more:
                    return all_models
                if len(val["data"]) == 0:
                    protocol_models("Malformed pagination: has_more true with empty data.")
                nxt = raw_last
                if not nxt or nxt == cursor:
                    protocol_models("Malformed pagination: cursor did not advance.")
                if nxt in seen:
                    protocol_models("Malformed pagination: cursor cycle.")
                cursor = nxt
            protocol_models("Too many pagination pages.")
        elif self._driver == "gemini":
            all_models = []
            page_token: Optional[str] = None
            seen_tokens: set = set()
            for _ in range(100):
                page_url = self._list_url if page_token is None else f"{self._list_url}?pageToken={urllib.parse.quote(page_token, safe='')}"
                if page_token is not None and page_token in seen_tokens:
                    protocol_models("Malformed pagination token.")
                if page_token is not None:
                    seen_tokens.add(page_token)
                status, resp_headers, body_bytes = _do_http("GET", page_url, self._headers, None, timeout, self._redact)
                req_id = resp_headers.get("x-request-id") or resp_headers.get("request-id")
                req_id = self._redact(req_id) if req_id else None
                if status < 200 or status >= 300:
                    raise _gemini_error(status, body_bytes.decode("utf-8", errors="replace"), req_id, self._redact)
                try:
                    val = json.loads(body_bytes.decode("utf-8"))
                except Exception:
                    protocol_models("Provider returned invalid JSON.")
                if not isinstance(val, dict) or not isinstance(val.get("models"), list):
                    protocol_models()
                nxt = val.get("nextPageToken")
                if nxt is not None and not isinstance(nxt, str):
                    protocol_models("Malformed nextPageToken.")
                for entry in val["models"]:
                    if not isinstance(entry, dict) or not isinstance(entry.get("name"), str) or not entry["name"].strip():
                        protocol_models()
                    raw_name = entry["name"]
                    mid = raw_name[len("models/") :] if raw_name.startswith("models/") else raw_name
                    if not mid.strip():
                        protocol_models()
                    pm = {}
                    for k, d in entry.items():
                        if k in ("name", "displayName"):
                            continue
                        try:
                            json.dumps(d)
                        except Exception:
                            protocol_models()
                        if d is not None:
                            pm[k] = d
                    if raw_name != mid:
                        pm["name"] = raw_name
                    info = ModelInfo(id=self._redact(mid), name=self._redact(entry["displayName"]) if isinstance(entry.get("displayName"), str) and entry["displayName"].strip() else None, provider_metadata=pm if pm else None)
                    all_models.append(info)
                if not nxt:
                    return all_models
                if nxt == page_token:
                    protocol_models("Malformed pagination: token did not advance.")
                if nxt in seen_tokens:
                    protocol_models("Malformed pagination: token cycle.")
                page_token = nxt
            protocol_models("Too many pagination pages.")
        else:
            status, resp_headers, body_bytes = _do_http("GET", self._list_url, self._headers, None, timeout, self._redact)
            req_id = resp_headers.get("x-request-id") or resp_headers.get("request-id")
            req_id = self._redact(req_id) if req_id else None
            if status < 200 or status >= 300:
                if self._driver == "ollama":
                    raise _ollama_error(status, body_bytes.decode("utf-8", errors="replace"), req_id, self._redact)
                raise _http_error_openai(status, body_bytes.decode("utf-8", errors="replace"), req_id, self._redact)
            try:
                val = json.loads(body_bytes.decode("utf-8"))
            except Exception:
                raise ConduitError("ProtocolError", "Provider returned invalid JSON.")
            # normalize
            if self._driver == "ollama":
                if not isinstance(val, dict) or not isinstance(val.get("models"), list):
                    protocol_models()
                infos: List[ModelInfo] = []
                for entry in val["models"]:
                    if not isinstance(entry, dict):
                        protocol_models()
                    pid = entry.get("name") if isinstance(entry.get("name"), str) else entry.get("model") if isinstance(entry.get("model"), str) else None
                    if not isinstance(pid, str) or not pid.strip():
                        protocol_models()
                    pm: Dict[str, Any] = {}
                    for k, d in entry.items():
                        if k in ("name", "model"):
                            continue
                        try:
                            json.dumps(d)
                        except Exception:
                            protocol_models()
                        if d is not None:
                            pm[k] = d
                    if isinstance(entry.get("model"), str) and entry["model"] != pid:
                        pm["model"] = entry["model"]
                    infos.append(ModelInfo(id=self._redact(pid), name=self._redact(entry.get("name")) if isinstance(entry.get("name"), str) and entry["name"].strip() else None, provider_metadata=pm if pm else None))
                return infos
            else:  # openai-compatible
                if not isinstance(val, dict) or not isinstance(val.get("data"), list):
                    protocol_models()
                infos = []
                for entry in val["data"]:
                    if not isinstance(entry, dict) or not isinstance(entry.get("id"), str) or not entry["id"].strip():
                        protocol_models()
                    pid = entry["id"]
                    pm = {}
                    for k, d in entry.items():
                        if k == "id":
                            continue
                        try:
                            json.dumps(d)
                        except Exception:
                            protocol_models()
                        if d is not None:
                            pm[k] = d
                    infos.append(ModelInfo(id=self._redact(pid), provider_metadata=pm if pm else None))
                return infos
        # unreachable
        return []

    def _generate(self, model_id: str, request: Dict[str, Any], streaming: bool) -> GenerationResponse:
        if _is_aborted(request.get("signal") if isinstance(request, dict) else None):
            raise ConduitError("CancelledError", "Request cancelled by caller.")
        # validate timeout on request
        _timeout_value(request.get("timeout") if isinstance(request, dict) else None)
        body = _encode_request(model_id, request, streaming, self._driver)
        timeout = request.get("timeout") if isinstance(request, dict) and request.get("timeout") is not None else self._default_timeout
        # gemini url per model
        if self._driver == "gemini":
            fetch_url = f"{self._url}/{urllib.parse.quote(model_id, safe='')}:generateContent"
        else:
            fetch_url = self._url
        headers = dict(self._headers)
        # ensure content-type
        if "content-type" not in {k.lower() for k in headers}:
            headers["content-type"] = "application/json"
        status, resp_headers, body_bytes = _do_http("POST", fetch_url, headers, body, timeout, self._redact)
        req_id = resp_headers.get("x-request-id") or resp_headers.get("request-id")
        req_id = self._redact(req_id) if req_id else None
        if status < 200 or status >= 300:
            text = body_bytes.decode("utf-8", errors="replace")
            if self._driver == "ollama":
                raise _ollama_error(status, text, req_id, self._redact, model_id)
            elif self._driver == "anthropic":
                raise _anthropic_error(status, text, req_id, self._redact)
            elif self._driver == "gemini":
                raise _gemini_error(status, text, req_id, self._redact)
            else:
                raise _http_error_openai(status, text, req_id, self._redact)
        try:
            val = json.loads(body_bytes.decode("utf-8"))
        except Exception:
            raise ConduitError("ProtocolError", "Provider returned invalid JSON.")
        if self._driver == "ollama":
            return _ollama_response(val, req_id, self._redact)
        elif self._driver == "anthropic":
            return _anthropic_response(val, req_id, self._redact)
        elif self._driver == "gemini":
            return _gemini_response(val, req_id, self._redact)
        else:
            return _decode_openai(val, req_id, self._redact)

    def _stream(self, model_id: str, request: Dict[str, Any]) -> Iterator[Dict[str, Any]]:
        if _is_aborted(request.get("signal") if isinstance(request, dict) else None):
            raise ConduitError("CancelledError", "Request cancelled by caller.")
        _timeout_value(request.get("timeout") if isinstance(request, dict) else None)
        # Return a lazy generator so first next() triggers connection and yields incrementally
        def _gen() -> Iterator[Dict[str, Any]]:
            # check abort at iteration start as well
            if _is_aborted(request.get("signal") if isinstance(request, dict) else None):
                raise ConduitError("CancelledError", "Request cancelled by caller.")
            body = _encode_request(model_id, request, True, self._driver)
            timeout = request.get("timeout") if isinstance(request, dict) and request.get("timeout") is not None else self._default_timeout
            if self._driver == "gemini":
                fetch_url = f"{self._url}/{urllib.parse.quote(model_id, safe='')}:streamGenerateContent?alt=sse"
            else:
                fetch_url = self._url
            headers = dict(self._headers)
            # incremental HTTP — same deadline semantics as _do_http_stream but yields events as they arrive
            parsed = urllib.parse.urlparse(fetch_url)
            host = parsed.hostname or ""
            port = parsed.port
            is_https = parsed.scheme == "https"
            path = parsed.path or "/"
            if parsed.query:
                path += "?" + parsed.query
            deadline: Optional[float] = None
            if timeout is not None:
                deadline = time.monotonic() + timeout / 1000.0
            def _remaining() -> Optional[float]:
                if deadline is None:
                    return None
                rem = deadline - time.monotonic()
                return max(0.05, rem) if rem > 0 else 0.05
            conn: http.client.HTTPConnection = http.client.HTTPSConnection(host, port, timeout=_remaining()) if is_https else http.client.HTTPConnection(host, port, timeout=_remaining())  # type: ignore[assignment]
            timed_out = threading.Event()
            timer: Optional[threading.Timer] = None
            def _on_timeout() -> None:
                timed_out.set()
                try: conn.close()
                except Exception: pass
                try:
                    sock = getattr(conn, "sock", None)
                    if sock is not None:
                        try: sock.shutdown(2)  # type: ignore[attr-defined]
                        except Exception: pass
                except Exception: pass
            if deadline is not None:
                delay = max(0.001, deadline - time.monotonic())
                timer = threading.Timer(delay, _on_timeout)
                timer.daemon = True
                timer.start()
            try:
                if deadline is not None and time.monotonic() >= deadline:
                    raise ConduitError("TimeoutError", "Request deadline exceeded.")
                if _is_aborted(request.get("signal") if isinstance(request, dict) else None):
                    raise ConduitError("CancelledError", "Request cancelled by caller.")
                conn.request("POST", path, body=body.encode("utf-8") if body is not None else None, headers=headers)
                resp = conn.getresponse()
                status = resp.status
                resp_headers = {k.lower(): v for k, v in resp.getheaders()}
                req_id = resp_headers.get("x-request-id") or resp_headers.get("request-id")
                req_id = self._redact(req_id) if req_id else None
                if status < 200 or status >= 300:
                    # read error body with deadline
                    err_bytes = resp.read()
                    text = err_bytes.decode("utf-8", errors="replace")
                    if self._driver == "ollama":
                        raise _ollama_error(status, text, req_id, self._redact, model_id)
                    elif self._driver == "anthropic":
                        raise _anthropic_error(status, text, req_id, self._redact)
                    elif self._driver == "gemini":
                        raise _gemini_error(status, text, req_id, self._redact)
                    else:
                        raise _http_error_openai(status, text, req_id, self._redact)
                ctype = (resp_headers.get("content-type") or "").split(";")[0].strip().lower()
                # incremental Ollama NDJSON — yields as bytes arrive
                if self._driver == "ollama":
                    if ctype not in ("application/x-ndjson", "application/ndjson", "application/json"):
                        raise ConduitError("ProtocolError", "Expected an NDJSON response.")
                    def _byte_chunks():
                        while True:
                            if _is_aborted(request.get("signal") if isinstance(request, dict) else None):
                                raise ConduitError("CancelledError", "Request cancelled by caller.")
                            if timed_out.is_set():
                                raise ConduitError("TimeoutError", "Request deadline exceeded.")
                            if deadline is not None and time.monotonic() >= deadline:
                                _on_timeout()
                                raise ConduitError("TimeoutError", "Request deadline exceeded.")
                            if deadline is not None:
                                rem = deadline - time.monotonic()
                                if rem <= 0:
                                    raise ConduitError("TimeoutError", "Request deadline exceeded.")
                                try:
                                    sock = getattr(conn, "sock", None)
                                    if sock is not None:
                                        sock.settimeout(rem)  # type: ignore[union-attr]
                                except Exception:
                                    pass
                            try:
                                chunk = resp.read(4096)
                            except (TimeoutError, socket.timeout) as e:
                                if deadline is not None:
                                    raise ConduitError("TimeoutError", "Request deadline exceeded.") from e
                                raise ConduitError("ConnectionError", "Provider connection failed.", cause={"name": type(e).__name__, "message": "[REDACTED]"}) from e
                            except OSError as e:
                                if timed_out.is_set():
                                    raise ConduitError("TimeoutError", "Request deadline exceeded.") from e
                                if isinstance(e, TimeoutError) or "timed out" in str(e).lower():
                                    if deadline is not None:
                                        raise ConduitError("TimeoutError", "Request deadline exceeded.") from e
                                raise ConduitError("ConnectionError", "Provider connection failed.", cause={"name": type(e).__name__, "message": "[REDACTED]"}) from e
                            if not chunk:
                                break
                            if timed_out.is_set():
                                raise ConduitError("TimeoutError", "Request deadline exceeded.")
                            if _is_aborted(request.get("signal") if isinstance(request, dict) else None):
                                raise ConduitError("CancelledError", "Request cancelled by caller.")
                            yield chunk
                    # Ollama uses incremental NDJSON parser — first event before EOF
                    yield from _parse_ollama_stream_incremental(_byte_chunks(), req_id, self._redact, model_id)
                    return
                else:
                    expected = "text/event-stream"
                    if ctype != expected:
                        if self._driver in ("openai-compatible", "anthropic", "gemini") and ctype != expected:
                            raise ConduitError("ProtocolError", "Expected a text/event-stream response.")
                    if self._driver == "openai-compatible":
                        def _byte_chunks_oa():
                            while True:
                                if _is_aborted(request.get("signal") if isinstance(request, dict) else None):
                                    raise ConduitError("CancelledError", "Request cancelled by caller.")
                                if timed_out.is_set():
                                    raise ConduitError("TimeoutError", "Request deadline exceeded.")
                                if deadline is not None and time.monotonic() >= deadline:
                                    _on_timeout()
                                    raise ConduitError("TimeoutError", "Request deadline exceeded.")
                                if deadline is not None:
                                    rem = deadline - time.monotonic()
                                    if rem <= 0:
                                        raise ConduitError("TimeoutError", "Request deadline exceeded.")
                                    try:
                                        sock = getattr(conn, "sock", None)
                                        if sock is not None:
                                            sock.settimeout(rem)  # type: ignore[union-attr]
                                    except Exception:
                                        pass
                                try:
                                    chunk = resp.read(4096)
                                except (TimeoutError, socket.timeout) as e:
                                    if deadline is not None:
                                        raise ConduitError("TimeoutError", "Request deadline exceeded.") from e
                                    raise ConduitError("ConnectionError", "Provider connection failed.", cause={"name": type(e).__name__, "message": "[REDACTED]"}) from e
                                except OSError as e:
                                    if timed_out.is_set():
                                        raise ConduitError("TimeoutError", "Request deadline exceeded.") from e
                                    if isinstance(e, TimeoutError) or "timed out" in str(e).lower():
                                        if deadline is not None:
                                            raise ConduitError("TimeoutError", "Request deadline exceeded.") from e
                                    raise ConduitError("ConnectionError", "Provider connection failed.", cause={"name": type(e).__name__, "message": "[REDACTED]"}) from e
                                if not chunk:
                                    break
                                if timed_out.is_set():
                                    raise ConduitError("TimeoutError", "Request deadline exceeded.")
                                if _is_aborted(request.get("signal") if isinstance(request, dict) else None):
                                    raise ConduitError("CancelledError", "Request cancelled by caller.")
                                yield chunk
                        # NOTE: incremental SSE — first event yielded before EOF
                        yield from _parse_openai_stream_incremental(_byte_chunks_oa(), req_id, self._redact)
                        return
                    elif self._driver == "anthropic":
                        def _byte_chunks_a():
                            while True:
                                if _is_aborted(request.get("signal") if isinstance(request, dict) else None):
                                    raise ConduitError("CancelledError", "Request cancelled by caller.")
                                if timed_out.is_set():
                                    raise ConduitError("TimeoutError", "Request deadline exceeded.")
                                if deadline is not None and time.monotonic() >= deadline:
                                    _on_timeout()
                                    raise ConduitError("TimeoutError", "Request deadline exceeded.")
                                if deadline is not None:
                                    rem = deadline - time.monotonic()
                                    if rem <= 0:
                                        raise ConduitError("TimeoutError", "Request deadline exceeded.")
                                    try:
                                        sock = getattr(conn, "sock", None)
                                        if sock is not None:
                                            sock.settimeout(rem)  # type: ignore[union-attr]
                                    except Exception:
                                        pass
                                try:
                                    chunk = resp.read(4096)
                                except (TimeoutError, socket.timeout) as e:
                                    if deadline is not None:
                                        raise ConduitError("TimeoutError", "Request deadline exceeded.") from e
                                    raise ConduitError("ConnectionError", "Provider connection failed.", cause={"name": type(e).__name__, "message": "[REDACTED]"}) from e
                                except OSError as e:
                                    if timed_out.is_set():
                                        raise ConduitError("TimeoutError", "Request deadline exceeded.") from e
                                    if isinstance(e, TimeoutError) or "timed out" in str(e).lower():
                                        if deadline is not None:
                                            raise ConduitError("TimeoutError", "Request deadline exceeded.") from e
                                    raise ConduitError("ConnectionError", "Provider connection failed.", cause={"name": type(e).__name__, "message": "[REDACTED]"}) from e
                                if not chunk:
                                    break
                                if timed_out.is_set():
                                    raise ConduitError("TimeoutError", "Request deadline exceeded.")
                                if _is_aborted(request.get("signal") if isinstance(request, dict) else None):
                                    raise ConduitError("CancelledError", "Request cancelled by caller.")
                                yield chunk
                        yield from _parse_anthropic_stream_incremental(_byte_chunks_a(), req_id, self._redact)
                        return
                    elif self._driver == "gem500" or self._driver == "gemini":
                        def _byte_chunks_g():
                            while True:
                                if _is_aborted(request.get("signal") if isinstance(request, dict) else None):
                                    raise ConduitError("CancelledError", "Request cancelled by caller.")
                                if timed_out.is_set():
                                    raise ConduitError("TimeoutError", "Request deadline exceeded.")
                                if deadline is not None and time.monotonic() >= deadline:
                                    _on_timeout()
                                    raise ConduitError("TimeoutError", "Request deadline exceeded.")
                                if deadline is not None:
                                    rem = deadline - time.monotonic()
                                    if rem <= 0:
                                        raise ConduitError("TimeoutError", "Request deadline exceeded.")
                                    try:
                                        sock = getattr(conn, "sock", None)
                                        if sock is not None:
                                            sock.settimeout(rem)  # type: ignore[union-attr]
                                    except Exception:
                                        pass
                                try:
                                    chunk = resp.read(4096)
                                except (TimeoutError, socket.timeout) as e:
                                    if deadline is not None:
                                        raise ConduitError("TimeoutError", "Request deadline exceeded.") from e
                                    raise ConduitError("ConnectionError", "Provider connection failed.", cause={"name": type(e).__name__, "message": "[REDACTED]"}) from e
                                except OSError as e:
                                    if timed_out.is_set():
                                        raise ConduitError("TimeoutError", "Request deadline exceeded.") from e
                                    if isinstance(e, TimeoutError) or "timed out" in str(e).lower():
                                        if deadline is not None:
                                            raise ConduitError("TimeoutError", "Request deadline exceeded.") from e
                                    raise ConduitError("ConnectionError", "Provider connection failed.", cause={"name": type(e).__name__, "message": "[REDACTED]"}) from e
                                if not chunk:
                                    break
                                if timed_out.is_set():
                                    raise ConduitError("TimeoutError", "Request deadline exceeded.")
                                if _is_aborted(request.get("signal") if isinstance(request, dict) else None):
                                    raise ConduitError("CancelledError", "Request cancelled by caller.")
                                yield chunk
                        yield from _parse_gemini_stream_incremental(_byte_chunks_g(), req_id, self._redact)
                        return
            except GeneratorExit:
                raise
            except ConduitError:
                raise
            except (TimeoutError, socket.timeout) as e:
                if deadline is not None:
                    raise ConduitError("TimeoutError", "Request deadline exceeded.") from e
                raise ConduitError("ConnectionError", "Provider connection failed.", cause={"name": type(e).__name__, "message": "[REDACTED]"}) from e
            except OSError as e:
                if timed_out.is_set():
                    raise ConduitError("TimeoutError", "Request deadline exceeded.") from e
                if isinstance(e, TimeoutError) or "timed out" in str(e).lower():
                    if deadline is not None:
                        raise ConduitError("TimeoutError", "Request deadline exceeded.") from e
                raise ConduitError("ConnectionError", "Provider connection failed.", cause={"name": type(e).__name__, "message": "[REDACTED]"}) from e
            except Exception as e:
                if isinstance(e, ConduitError):
                    raise
                if timed_out.is_set():
                    raise ConduitError("TimeoutError", "Request deadline exceeded.") from e
                if isinstance(e, TimeoutError) or "timed out" in str(type(e).__name__).lower():
                    if deadline is not None:
                        raise ConduitError("TimeoutError", "Request deadline exceeded.") from e
                raise ConduitError("ConnectionError", "Provider connection failed.", cause={"name": type(e).__name__, "message": "[REDACTED]"}) from e
            finally:
                if timer is not None:
                    timer.cancel()
                try:
                    conn.close()
                except Exception:
                    pass
        return _gen()


# ---------------------------------------------------------------------------
# connect()
# ---------------------------------------------------------------------------

def connect(config: Optional[Dict[str, Any]] = None, **kwargs: Any) -> Any:
    """Create a Client (or Model if ``model`` is supplied).

    ``config`` keys: driver, endpoint, credentials?, headers?, timeout?, model?
    Accepts either a dict positional arg or keyword arguments (or both, with kwargs winning).
    """
    if config is None:
        config = dict(kwargs) if kwargs else {}
    elif isinstance(config, dict):
        if kwargs:
            config = {**config, **kwargs}
    else:
        raise ConduitError("InvalidRequestError", "Client configuration is required.")
    if not isinstance(config, dict):
        raise ConduitError("InvalidRequestError", "Client configuration is required.")
    for k in config.keys():
        if k not in ("driver", "endpoint", "credentials", "headers", "timeout", "model"):
            raise ConduitError("InvalidRequestError", "Unrecognized field; use providerOptions for native request settings.")
    driver = config.get("driver")
    if driver not in ("openai-compatible", "ollama", "anthropic", "gemini"):
        raise ConduitError("InvalidRequestError", "Unknown driver.")
    endpoint_str = config.get("endpoint")
    if endpoint_str is None:
        defaults = {"ollama": "http://localhost:11434", "anthropic": "https://api.anthropic.com", "gemini": "https://generativelanguage.googleapis.com"}
        if driver == "openai-compatible":
            raise ConduitError("InvalidRequestError", "endpoint must be an HTTP(S) API base URL.")
        endpoint_str = defaults[driver]
        config["endpoint"] = endpoint_str
    if not isinstance(endpoint_str, str):
        raise ConduitError("InvalidRequestError", "endpoint must be an HTTP(S) API base URL.")
    try:
        endpoint = urllib.parse.urlparse(endpoint_str)
    except Exception:
        raise ConduitError("InvalidRequestError", "endpoint must be an HTTP(S) API base URL.")
    if endpoint.scheme not in ("http", "https") or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment or "?" in endpoint_str or "#" in endpoint_str:
        raise ConduitError("InvalidRequestError", "endpoint must be an HTTP(S) API base URL without userinfo, query, or fragment.")
    # Validate netloc exists
    if not endpoint.hostname:
        raise ConduitError("InvalidRequestError", "endpoint must be an HTTP(S) API base URL.")
    base_path = endpoint.path.rstrip("/")  # keep without trailing slash
    # Build URLs
    if driver == "gemini":
        # endpoint.pathname = basePath + "/v1beta/models"
        # Use url building via string to preserve behavior of TS (endpoint.href without trailing slash)
        base = f"{endpoint.scheme}://{endpoint.netloc}{base_path}/v1beta/models"
        base = base.rstrip("/")
        url = base
        list_url = base
    else:
        suffix = "/api/chat" if driver == "ollama" else ("/v1/messages" if driver == "anthropic" else "/chat/completions")
        url = f"{endpoint.scheme}://{endpoint.netloc}{base_path}{suffix}"
        # Normalize: ensure no double slash
        list_suffix = "/api/tags" if driver == "ollama" else ("/v1/models" if driver == "anthropic" else "/models")
        list_url = f"{endpoint.scheme}://{endpoint.netloc}{base_path}{list_suffix}"
    cred = config.get("credentials")
    if cred is not None and (not isinstance(cred, str) or not re.match(r"^[A-Za-z0-9._~+/\-]+=*$", cred)):
        raise ConduitError("InvalidRequestError", "credentials must be a nonempty Bearer token")
    _timeout_value(config.get("timeout"))
    default_timeout = config.get("timeout")
    headers: Dict[str, str] = {"content-type": "application/json"}
    secrets: List[str] = []
    if cred:
        secrets.append(cred)
    hdrs = config.get("headers")
    if hdrs is not None:
        if not isinstance(hdrs, dict):
            raise ConduitError("InvalidRequestError", "headers must be a string-valued object.")
        for k, v in hdrs.items():
            if not isinstance(k, str) or k.lower() in _protected_headers:
                raise ConduitError("InvalidRequestError", "Custom header conflicts with a protected header.")
            if not isinstance(v, str):
                raise ConduitError("InvalidRequestError", "Custom header values must be strings.")
            # validate header name/value via http.client? Use simple check
            try:
                # http.client will validate on send; we do minimal
                k.encode("ascii")
                v.encode("ascii")
            except Exception:
                raise ConduitError("InvalidRequestError", "Invalid custom HTTP header.")
            headers[k.lower()] = v  # store lower for http.client; will be sent as-is lower (acceptable)
            # also keep original case for redaction
            if v:
                secrets.append(v)
    # driver-specific auth headers
    if driver == "anthropic":
        if cred:
            headers["x-api-key"] = cred
        headers["anthropic-version"] = "2023-06-01"
    elif driver == "gemini":
        if cred:
            headers["x-goog-api-key"] = cred
    elif cred:
        headers["authorization"] = f"Bearer {cred}"
    redact = _make_redactor(secrets)
    client = Client(driver=driver, endpoint=endpoint_str, url=url, list_url=list_url, headers=headers, redact=redact, default_timeout=default_timeout)
    model_id = config.get("model")
    if model_id is not None:
        if not isinstance(model_id, str) or not model_id.strip():
            raise ConduitError("InvalidRequestError", "model must be a nonempty string.")
        return client.model(model_id)
    return client


__all__ = ["connect", "Client", "Model", "ConduitError", "GenerationResponse", "ModelInfo"]

