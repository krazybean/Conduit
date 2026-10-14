import json
import pathlib
import unittest

import conduit
from helpers import mock_http_client


CASES = json.loads((pathlib.Path(__file__).parents[2] / "conformance/provider-resolution.json").read_text())


class ProviderResolutionConformance(unittest.TestCase):
    def test_all_shared_cases(self):
        for case in CASES:
            with self.subTest(case=case["id"]):
                config = dict(case["config"])
                if "endpoint" in config:
                    config["endpoint"] = config["endpoint"].replace("http://127.0.0.1:0", "http://127.0.0.1:43127")
                if case["kind"] == "error":
                    with self.assertRaises(conduit.ConduitError) as raised:
                        conduit.connect(**config)
                    self.assertEqual(raised.exception.name, case["expected"]["error"])
                    continue

                client = conduit.connect(**config)
                if case["kind"] == "resolve":
                    self.assertEqual(client._driver, case["expected"]["driver"])
                    self.assertEqual(client._endpoint, case["expected"]["endpoint"])
                    continue

                def responder(method, url, body, headers):
                    path = url.split("?", 1)[0]
                    payload = {"models": []} if path.endswith(("/api/tags", "/v1beta/models")) else {"data": []}
                    return 200, {"content-type": "application/json"}, json.dumps(payload).encode()

                patches = mock_http_client(responder)
                patches[0].start(); patches[1].start()
                try:
                    client.list_models()
                finally:
                    patches[0].stop(); patches[1].stop()
                self.assertEqual(client._list_url.split("?", 1)[0], f"http://127.0.0.1:43127{case['expected']['path']}")
                for key, value in case["expected"]["headers"].items():
                    self.assertEqual(client._headers.get(key), value)


if __name__ == "__main__":
    unittest.main()
