"""Normalized errors shared by the Conduit Python implementation."""

from __future__ import annotations

from typing import Any, Dict, Optional

ErrorCode = str

ERROR_CODES = {
    "AuthenticationError", "AuthorizationError", "ConnectionError", "TimeoutError",
    "RateLimitError", "InvalidRequestError", "UnsupportedCapabilityError",
    "ModelNotFoundError", "ProviderError", "ProtocolError", "CancelledError",
}


class ConduitError(Exception):
    """Normalized error contract. ``name`` is the stable category."""

    def __init__(self, name: str, message: str, *, status_code: Optional[int] = None,
                 provider_code: Optional[str] = None, request_id: Optional[str] = None,
                 provider_details: Optional[Dict[str, str]] = None,
                 cause: Optional[Dict[str, str]] = None) -> None:
        super().__init__(message)
        self.name: str = name
        self.status_code = status_code
        self.provider_code = provider_code
        self.request_id = request_id
        self.provider_details = provider_details
        self.cause = cause

    def __str__(self) -> str:
        return f"{self.name}: {self.args[0]}"

    def __repr__(self) -> str:
        return f"ConduitError({self.name!r}, {self.args[0]!r}, status_code={self.status_code!r})"


def http_failure(status: int, details: Dict[str, str], request_id: Optional[str] = None,
                model_not_found: bool = False) -> ConduitError:
    codes: Dict[int, str] = {400: "InvalidRequestError", 401: "AuthenticationError", 403: "AuthorizationError", 408: "TimeoutError", 422: "InvalidRequestError", 429: "RateLimitError"}
    name = "ModelNotFoundError" if status == 404 and model_not_found else codes.get(status, "ProviderError")
    msg = details.get("message") or f"Provider returned HTTP {status}."
    kwargs: Dict[str, Any] = {"status_code": status}
    if request_id is not None:
        kwargs["request_id"] = request_id
    if details.get("code") is not None:
        kwargs["provider_code"] = details["code"]
    if details:
        kwargs["provider_details"] = dict(details)
    return ConduitError(name, msg, **kwargs)
