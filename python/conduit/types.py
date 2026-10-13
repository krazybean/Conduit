"""Public value objects shared by the Conduit Python implementation."""

from __future__ import annotations

from typing import Any, Dict, List, Optional


class GenerationResponse:
    def __init__(self, *, id: Optional[str] = None, model: Optional[str] = None,
                 content: List[Dict[str, Any]], finish_reason: str,
                 usage: Optional[Dict[str, int]] = None,
                 provider_metadata: Optional[Dict[str, Any]] = None):
        self.id = id
        self.model = model
        self.content = content
        self.finish_reason = finish_reason
        self.usage = usage
        self.provider_metadata: Dict[str, Any] = provider_metadata or {}

    @property
    def text(self) -> str:
        return "".join(p.get("text", "") for p in self.content if p.get("type") == "text")

    @property
    def tool_calls(self) -> List[Dict[str, Any]]:
        return [p for p in self.content if p.get("type") == "tool_call"]

    def __getitem__(self, key: str) -> Any:
        if key == "id": return self.id
        if key == "model": return self.model
        if key == "content": return self.content
        if key == "finish_reason": return self.finish_reason
        if key == "usage": return self.usage
        if key == "provider_metadata": return self.provider_metadata
        if key == "text": return self.text
        if key == "tool_calls": return self.tool_calls
        raise KeyError(key)

    def __contains__(self, key: object) -> bool:
        return key in ("id", "model", "content", "finish_reason", "usage", "provider_metadata", "text", "tool_calls")

    def get(self, key: str, default: Any = None) -> Any:
        try:
            return self[key]
        except KeyError:
            return default

    def keys(self):
        return ("id", "model", "content", "finish_reason", "usage", "provider_metadata", "text", "tool_calls")

    def __iter__(self):
        return iter(self.keys())

    def __repr__(self) -> str:
        return f"GenerationResponse(id={self.id!r}, finish_reason={self.finish_reason!r}, text={self.text!r})"


def text_response(fields: Dict[str, Any]) -> GenerationResponse:
    return GenerationResponse(id=fields.get("id"), model=fields.get("model"),
                              content=fields.get("content", []),
                              finish_reason=fields["finish_reason"],
                              usage=fields.get("usage"),
                              provider_metadata=fields.get("provider_metadata", {}))


class ModelInfo:
    def __init__(self, id: str, name: Optional[str] = None,
                 provider_metadata: Optional[Dict[str, Any]] = None):
        self.id = id
        self.name = name
        self.provider_metadata = provider_metadata

    def __repr__(self) -> str:
        return f"ModelInfo(id={self.id!r})"
