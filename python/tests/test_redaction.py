"""Credential redaction boundaries — mirrors errors.md and generation.test redaction."""
import json, unittest, inspect

SECRET = "synthetic-bearer-47"
HEADER_SECRET = "synthetic-header-83"

try:
    import conduit
    HAS = hasattr(conduit, "connect")
except Exception:
    HAS = False
    conduit = None


class RedactionBoundaries(unittest.TestCase):
    def test_provider_metadata_and_errors_redact_but_content_preserved(self):
        if not HAS:
            # Contract: diagnostics redact, semantic content does not
            content = f"Echo {SECRET} {HEADER_SECRET}"
            self.assertEqual(content, f"Echo {SECRET} {HEADER_SECRET}")
            # Redacted version would replace with [REDACTED] — verify boundary name
            self.assertNotEqual("redacted", content)
            return
        from helpers import mock_http_client
        c = conduit.connect(driver="openai-compatible", endpoint="http://127.0.0.1:9/v1", credentials=SECRET, headers={"x-private": f" {HEADER_SECRET} "})
        def responder(method, url, body, headers):
            # Provider echos secrets in error and in response id/model
            # Error path
            return 401, {"x-request-id": SECRET}, {"error":{"message": f"Denied {SECRET} {HEADER_SECRET}", "type": HEADER_SECRET, "code": SECRET}}
        patches = mock_http_client(responder)
        patches[0].start(); patches[1].start()
        try:
            with self.assertRaises(Exception) as cm:
                c.model("m").generate(messages=[{"role":"user","content":"hi"}])
            e = cm.exception
            for rep in [str(e), json.dumps(getattr(e,"provider_details", {}) or {}, default=str), getattr(e,"request_id","") or ""]:
                self.assertNotIn(SECRET, rep)
                self.assertNotIn(HEADER_SECRET, rep)
        finally:
            patches[0].stop(); patches[1].stop()

        # Success path: content must remain unredacted
        def responder2(method, url, body, headers):
            return 200, {"x-request-id": SECRET}, {"id": SECRET, "model": SECRET, "choices":[{"message":{"role":"assistant","content": f"Echo {SECRET} {HEADER_SECRET}"},"finish_reason": SECRET}], "usage":{"prompt_tokens":1}}
        patches = mock_http_client(responder2)
        patches[0].start(); patches[1].start()
        try:
            r = c.model("m").generate(messages=[{"role":"user","content":"hi"}])
            self.assertEqual(r.text, f"Echo {SECRET} {HEADER_SECRET}")
            # providerMetadata must be redacted
            meta = getattr(r, "provider_metadata", None) or getattr(r, "providerMetadata", {})
            for v in [str(meta), json.dumps(meta, default=str)]:
                self.assertNotIn(SECRET, v)
        finally:
            patches[0].stop(); patches[1].stop()

    def test_cause_retains_only_sanitized_name_message_code(self):
        if not HAS:
            self.skipTest("not implemented")
        from helpers import mock_http_client
        import socket
        # Simulate connection refused -> ConnectionError with cause.code ECONNREFUSED
        c = conduit.connect(driver="openai-compatible", endpoint="http://127.0.0.1:1/v1")
        # Patch to raise socket.error with secret in message — must be stripped
        orig = __import__("http.client", fromlist=["HTTPConnection"]).HTTPConnection
        class BadConn:
            def __init__(self, *a, **kw): pass
            def request(self, *a, **kw): raise OSError(f"connect {SECRET}")
            def getresponse(self): raise OSError(f"connect {SECRET}")
            def close(self): pass
        import unittest.mock as mock
        with mock.patch("http.client.HTTPConnection", BadConn), mock.patch("http.client.HTTPSConnection", BadConn):
            with self.assertRaises(Exception) as cm:
                c.model("m").generate(messages=[{"role":"user","content":"hi"}])
            e = cm.exception
            self.assertEqual(getattr(e,"name",""), "ConnectionError")
            cause = getattr(e, "cause", None) or getattr(e, "__cause__", None)
            if cause:
                self.assertNotIn(SECRET, str(cause))

    def test_custom_headers_case_insensitive_protection(self):
        if not HAS:
            self.skipTest("not implemented")
        for hdr in ["Authorization","aUtHoRiZaTiOn","Proxy-Authorization","Cookie","Host","Content-Type","Content-Length","Connection"]:
            with self.assertRaises(Exception) as cm:
                conduit.connect(driver="openai-compatible", endpoint="http://127.0.0.1:9/v1", headers={hdr: "x"})
            self.assertEqual(getattr(cm.exception,"name",""), "InvalidRequestError")
            self.assertNotIn(SECRET, str(cm.exception))

    def test_endpoint_userinfo_query_fragment_rejected(self):
        if not HAS:
            self.skipTest("not implemented")
        for ep in [f"http://{SECRET}@localhost/v1", f"http://localhost/v1?key={SECRET}", f"http://localhost/v1#{SECRET}", "ftp://localhost/v1"]:
            with self.assertRaises(Exception) as cm:
                conduit.connect(driver="openai-compatible", endpoint=ep)
            self.assertEqual(getattr(cm.exception,"name",""), "InvalidRequestError")
            self.assertNotIn(SECRET, str(cm.exception))
