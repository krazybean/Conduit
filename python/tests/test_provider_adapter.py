import unittest

from conduit.providers import for_driver
from conduit.providers.anthropic import AnthropicProvider
from conduit.providers.gemini import GeminiProvider
from conduit.providers.ollama import OllamaProvider
from conduit.providers.openai import OpenAIProvider


class ProviderAdapterContract(unittest.TestCase):
    def test_for_driver_resolves_each_supported_provider(self):
        self.assertIsInstance(for_driver("openai-compatible"), OpenAIProvider)
        self.assertIsInstance(for_driver("ollama"), OllamaProvider)
        self.assertIsInstance(for_driver("anthropic"), AnthropicProvider)
        self.assertIsInstance(for_driver("gemini"), GeminiProvider)

    def test_for_driver_rejects_unknown_provider(self):
        with self.assertRaises(ValueError):
            for_driver("unsupported")
