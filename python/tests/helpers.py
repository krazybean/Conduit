"""Conformance test helpers — stdlib only.

Mirrors typescript/test/http.mjs and truncation/redaction assertions.
Uses http.server for local mock endpoints and unittest.mock for http.client.
"""
import base64
import json
import http.server
import threading
import socket
import inspect
import contextlib
from unittest import mock


SECRET = "synthetic-bearer-47"
HEADER_SECRET = "synthetic-header-83"


def load_fixture(relative: str):
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[2]
    return json.loads((root / relative).read_text())


def assert_no_secrets(value, secrets=(SECRET, HEADER_SECRET)):
    """Assert no secret appears in string/inspect/repr of value."""
    for text in (json.dumps(value, default=str) if isinstance(value, (dict, list)) else str(value),
                 repr(value),
                 inspect.getsource(value) if inspect.isfunction(value) else "",
                 str(inspect.getmembers(value)) if hasattr(value, "__dict__") else ""):
        for s in secrets:
            assert s not in text, f"secret leaked in {text[:500]!r}"

    # Also check stringified error diagnostics deeply
    dumped = json.dumps(value, default=str) if isinstance(value, dict) else str(value)
    for s in secrets:
        assert s not in dumped


def fragments_to_bytes(fragments_b64):
    return [base64.b64decode(p) for p in fragments_b64]


def sse_data(obj):
    return f"data: {json.dumps(obj)}\n\n"

def sse_chunk(content, finish=None, extra=None):
    base = {"choices": [{"index": 0, "delta": {} if content is None else {"content": content}, "finish_reason": finish}]}
    if extra:
        base.update(extra)
    return base


class MockHandler(http.server.BaseHTTPRequestHandler):
    # class-level hook set per-server
    handle_fn = None
    requests = None

    def do_POST(self):
        self._capture_and_handle()

    def do_GET(self):
        self._capture_and_handle()

    def _capture_and_handle(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(length) if length else b""
        parsed = None
        if body:
            try:
                parsed = json.loads(body)
            except Exception:
                parsed = body.decode(errors="ignore")
        self.requests.append({
            "method": self.command,
            "path": self.path,
            "headers": {k.lower(): v for k, v in self.headers.items()},
            "body": parsed,
            "raw_body": body,
        })
        try:
            handle_fn = type(self).handle_fn
            handle_fn(self, self.requests[-1])
        except Exception:
            try:
                self.send_error(500)
            except Exception:
                pass
            try: self.close_connection = True
            except: pass

    def log_message(self, format, *args):
        pass


@contextlib.contextmanager
def local_server(handle_fn):
    """Yield (endpoint, requests) with a background HTTP server."""
    requests = []
    MockHandler.requests = requests
    MockHandler.handle_fn = handle_fn
    # find free port
    srv = http.server.HTTPServer(("127.0.0.1", 0), MockHandler)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield (f"http://127.0.0.1:{port}/v1", requests, srv)
    finally:
        srv.shutdown()
        srv.server_close()


def mock_http_client(responder):
    """Patch http.client.HTTPConnection to call responder(method, url, body, headers) -> (status, headers, body_bytes)."""
    orig_conn = __import__("http.client", fromlist=["HTTPConnection"]).HTTPConnection

    class FakeResponse:
        def __init__(self, status, headers, body):
            self.status = status
            self.headers = headers or {}
            self._body = body if isinstance(body, bytes) else (json.dumps(body).encode() if body is not None else b"")
            self._pos = 0
            self.closed = False
        def getheader(self, name, default=None):
            return self.headers.get(name.lower(), self.headers.get(name, default))
        def getheaders(self):
            return list(self.headers.items())
        def read(self, amt=None):
            if amt is None:
                data = self._body[self._pos:]
                self._pos = len(self._body)
                return data
            data = self._body[self._pos:self._pos+amt]
            self._pos += len(data)
            return data
        def close(self):
            self.closed = True

    class FakeConn:
        def __init__(self, host, port=None, timeout=None, **kw):
            self.host = host; self.port = port; self.timeout = timeout
            self._method = None; self._url = None; self._body = None; self._headers = {}
        def request(self, method, url, body=None, headers=None):
            self._method = method; self._url = url; self._body = body; self._headers = headers or {}
        def getresponse(self):
            body = self._body
            if isinstance(body, bytes):
                try: body = json.loads(body)
                except: pass
            else:
                body = self._body
            status, hdrs, resp_body = responder(self._method, self._url, body, self._headers)
            return FakeResponse(status, {k.lower(): v for k, v in (hdrs or {}).items()}, resp_body)
        def close(self): pass

    return mock.patch("http.client.HTTPConnection", FakeConn), mock.patch("http.client.HTTPSConnection", FakeConn)


# SSE incremental parser (mirrors typescript/src/openai-stream.ts dataEvents)
def parse_sse_fragments(fragments, join_data_lines=True):
    """Incrementally parse SSE byte fragments into data payloads — handles split UTF-8."""
    import codecs
    decoder = codecs.getincrementaldecoder("utf-8")(errors="strict")
    line = ""
    data = []
    results = []
    skip_lf = False
    for frag in fragments:
        try:
            text = decoder.decode(frag, final=False)
        except UnicodeDecodeError as e:
            # If incomplete sequence at fragment boundary, decoder buffers; but strict raises only on invalid bytes
            # Re-raise as AssertionError for invalid UTF-8 contract
            raise AssertionError(f"invalid utf-8 fragment: {e}")
        # Flush decoder if frag was empty? nothing
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
                if data:
                    results.append("\n".join(data) if join_data_lines else data)
                    data = []
            else:
                colon = line.find(":")
                field = line if colon < 0 else line[:colon]
                value = "" if colon < 0 else line[colon+1:]
                if value.startswith(" "):
                    value = value[1:]
                if field == "data":
                    data.append(value)
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
            if data:
                results.append("\n".join(data) if join_data_lines else data)
                data = []
        else:
            colon = line.find(":")
            field = line if colon < 0 else line[:colon]
            value = "" if colon < 0 else line[colon+1:]
            if value.startswith(" "):
                value = value[1:]
            if field == "data":
                data.append(value)
        line = ""
    # EOF does not dispatch incomplete event per SSE spec
    return results


def ndjson_records(fragments):
    """Incremental NDJSON reader: LF/CRLF, blank lines ignored, split JSON/utf-8."""
    buf = b""
    for frag in fragments:
        buf += frag
    # normalize CRLF
    text = buf.decode("utf-8")
    records = []
    for line in text.replace("\r\n", "\n").split("\n"):
        if not line.strip():
            continue
        records.append(json.loads(line))
    return records
