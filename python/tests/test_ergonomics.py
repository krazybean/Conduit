import unittest
from conduit import connect

class TestErgonomics(unittest.TestCase):
    def test_generate_string_shorthand(self):
        from http.server import HTTPServer, BaseHTTPRequestHandler
        import json, threading

        captures = []
        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("content-length", 0))
                body = self.rfile.read(length)
                v = json.loads(body)
                captures.append(v)
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"id": "id", "object": "chat.completion", "created": 1, "model": "m", "choices": [{"index": 0, "message": {"role": "assistant", "content": "Hi"}, "finish_reason": "stop"}]}).encode())
            def log_message(self, *a, **k): pass

        s = HTTPServer(("127.0.0.1", 0), H)
        t = threading.Thread(target=s.serve_forever, daemon=True)
        t.start()
        _, port = s.server_address
        client = connect(driver="openai-compatible", endpoint=f"http://127.0.0.1:{port}/v1", model="m")
        res = client.generate("Hello")
        self.assertEqual(res.text, "Hi")
        self.assertEqual(res["text"], "Hi")
        self.assertEqual(res.get("text"), "Hi")
        self.assertEqual(res.get("usage"), None)
        # differential: shorthand vs explicit old-form must be wire-identical
        client.generate(messages=[{"role": "user", "content": "Hello"}])
        self.assertEqual(captures[0], captures[1])
        s.shutdown()

    def test_stream_string_shorthand(self):
        from http.server import HTTPServer, BaseHTTPRequestHandler
        import json, threading
        captures = []
        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("content-length", 0))
                body = self.rfile.read(length)
                captures.append(json.loads(body))
                self.send_response(200)
                self.send_header("content-type", "text/event-stream")
                self.end_headers()
                self.wfile.write(f"data: {json.dumps({'choices': [{'index': 0, 'delta': {'content': 'Hi'}, 'finish_reason': None}]})}\n\n".encode())
                self.wfile.write(f"data: {json.dumps({'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]})}\n\n".encode())
                self.wfile.write(b"data: [DONE]\n\n")
            def log_message(self, *a, **k): pass
        s = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=s.serve_forever, daemon=True).start()
        _, port = s.server_address
        client = connect(driver="openai-compatible", endpoint=f"http://127.0.0.1:{port}/v1")
        m = client.model("m")
        list(m.stream("Hello"))
        list(m.stream(messages=[{"role": "user", "content": "Hello"}]))
        self.assertEqual(captures[0], captures[1])
        s.shutdown()

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
        from http.server import HTTPServer, BaseHTTPRequestHandler
        import json, threading
        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"id": "id", "object": "chat.completion", "created": 1, "model": "m", "choices": [{"index": 0, "message": {"role": "assistant", "content": "Hi"}, "finish_reason": "stop"}]}).encode())
            def log_message(self, *a, **k): pass
        s = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=s.serve_forever, daemon=True).start()
        _, port = s.server_address
        client = connect(driver="openai-compatible", endpoint=f"http://127.0.0.1:{port}/v1")
        model = client.model("m")
        res = model.generate(messages=[{"role": "user", "content": "Hello"}])
        self.assertEqual(res.text, "Hi")
        s.shutdown()

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
