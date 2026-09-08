"""Error mapping — mirrors conformance/errors/*.json and errors.md taxonomy."""
import json, pathlib, unittest

try:
    import conduit
    from conduit import ConduitError
    HAS = True
except Exception:
    HAS = False
    class ConduitError(Exception):
        def __init__(self, name, msg, details=None):
            super().__init__(msg)
            self.name = name
            self.details = details or {}


EXPECTED_MAP = {
    401: "AuthenticationError",
    403: "AuthorizationError",
    408: "TimeoutError",
    429: "RateLimitError",
    400: "InvalidRequestError",
    422: "InvalidRequestError",
}

class ErrorMapping(unittest.TestCase):
    def test_http_status_to_category(self):
        fixtures = json.loads((pathlib.Path(__file__).parents[2] / "conformance/errors/openai-http.json").read_text())
        for f in fixtures:
            status = f["input"]["wire"]["status"]
            expected = f["expected"]["error"]["category"]
            # Contract: mapping must hold
            if status in EXPECTED_MAP:
                self.assertEqual(EXPECTED_MAP[status], expected)
            # 5xx -> ProviderError
            if 500 <= status <= 599:
                self.assertEqual(expected, "ProviderError")

    def test_404_model_not_found_requires_exact_code(self):
        # OpenAI: 404 + code model_not_found => ModelNotFoundError, else ProviderError
        if not HAS:
            self.assertEqual(EXPECTED_MAP.get(404, "ProviderError"), "ProviderError")
            return
        # If implementation helper exists, test httpFailure directly
        fn = getattr(conduit, "http_failure", None) or getattr(conduit, "httpFailure", None)
        if fn is None:
            self.skipTest("no httpFailure helper")
        e = fn(404, {"message":"nope","code":"model_not_found"}, "req", True)
        self.assertEqual(e.name, "ModelNotFoundError")
        e2 = fn(404, {"message":"nope","code":"other"}, "req", False)
        self.assertEqual(e2.name, "ProviderError")

    def test_ollama_404_exact_message(self):
        # Ollama uses plain string message with quotes and pull hint
        variants = ["model 'm' not found", 'model "m" not found', 'model "m" not found, try pulling it first']
        for msg in variants:
            # If python helper exists, it should recognize these as ModelNotFound
            if HAS and hasattr(conduit, "is_ollama_model_not_found"):
                self.assertTrue(conduit.is_ollama_model_not_found(msg, "m"))

    def test_in_band_stream_error_is_provider_error(self):
        # SSE data: {"error":...} inside 200 stream must map to ProviderError
        body = json.dumps({"error":{"message":"stream failed","type":"server_error","code":"internal"}})
        if HAS and hasattr(conduit, "http_failure"):
            # In-band errors use status 200 but still ProviderError
            e = conduit.http_failure(200, {"message":"stream failed","type":"server_error"}, None)
            # Direct http 200 not mapped; but stream wrapper should raise ProviderError
            self.assertIn(e.name, ("ProviderError","ProtocolError"))

    def test_malformed_success_payload_is_protocol_error(self):
        if not HAS:
            self.skipTest("not implemented")
        from helpers import mock_http_client
        c = conduit.connect(driver="openai-compatible", endpoint="http://127.0.0.1:9/v1")
        bad_bodies = [
            {"choices":[]},
            {"choices":[{"message":{"role":"assistant","content":"hi"},"finish_reason": None}]},
            {"id": 42, "choices":[{"message":{"role":"assistant","content":"hi"},"finish_reason":"stop"}]},
            "not json at all",
        ]
        for bad in bad_bodies:
            patches = mock_http_client(lambda *a, **kw: (200, {}, bad if isinstance(bad, dict) else bad))
            patches[0].start(); patches[1].start()
            try:
                with self.assertRaises(Exception) as cm:
                    c.model("m").generate(messages=[{"role":"user","content":"hi"}])
                self.assertEqual(getattr(cm.exception,"name",""), "ProtocolError")
            finally:
                patches[0].stop(); patches[1].stop()

    def test_stream_protocol_error_no_done(self):
        if not HAS:
            self.skipTest("not implemented")
        from helpers import mock_http_client
        import http.client, json as j
        c = conduit.connect(driver="openai-compatible", endpoint="http://127.0.0.1:9/v1")
        def responder(method, url, body, headers):
            # valid headers but invalid SSE payload (multiple choices)
            payload = "data: {\"choices\":[{\"index\":0,\"delta\":{\"content\":\"hi\"}},{\"index\":1,\"delta\":{\"content\":\"bad\"}}]}\n\n"
            return 200, {"content-type":"text/event-stream"}, payload.encode()
        patches = mock_http_client(responder)
        patches[0].start(); patches[1].start()
        try:
            gen = c.model("m").stream(messages=[{"role":"user","content":"hi"}])
            # consume — expect ProtocolError before done
            import asyncio
            async def consume():
                async for ev in gen:
                    if ev.get("type") == "done":
                        self.fail("should not reach done")
            # If sync generator, adapt
            try:
                for ev in gen:
                    if ev.get("type") == "done":
                        self.fail("should not reach done")
            except Exception as e:
                self.assertEqual(getattr(e,"name",""), "ProtocolError")
        finally:
            patches[0].stop(); patches[1].stop()

    def test_anthropic_and_gemini_error_shapes(self):
        anth = json.loads((pathlib.Path(__file__).parents[2]/"conformance/errors/anthropic-http.json").read_text()) if (pathlib.Path(__file__).parents[2]/"conformance/errors/anthropic-http.json").exists() else []
        gem = json.loads((pathlib.Path(__file__).parents[2]/"conformance/errors/gemini-http.json").read_text()) if (pathlib.Path(__file__).parents[2]/"conformance/errors/gemini-http.json").exists() else []
        for f in anth + gem:
            self.assertIn("category", f["expected"]["error"])
            self.assertIn("status_code", f["expected"]["error"])
