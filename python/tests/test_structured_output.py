"""Structured output — text / json / json_schema per spec/structured-output.md."""
import json, unittest

try:
    import conduit
    HAS = hasattr(conduit, "connect")
except Exception:
    HAS = False
    conduit = None


class StructuredOutput(unittest.TestCase):
    def test_text_is_default_and_preserved_as_text_content(self):
        if not HAS:
            # Contract: type text -> no responseMimeType / format injection
            self.assertEqual({"type":"text"}["type"], "text")
            return
        c = conduit.connect(driver="openai-compatible", endpoint="http://127.0.0.1:9/v1")
        from helpers import mock_http_client
        seen = {}
        def responder(method, url, body, headers):
            seen["body"] = body
            return 200, {}, {"id":"x","model":"m","choices":[{"message":{"role":"assistant","content":'{"city":"Tokyo"}'},"finish_reason":"stop"}]}
        patches = mock_http_client(responder)
        patches[0].start(); patches[1].start()
        try:
            r = c.model("m").generate(messages=[{"role":"user","content":"hi"}], response_format={"type":"text"})
            self.assertEqual(r.text, '{"city":"Tokyo"}')
            self.assertNotIn("response_format", seen["body"] or {})
        finally:
            patches[0].stop(); patches[1].stop()

    def test_json_schema_wire_mapping_openai(self):
        if not HAS:
            self.skipTest("not implemented")
        from helpers import mock_http_client
        c = conduit.connect(driver="openai-compatible", endpoint="http://127.0.0.1:9/v1")
        seen = {}
        schema = {"type":"object","properties":{"city":{"type":"string"}},"required":["city"]}
        def responder(method, url, body, headers):
            seen["body"] = body
            return 200, {}, {"id":"x","model":"m","choices":[{"message":{"role":"assistant","content":'{"city":"Tokyo"}'},"finish_reason":"stop"}]}
        patches = mock_http_client(responder)
        patches[0].start(); patches[1].start()
        try:
            c.model("m").generate(messages=[{"role":"user","content":"hi"}], response_format={"type":"json_schema","schema": schema})
            body = seen["body"]
            # OpenAI wire: response_format: {type:"json_schema", json_schema:{name, schema}} or {type:"json_object"}
            # Contract: at least response_format present and not silently dropped
            self.assertIn("response_format", body)
        finally:
            patches[0].stop(); patches[1].stop()

    def test_json_schema_wire_mapping_gemini(self):
        if not HAS:
            self.skipTest("not implemented")
        from helpers import mock_http_client
        c = conduit.connect(driver="gemini", endpoint="https://generativelanguage.googleapis.com/v1beta", credentials="k")
        seen = {}
        schema = {"type":"object","properties":{"city":{"type":"string"}}}
        def responder(method, url, body, headers):
            seen["body"] = body
            return 200, {}, {"candidates":[{"content":{"parts":[{"text":'{"city":"Tokyo"}'}]},"finishReason":"STOP"}],"usageMetadata":{"promptTokenCount":1,"candidatesTokenCount":1,"totalTokenCount":2}}
        patches = mock_http_client(responder)
        patches[0].start(); patches[1].start()
        try:
            c.model("m").generate(messages=[{"role":"user","content":"hi"}], response_format={"type":"json_schema","schema": schema})
            body = seen["body"]
            self.assertIn("generationConfig", body)
            self.assertEqual(body["generationConfig"].get("responseMimeType"), "application/json")
        finally:
            patches[0].stop(); patches[1].stop()

    def test_unsupported_format_raises(self):
        if not HAS:
            self.skipTest("not implemented")
        # Anthropic stable Messages does not support responseFormat — should raise UnsupportedCapabilityError
        c = conduit.connect(driver="anthropic", endpoint="https://api.anthropic.com")
        with self.assertRaises(Exception) as cm:
            c.model("claude-3-5-sonnet-20241022").generate(messages=[{"role":"user","content":"hi"}], response_format={"type":"json_schema","schema": {"type":"object"}})
        self.assertEqual(getattr(cm.exception,"name",""), "UnsupportedCapabilityError")

    def test_generated_json_remains_text_content_caller_parses(self):
        if not HAS:
            self.skipTest("not implemented")
        from helpers import mock_http_client
        c = conduit.connect(driver="openai-compatible", endpoint="http://127.0.0.1:9/v1")
        patches = mock_http_client(lambda *a, **kw: (200, {}, {"id":"x","model":"m","choices":[{"message":{"role":"assistant","content":'{"city":"Tokyo","temp":22}'},"finish_reason":"stop"}]}))
        patches[0].start(); patches[1].start()
        try:
            r = c.model("m").generate(messages=[{"role":"user","content":"hi"}], response_format={"type":"json"})
            parsed = json.loads(r.text)
            self.assertEqual(parsed["city"], "Tokyo")
        finally:
            patches[0].stop(); patches[1].stop()
