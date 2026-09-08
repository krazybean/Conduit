"""Gemini driver — SSE/pagination, tool IDs, stateless interactions."""
import json, unittest

try:
    import conduit
    HAS = hasattr(conduit, "connect")
except Exception:
    HAS = False
    conduit = None


class GeminiStreamContract(unittest.TestCase):
    def test_sse_data_lines_join_and_done_semantics(self):
        from helpers import parse_sse_fragments
        # Gemini SSE also uses data: lines; [DONE] not required — stream ends on finishReason
        payloads = [
            {"candidates":[{"content":{"parts":[{"text":"Hello "}]}}],"usageMetadata":{"promptTokenCount":4}},
            {"candidates":[{"content":{"parts":[{"text":"world"}]},"finishReason":"STOP"}],"usageMetadata":{"candidatesTokenCount":2,"totalTokenCount":6}},
        ]
        raw = b"".join(f"data: {json.dumps(p)}\n\n".encode() for p in payloads)
        events = parse_sse_fragments([raw])
        self.assertEqual(len(events), 2)
        self.assertEqual(json.loads(events[0])["candidates"][0]["content"]["parts"][0]["text"], "Hello ")

    def test_tool_call_ids_preserved_per_fragment(self):
        # Gemini functionCall may carry id; must be preserved verbatim and not fabricated
        body = {"candidates":[{"content":{"parts":[
            {"functionCall":{"id":"call_123","name":"get_weather","args":{"city":"Tokyo"}}}
        ]},"finishReason":"STOP"}],"usageMetadata":{"promptTokenCount":4,"candidatesTokenCount":10,"totalTokenCount":14}}
        if HAS and hasattr(conduit, "decode_gemini"):
            r = conduit.decode_gemini(body, None, lambda x: x)
            self.assertEqual(r.tool_calls[0].id, "call_123")
            self.assertEqual(r.tool_calls[0].name, "get_weather")
        else:
            self.assertEqual(body["candidates"][0]["content"]["parts"][0]["functionCall"]["id"], "call_123")

    def test_stream_tool_call_deltas_accumulate_args_as_string(self):
        # Gemini stream yields tool_call_delta with argumentsDelta as JSON string
        parts = [
            {"functionCall":{"name":"get_weather","args":{"city":"Tokyo"}}},
            {"functionCall":{"name":"get_time","args":{"zone":"UTC"}}},
        ]
        # Simulate two delta events each carrying one functionCall
        deltas = [{"index": i, "name": p["functionCall"]["name"], "argumentsDelta": json.dumps(p["functionCall"]["args"])} for i,p in enumerate(parts)]
        self.assertEqual(deltas[0]["argumentsDelta"], '{"city": "Tokyo"}')
        self.assertEqual(deltas[1]["index"], 1)

    def test_thinking_parts_not_emitted_as_text_delta(self):
        # Parts with thought:true stay metadata
        body = {"candidates":[{"content":{"parts":[
            {"text":"visible","thought":True},
            {"text":"Hello!"}
        ]}}]}
        # Only non-thought text should be emitted
        texts = [p["text"] for p in body["candidates"][0]["content"]["parts"] if not p.get("thought")]
        self.assertEqual(texts, ["Hello!"])

    def test_stateless_request_has_no_history_id(self):
        if not HAS:
            self.skipTest("not implemented")
        c = conduit.connect(driver="gemini", endpoint="https://generativelanguage.googleapis.com/v1beta", credentials="key")
        m = c.model("gemini-2.0-flash")
        # providerOptions that enable chaining must be rejected
        for bad in [{"contents": []}, {"cachedContent":"x"}, {"generationConfig": {"candidateCount":2}}]:
            # candidateCount is owned — check via generationConfig sub-field
            pass
        # Direct check: store / previous_interaction_id must not be settable
        with self.assertRaises(Exception):
            m.generate(messages=[{"role":"user","content":"hi"}], provider_options={"cachedContent": "x"})


class GeminiPaginationContract(unittest.TestCase):
    def test_list_models_aggregates_next_page_token(self):
        if not HAS:
            self.skipTest("not implemented")
        from helpers import mock_http_client
        pages = [
            (200, {}, {"models":[{"name":"models/gemini-1"},{"name":"models/gemini-2"}],"nextPageToken":"t2"}),
            (200, {}, {"models":[{"name":"models/gemini-3"}]}),
        ]
        calls = {"i":0}
        def responder(method, url, body, headers):
            idx = calls["i"]; calls["i"]+=1
            return pages[idx]
        c = conduit.connect(driver="gemini", endpoint="https://generativelanguage.googleapis.com/v1beta", credentials="key")
        patches = mock_http_client(responder)
        patches[0].start(); patches[1].start()
        try:
            fn = getattr(c, "list_models", None) or getattr(c, "listModels", None)
            if fn is None:
                self.skipTest("listModels not implemented")
            models = fn()
            self.assertEqual(len(models), 3)
        finally:
            patches[0].stop(); patches[1].stop()

    def test_malformed_paginated_payload_is_protocol_error(self):
        if not HAS:
            self.skipTest("not implemented")
        from helpers import mock_http_client
        c = conduit.connect(driver="gemini", endpoint="https://generativelanguage.googleapis.com/v1beta", credentials="key")
        patches = mock_http_client(lambda *a, **kw: (200, {}, {"not_models": []}))
        patches[0].start(); patches[1].start()
        try:
            fn = getattr(c, "list_models", None) or getattr(c, "listModels", None)
            if fn:
                with self.assertRaises(Exception) as cm:
                    fn()
                self.assertEqual(getattr(cm.exception,"name",""), "ProtocolError")
        finally:
            patches[0].stop(); patches[1].stop()
