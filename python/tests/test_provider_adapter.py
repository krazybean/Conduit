import unittest
import json
from pathlib import Path

from conduit import connect, provider_manifest, provider_presets
from conduit.providers.anthropic import AnthropicProvider
from conduit.providers.gemini import GeminiProvider
from conduit.providers.ollama import OllamaProvider
from conduit.providers.openai import OpenAIProvider


class ProviderAdapterContract(unittest.TestCase):
    def test_bundled_manifest_is_canonical_and_complete(self):
        canonical = json.loads((Path(__file__).parents[2] / "conformance/providers.json").read_text())
        self.assertEqual(provider_manifest, canonical)
        self.assertEqual(set(provider_presets), set(canonical["providers"]))
        self.assertIn("python", canonical["languages"])
        self.assertEqual(canonical["capabilities"], ["provider_preset", "endpoint_override"])

    def test_presets_select_driver_and_allow_endpoint_override(self):
        self.assertEqual(provider_presets["muse"]["driver"], "openai-compatible")
        self.assertEqual(connect(provider="muse")._endpoint, "https://api.meta.ai/v1")
        self.assertEqual(connect(provider="groq", endpoint="http://127.0.0.1:1/v1")._endpoint, "http://127.0.0.1:1/v1")
        with self.assertRaises(Exception):
            connect(provider="muse", driver="ollama", model="test")

    def test_for_driver_resolves_each_supported_provider(self):
        from conduit.providers import for_driver
        self.assertIsInstance(for_driver("openai-compatible"), OpenAIProvider)
        self.assertIsInstance(for_driver("ollama"), OllamaProvider)
        self.assertIsInstance(for_driver("anthropic"), AnthropicProvider)
        self.assertIsInstance(for_driver("gemini"), GeminiProvider)

    def test_for_driver_rejects_unknown_provider(self):
        from conduit.providers import for_driver
        with self.assertRaises(ValueError):
            for_driver("unsupported")
