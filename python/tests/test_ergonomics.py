import contextlib
import http.server
import json
import threading
import unittest

from conduit import connect
from helpers import MockHandler


@contextlib.contextmanager
def _ergonomics_server(handle_fn):
    """Tiny local helper: deterministic shutdown + server_close + thread join."""
    requests = []
    MockHandler.requests = requests
    MockHandler.handle_fn = handle_fn
    srv = http.server.HTTPServer(("127.0.0.1", 0), MockHandler)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield (f"http://127.0.0.1:{port}/v1", requests, srv)
    finally:
        try:
            srv.shutdown()
        finally:
            srv.server_close()
        t.join(timeout=2)


class TestErgonomics(unittest.TestCase):
    def test_generate_string_shorthand(self):
        captures = []

        def handle(handler, req):
            captures.append(req["body"])
            handler.send_response(200)
            handler.send_header("content-type", "application/json")
            handler.end_headers()
            handler.wfile.write(json.dumps({"id": "id", "object": "chat.completion", "created": 1, "model": "m", "choices": [{"index": 0, "message": {"role": "assistant", "content": "Hi"}, "finish_reason": "stop"}]}).encode())

        with _ergonomics_server(handle) as (endpoint, _requests, _srv):
            client = connect(driver="openai-compatible", endpoint=endpoint, model="m")
            res = client.generate("Hello")
            self.assertEqual(res.text, "Hi")
            self.assertEqual(res["text"], "Hi")
            self.assertEqual(res.get("text"), "Hi")
            self.assertEqual(res.get("usage"), None)
            # differential: shorthand vs explicit old-form must be wire-identical
            client.generate(messages=[{"role": "user", "content": "Hello"}])
            self.assertEqual(captures[0], captures[1])

    def test_stream_string_shorthand(self):
        captures = []

        def handle(handler, req):
            captures.append(req["body"])
            handler.send_response(200)
            handler.send_header("content-type", "text/event-stream")
            handler.end_headers()
            handler.wfile.write(f"data: {json.dumps({'choices': [{'index': 0, 'delta': {'content': 'Hi'}, 'finish_reason': None}]})}\n\n".encode())
            handler.wfile.write(f"data: {json.dumps({'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]})}\n\n".encode())
            handler.wfile.write(b"data: [DONE]\n\n")

        with _ergonomics_server(handle) as (endpoint, _requests, _srv):
            client = connect(driver="openai-compatible", endpoint=endpoint)
            m = client.model("m")
            list(m.stream("Hello"))
            list(m.stream(messages=[{"role": "user", "content": "Hello"}]))
            self.assertEqual(captures[0], captures[1])

    def test_ollama_default_endpoint(self):
        m = connect(driver="ollama", model="qwen3:8b")
        self.assertIsNotNone(m)
        m2 = connect(driver="ollama", endpoint="http://example.com:11434", model="qwen3:8b")
        self.assertIsNotNone(m2)

    def test_openai_requires_endpoint(self):
        with self.assertRaises(Exception) as cm:
            connect(driver="openai-compatible", model="x")
        self.assertIn("endpoint", str(cm.exception))

    def test_old_request_still_works(self):
        def handle(handler, _req):
            handler.send_response(200)
            handler.send_header("content-type", "application/json")
            handler.end_headers()
            handler.wfile.write(json.dumps({"id": "id", "object": "chat.completion", "created": 1, "model": "m", "choices": [{"index": 0, "message": {"role": "assistant", "content": "Hi"}, "finish_reason": "stop"}]}).encode())

        with _ergonomics_server(handle) as (endpoint, _requests, _srv):
            client = connect(driver="openai-compatible", endpoint=endpoint)
            model = client.model("m")
            res = model.generate(messages=[{"role": "user", "content": "Hello"}])
            self.assertEqual(res.text, "Hi")

    def test_response_mapping_compat(self):
        from conduit import GenerationResponse
        r = GenerationResponse(id="id", model="m", content=[{"type": "text", "text": "Hi"}], finish_reason="stop", usage={"input_tokens": 1})
        self.assertEqual(r.text, "Hi")
        self.assertEqual(r["text"], "Hi")
        self.assertEqual(r.get("text"), "Hi")
        self.assertEqual(r["usage"], {"input_tokens": 1})
        self.assertIn("text", r)
        self.assertEqual(list(r.keys())[:2], ["id", "model"])

if __name__ == "__main__":
    unittest.main()
