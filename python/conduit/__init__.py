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

from .errors import ConduitError, ErrorCode, ERROR_CODES, http_failure
from .types import GenerationResponse, ModelInfo, text_response
from .providers import ProviderAdapter, for_driver


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_owned_fields = {"model", "messages", "stream", "stream_options", "max_tokens", "max_completion_tokens", "temperature", "top_p", "stop", "tools", "tool_choice", "functions", "function_call", "response_format", "n", "modalities"}
_protected_headers = {"authorization", "proxy-authorization", "cookie", "host", "content-type", "content-length", "connection", "transfer-encoding", "upgrade", "trailer", "te", "keep-alive", "x-api-key", "anthropic-version", "x-goog-api-key"}
_request_fields = {"messages", "max_output_tokens", "temperature", "top_p", "stop", "tools", "tool_choice", "response_format", "provider_options", "signal", "timeout"}
_unsupported_fields = {"stream", "reasoning", "vision"}


def _parse_openai_stream_incremental(chunks, request_id, redact):
    """Internal test seam; Client dispatches through ProviderAdapter directly."""
    return for_driver("openai-compatible").parse_stream(chunks, request_id, redact)


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
    _validate_response_format(request.get("response_format"))

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
        try:
            _json_validate(provider_options)
            json.dumps(provider_options)
        except ConduitError:
            raise
        except Exception:
            _invalid("providerOptions must contain plain JSON data.")
    provider = for_driver(driver)
    provider.validate_request(request, streaming)
    return provider.encode_request(model, messages, request, streaming)


# ---------------------------------------------------------------------------
# Response decoders for other drivers (simplified but spec-compliant enough)
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
        self._provider: ProviderAdapter = for_driver(driver)
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
        all_models: List[ModelInfo] = []
        token: Optional[str] = None
        seen: set = set()
        for _ in range(100):
            if token is not None:
                if token in seen:
                    _protocol("Malformed pagination cursor.")
                seen.add(token)
            page_url = self._provider.model_page_url(self._list_url, token)
            status, resp_headers, body_bytes = _do_http("GET", page_url, self._headers, None, timeout, self._redact)
            req_id = resp_headers.get("x-request-id") or resp_headers.get("request-id")
            req_id = self._redact(req_id) if req_id else None
            if status < 200 or status >= 300:
                raise self._provider.http_error(status, body_bytes.decode("utf-8", errors="replace"), req_id, self._redact)
            try:
                value = json.loads(body_bytes.decode("utf-8"))
            except Exception:
                _protocol("Provider returned invalid JSON.")
            page_models, next_token, has_more = self._provider.model_page(value, self._redact)
            all_models.extend(page_models)
            if not has_more:
                return all_models
            if not next_token or next_token == token or next_token in seen:
                _protocol("Malformed pagination: cursor did not advance.")
            token = next_token
        _protocol("Too many pagination pages.")
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
            raise self._provider.http_error(status, text, req_id, self._redact, model_id)
        try:
            val = json.loads(body_bytes.decode("utf-8"))
        except Exception:
            raise ConduitError("ProtocolError", "Provider returned invalid JSON.")
        return self._provider.decode_response(val, req_id, self._redact)

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
                    raise self._provider.http_error(status, text, req_id, self._redact, model_id)
                ctype = (resp_headers.get("content-type") or "").split(";")[0].strip().lower()
                if ctype not in self._provider.stream_content_types:
                    raise ConduitError("ProtocolError", "Expected a provider stream response.")
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
                yield from self._provider.parse_stream(_byte_chunks(), req_id, self._redact, model_id)
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

