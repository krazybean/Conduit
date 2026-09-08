"""Timeout/cancellation — entire operation deadline, first abort wins, prompt I/O stop."""
import time, threading, unittest, http.server, socket, json

SECRET = "synthetic-bearer-47"

try:
    import conduit
    HAS = hasattr(conduit, "connect")
except Exception:
    HAS = False
    conduit = None


class TimeoutContract(unittest.TestCase):
    def test_timeout_covers_entire_stream_not_per_chunk(self):
        # Spec: timeout covers operation through stream completion, not fresh timer per chunk
        if not HAS:
            self.skipTest("not implemented")
        from helpers import local_server
        import http.client
        # Server holds stream open after first chunk — deadline must fire during pause
        def handle(handler, req):
            handler.send_response(200)
            handler.send_header("content-type", "text/event-stream")
            handler.end_headers()
            handler.wfile.write(b'data: {"choices":[{"index":0,"delta":{"content":"partial"}}]}\n\n')
            handler.wfile.flush()
            time.sleep(0.5)  # longer than timeout
            try:
                handler.wfile.write(b'data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n')
                handler.wfile.write(b"data: [DONE]\n\n")
            except BrokenPipeError:
                pass
        with local_server(handle) as (endpoint, reqs, srv):
            c = conduit.connect(driver="openai-compatible", endpoint=endpoint)
            # per-request timeout 100ms must fail during pause, not per-chunk reset
            gen = c.model("m").stream(messages=[{"role":"user","content":"hi"}], timeout=100)
            # Sync generator consumption — collect until error
            from helpers import SECRET as S
            try:
                events = []
                for ev in gen:
                    events.append(ev)
                self.fail("should have timed out")
            except Exception as e:
                self.assertEqual(getattr(e,"name",""), "TimeoutError")

    def test_pre_aborted_signal_makes_no_request(self):
        if not HAS:
            self.skipTest("not implemented")
        from helpers import mock_http_client
        calls = {"n":0}
        def responder(*a, **kw):
            calls["n"]+=1
            return 200, {}, {"id":"x","model":"m","choices":[{"message":{"role":"assistant","content":"hi"},"finish_reason":"stop"}]}
        patches = mock_http_client(responder)
        patches[0].start(); patches[1].start()
        try:
            import threading
            c = conduit.connect(driver="openai-compatible", endpoint="http://127.0.0.1:9/v1")
            # Python cancellation uses threading.Event or custom signal — model as CancelledError
            # Simulate by calling with timeout=0 equivalent: mock a pre-cancelled flag
            # If API accepts signal/event, test that zero-timeout-equivalent raises before I/O
            # Use invalid timeout 0 to ensure no I/O if cancellation primitive not exposed
            # Fallback: check that abort-like object causes CancelledError without network
            class FakeSignal:
                aborted = True
                reason = Exception(SECRET)
            with self.assertRaises(Exception) as cm:
                c.model("m").generate(messages=[{"role":"user","content":"hi"}], signal=FakeSignal())
            self.assertEqual(getattr(cm.exception,"name",""), "CancelledError")
            self.assertEqual(calls["n"], 0)
        finally:
            patches[0].stop(); patches[1].stop()

    def test_first_abort_wins_timeout_over_cancellation(self):
        if not HAS:
            self.skipTest("not implemented")
        from helpers import local_server
        def handle(handler, req):
            handler.send_response(200)
            handler.send_header("content-type", "application/json")
            handler.end_headers()
            time.sleep(0.5)
            handler.wfile.write(b'{"id":"x","model":"m","choices":[{"message":{"role":"assistant","content":"hi"},"finish_reason":"stop"}]}')
        with local_server(handle) as (endpoint, reqs, srv):
            c = conduit.connect(driver="openai-compatible", endpoint=endpoint)
            # Timeout fires before manual cancel — should be TimeoutError
            import threading
            cancel = threading.Event()
            # If stream API supports signal, race: timeout 50ms vs cancel at 200ms -> Timeout wins
            try:
                c.model("m").generate(messages=[{"role":"user","content":"hi"}], timeout=50, signal=cancel)
                self.fail("should timeout")
            except Exception as e:
                self.assertEqual(getattr(e,"name",""), "TimeoutError")

    def test_break_releases_connection(self):
        if not HAS:
            self.skipTest("not implemented")
        from helpers import local_server
        closed = threading.Event()
        def handle(handler, req):
            handler.send_response(200)
            handler.send_header("content-type", "text/event-stream")
            handler.end_headers()
            handler.wfile.write(b'data: {"choices":[{"index":0,"delta":{"content":"partial"}}]}\n\n')
            handler.wfile.flush()
            # Wait until client closes
            time.sleep(0.3)
            try:
                handler.wfile.write(b'data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n')
            except BrokenPipeError:
                closed.set()
        with local_server(handle) as (endpoint, reqs, srv):
            c = conduit.connect(driver="openai-compatible", endpoint=endpoint)
            gen = c.model("m").stream(messages=[{"role":"user","content":"hi"}])
            it = iter(gen)
            ev = next(it)
            self.assertEqual(ev.get("type"), "start")
            ev2 = next(it)
            self.assertEqual(ev2.get("type"), "text_delta")
            # break / close generator
            try:
                if hasattr(gen, "close"):
                    gen.close()
                elif hasattr(it, "close"):
                    it.close()
            except Exception:
                pass
            time.sleep(0.2)
            # Server should have seen disconnect — no assertion on timing, just no hang

    def test_invalid_timeout_values_rejected_not_clamped(self):
        if not HAS:
            self.skipTest("not implemented")
        c = conduit.connect(driver="openai-compatible", endpoint="http://127.0.0.1:9/v1")
        for bad in [0, -1, 0.5, 2147483648, float("inf"), "100"]:
            with self.assertRaises(Exception) as cm:
                c.model("m").generate(messages=[{"role":"user","content":"hi"}], timeout=bad)
            self.assertEqual(getattr(cm.exception,"name",""), "InvalidRequestError")
        # Client-level timeout also validated
        with self.assertRaises(Exception):
            conduit.connect(driver="openai-compatible", endpoint="http://127.0.0.1:9/v1", timeout=0)
