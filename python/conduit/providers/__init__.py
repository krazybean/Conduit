"""Provider adapters and the bundled provider preset manifest."""

import json
from pathlib import Path

from typing import Any, Dict, Iterator, List, Optional, Protocol, Tuple

from ..types import GenerationResponse, ModelInfo
from .anthropic import AnthropicProvider
from .gemini import GeminiProvider
from .ollama import OllamaProvider
from .openai import OpenAIProvider

with (Path(__file__).resolve().parent.parent / "providers.json").open(encoding="utf-8") as _manifest_file:
    provider_manifest = json.load(_manifest_file)
provider_presets = provider_manifest["providers"]


def preset(name: str) -> Dict[str, str]:
    try:
        return provider_presets[name]
    except KeyError:
        raise ValueError(f"Unknown provider preset: {name}") from None


class ProviderAdapter(Protocol):
    stream_content_types: Tuple[str, ...]

    def validate_request(self, request: Dict[str, Any], streaming: bool) -> None: ...
    def encode_request(self, model: str, messages: List[Dict[str, Any]], request: Dict[str, Any], streaming: bool) -> str: ...
    def decode_response(self, value: Any, request_id: Optional[str], redact) -> GenerationResponse: ...
    def http_error(self, status: int, body: str, request_id: Optional[str], redact, model: Optional[str] = None): ...
    def parse_stream(self, chunks: Iterator[bytes], request_id: Optional[str], redact, model_id: Optional[str] = None) -> Iterator[Dict[str, Any]]: ...
    def normalize_models(self, value: Any, redact) -> List[ModelInfo]: ...
    def model_page(self, value: Any, redact) -> Tuple[List[ModelInfo], Optional[str], bool]: ...
    def model_page_url(self, base_url: str, token: Optional[str]) -> str: ...


_PROVIDERS: Dict[str, ProviderAdapter] = {
    "openai-compatible": OpenAIProvider(),
    "ollama": OllamaProvider(),
    "anthropic": AnthropicProvider(),
    "gemini": GeminiProvider(),
}


def for_driver(driver: str) -> ProviderAdapter:
    try:
        return _PROVIDERS[driver]
    except KeyError:
        raise ValueError(f"Unsupported driver: {driver}") from None
