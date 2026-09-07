# Errors

`ConduitError` is the stable normalized error contract; implementations may use
idiomatic exception classes or enum variants preserving these categories:

| Category | Meaning / initial mapping |
| --- | --- |
| `AuthenticationError` | Missing/invalid credentials; HTTP 401 |
| `AuthorizationError` | Permission denied; HTTP 403 |
| `ConnectionError` | Network, DNS, socket, or TLS connection failure |
| `TimeoutError` | Operation deadline exceeded; HTTP 408 |
| `RateLimitError` | HTTP 429 / identified provider rate limit |
| `InvalidRequestError` | Invalid input/options; ordinary HTTP 400/422 |
| `UnsupportedCapabilityError` | Requested operation/feature cannot be represented or supported |
| `ModelNotFoundError` | Provider explicitly identifies an unknown model |
| `ProviderError` | Other provider failures, including ordinary HTTP 5xx |
| `ProtocolError` | Malformed/unexpected successful payload, invalid complete tool JSON, truncated stream |
| `CancelledError` | Explicit caller cancellation |

A generic HTTP 404 is not sufficient evidence of a missing model: it may be a
wrong endpoint, so use `ProviderError` absent model-specific evidence. Document
provider-code refinements in driver fixtures. For unsuccessful HTTP responses,
known status mappings still apply when the error body is not JSON. In-band stream
failures use this same taxonomy.

Preserve `message`, optional `status_code`, `provider_code`, `request_id`,
`provider_details`, and `cause` when safe. Cause uses native language mechanisms;
its serialized fixture representation is not a runtime object. Error details
must not expose credentials, sensitive headers, endpoint userinfo, or raw
credential-bearing request objects. Sanitize even provider messages that echo
input; omit unsafe details when reliable redaction is impossible.

Never turn an error into an empty response, an error stream event, or an automatic
retry. No policy engine or hidden decisions about retrying billable operations.

The initial TypeScript API exports one `ConduitError` class with `name` as the
stable category (the `ErrorCode` string union), rather than one subclass per
category. Applications can test `instanceof ConduitError` and switch on `name`.
