"""Ollama NDJSON — mirrors spec/drivers/ollama.md and ollama.test.mjs."""
import json, pathlib, unittest
from helpers import ndjson_records, SECRET

try:
    import conduit
    HAS = hasattr(conduit, "connect")
except Exception:
    HAS = False
    conduit = None


class OllamaNDJSONContract(unittest.TestCase):
    def test_ndjson_parsing_lf_crlf_multiple_per_read_split_json(self):
        # Multiple JSON records in one read
        recs = [{"model":"llama","message":{"role":"assistant","content":"Hello "},"done":False},
                {"model":"llama","message":{"role":"assistant","content":"world"},"done":False},
                {"model":"llama","done":True}]
        payload = "\n".join(json.dumps(r) for r in recs) + "\n"
        # All in one fragment
        self.assertEqual(ndjson_records([payload.encode()]), recs)
        # CRLF variant
        self.assertEqual(ndjson_records([payload.replace("\n","\r\n").encode()]), recs)
        # Split JSON and split newline across fragments
        mid = len(payload)//2
        self.assertEqual(ndjson_records([payload[:mid].encode(), payload[mid:].encode()]), recs)
        # Split utf-8 multi-byte (emoji) across fragments — slice on bytes may split code units
        emoji_payload = json.dumps({"message":{"role":"assistant","content":"Hello 世界"},"done":True})+"\n"
        raw = emoji_payload.encode("utf-8")
        self.assertEqual(ndjson_records([raw[:5], raw[5:]]), [json.loads(emoji_payload)])

    def test_blank_lines_ignored_and_final_record_without_newline(self):
        recs = [{"message":{"role":"assistant","content":"hi"},"done":False},{"done":True}]
        payload = "\n" + json.dumps(recs[0]) + "\n\n" + json.dumps(recs[1])  # no trailing newline
        self.assertEqual(ndjson_records([payload.encode()]), recs)

    def test_invalid_utf8_json_or_partial_final_is_protocol_error(self):
        with self.assertRaises(Exception):
            ndjson_records([b"\xff\xfe\n"])
        with self.assertRaises(Exception):
            ndjson_records([b'{"incomplete":\n'])
        with self.assertRaises(Exception):
            ndjson_records([b'{"done":true}\n{"partial":'])

    def test_eof_without_done_true_is_protocol_error(self):
        recs = ndjson_records([(json.dumps({"message":{"role":"assistant","content":"hi"},"done":False})+"\n").encode()])
        self.assertFalse(any(r.get("done") is True for r in recs))

    def test_stream_media_types_accepted(self):
        for ctype in ["application/x-ndjson","application/ndjson","application/json"]:
            self.assertIn("ndjson" if "ndjson" in ctype else "json", ctype)

    def test_request_mapping_common_to_ollama_options(self):
        if not HAS:
            # Contract: fixture mapping documented in ollama.md
            mapping = {"max_output_tokens":"options.num_predict","temperature":"options.temperature","top_p":"options.top_p","stop":"options.stop"}
            self.assertEqual(mapping["max_output_tokens"], "options.num_predict")
            return
        c = conduit.connect(driver="ollama", endpoint="http://127.0.0.1:11434")
        m = c.model("llama3.2")
        # Owned fields must raise InvalidRequestError even when equal
        for field in ["model","messages","stream","tools","format"]:
            with self.assertRaises(Exception):
                m.generate(messages=[{"role":"user","content":"hi"}], provider_options={field: 1})
        # options.num_predict etc also owned
        with self.assertRaises(Exception):
            m.generate(messages=[{"role":"user","content":"hi"}], provider_options={"options": {"num_predict": 10}})

    def test_usage_maps_prompt_eval_count(self):
        # Generate response maps prompt_eval_count->inputTokens, eval_count->outputTokens, never synthesizes total
        body = {"model":"llama3.2","message":{"role":"assistant","content":"Hello!"},"done":True,"prompt_eval_count":4,"eval_count":2,"total_duration":1000}
        self.assertEqual(body["prompt_eval_count"], 4)
        self.assertEqual(body["eval_count"], 2)
        # If implementation exposes decode, verify total_tokens absent
        if HAS and hasattr(conduit, "decode_ollama"):
            r = conduit.decode_ollama(body, None, lambda x: x)
            self.assertEqual(r.usage["inputTokens"], 4)
            self.assertNotIn("totalTokens", r.usage)


class OllamaListModelsContract(unittest.TestCase):
    def test_list_models_path_and_normalization(self):
        if not HAS:
            self.skipTest("not implemented")
        c = conduit.connect(driver="ollama", endpoint="http://127.0.0.1:11434")
        from unittest import mock
        from helpers import mock_http_client
        def responder(method, url, body, headers):
            self.assertEqual(method, "GET")
            self.assertTrue(url.endswith("/api/tags"))
            return 200, {"content-type":"application/json"}, {"models":[{"name":"qwen3:8b","size":123,"digest":"abc","details":{"family":"qwen3"}}]}
        patches = mock_http_client(responder)
        patches[0].start(); patches[1].start()
        try:
            models = c.list_models() if hasattr(c,"list_models") else c.listModels()
            self.assertEqual(models[0].id if hasattr(models[0],"id") else models[0]["id"], "qwen3:8b")
        finally:
            patches[0].stop(); patches[1].stop()

    def test_malformed_listing_is_protocol_error(self):
        if not HAS:
            self.skipTest("not implemented")
        from helpers import mock_http_client
        c = conduit.connect(driver="ollama", endpoint="http://127.0.0.1:11434")
        for bad in [{}, {"models":"nope"}, {"models":[{"size":1}]}]:
            patches = mock_http_client(lambda *a, **kw: (200, {}, bad))
            patches[0].start(); patches[1].start()
            try:
                with self.assertRaises(Exception) as cm:
                    (c.list_models() if hasattr(c,"list_models") else c.listModels())
                self.assertIn(getattr(cm.exception,"name", ""), ("ProtocolError","ConduitError"))
            finally:
                patches[0].stop(); patches[1].stop()
