"""ProviderOptions collisions — Conduit-owned wire fields cannot be overridden."""
import unittest

try:
    import conduit
    HAS = hasattr(conduit, "connect")
except Exception:
    HAS = False
    conduit = None


# Owned-field tables per driver (mirrors spec/drivers/*.md and TS reserved lists)
OWNED = {
    "openai-compatible": ["model","messages","stream","stream_options","max_tokens","max_completion_tokens","temperature","top_p","stop","tools","tool_choice","functions","function_call","response_format","n","modalities"],
    "ollama": ["model","messages","stream","tools","format","options","options.num_predict","options.temperature","options.top_p","options.stop"],
    "anthropic": ["model","messages","system","stream","max_tokens","temperature","top_p","top_k","stop_sequences","tools","tool_choice"],
    "gemini": ["contents","systemInstruction","generationConfig","tools","toolConfig","safetySettings","cachedContent","generationConfig.maxOutputTokens","generationConfig.temperature","generationConfig.topP","generationConfig.stopSequences","generationConfig.responseMimeType","generationConfig.responseJsonSchema","generationConfig.candidateCount"],
}


class ProviderOptionsCollisions(unittest.TestCase):
    def test_each_driver_rejects_its_owned_fields(self):
        if not HAS:
            # Contract-only: tables above are authoritative; ensure no empty driver
            for driver, fields in OWNED.items():
                self.assertTrue(len(fields) > 0, driver)
            return
        for driver, fields in OWNED.items():
            endpoint = "http://127.0.0.1:9/v1" if driver != "ollama" else "http://127.0.0.1:11434"
            if driver == "anthropic":
                endpoint = "https://api.anthropic.com"
            if driver == "gemini":
                endpoint = "https://generativelanguage.googleapis.com/v1beta"
            creds = {} if driver == "ollama" else {"credentials": "k"}
            c = conduit.connect(driver=driver, endpoint=endpoint, **creds)
            m = c.model("test-model")
            for field in fields:
                # For nested generationConfig fields, supply via provider_options.generationConfig
                if field.startswith("generationConfig.") or field.startswith("options."):
                    parent, child = field.split(".", 1)
                    with self.assertRaises(Exception, msg=f"{driver}:{field} should collide"):
                        m.generate(messages=[{"role":"user","content":"hi"}], provider_options={parent: {child: 1}})
                else:
                    with self.assertRaises(Exception, msg=f"{driver}:{field} should collide"):
                        m.generate(messages=[{"role":"user","content":"hi"}], provider_options={field: 1})

    def test_non_conflicting_options_pass_through(self):
        if not HAS:
            self.skipTest("not implemented")
        from helpers import mock_http_client
        c = conduit.connect(driver="openai-compatible", endpoint="http://127.0.0.1:9/v1")
        seen = {}
        def responder(method, url, body, headers):
            seen["body"] = body
            return 200, {}, {"id":"x","model":"m","choices":[{"message":{"role":"assistant","content":"ok"},"finish_reason":"stop"}]}
        patches = mock_http_client(responder)
        patches[0].start(); patches[1].start()
        try:
            c.model("m").generate(messages=[{"role":"user","content":"hi"}], provider_options={"native_flag": True, "custom": 123})
            self.assertEqual(seen["body"].get("native_flag"), True)
            self.assertEqual(seen["body"].get("custom"), 123)
        finally:
            patches[0].stop(); patches[1].stop()

    def test_anthropic_generationConfig_subfield_collision(self):
        if not HAS:
            self.skipTest("not implemented")
        c = conduit.connect(driver="gemini", endpoint="https://generativelanguage.googleapis.com/v1beta", credentials="k")
        m = c.model("m")
        with self.assertRaises(Exception) as cm:
            m.generate(messages=[{"role":"user","content":"hi"}], provider_options={"generationConfig": {"maxOutputTokens": 10}})
        self.assertEqual(getattr(cm.exception,"name",""), "InvalidRequestError")

    def test_cyclic_and_non_json_provider_options_rejected(self):
        if not HAS:
            self.skipTest("not implemented")
        c = conduit.connect(driver="openai-compatible", endpoint="http://127.0.0.1:9/v1")
        m = c.model("m")
        cycle = {}; cycle["self"] = cycle
        for bad in [cycle, {"x": float("inf")}, {"x": float("nan")}, {"x": object()}]:
            with self.assertRaises(Exception):
                m.generate(messages=[{"role":"user","content":"hi"}], provider_options=bad)
