# Clients and selected models

`ClientConfig` contains a driver identifier, optional endpoint, optional
credentials, optional custom headers, and optional operation timeout default.
Drivers: `openai-compatible`, `ollama`, `anthropic`, `gemini`. Unknown drivers,
invalid endpoints, and malformed settings are `InvalidRequestError`.
No public registries or provider factories. Drivers define documented endpoint
and credential requirements; do not infer arbitrary credentials from ambient
state. Endpoint credentials/userinfo must not become public configuration.

`connect(ClientConfig)` creates a client/provider connection. It MUST NOT make
network calls, including credential checks or capability discovery.
`client.model(id, ModelConfig?)` selects an explicit nonempty identifier locally;
selection does not assert remote existence. `ModelConfig` may carry caller-supplied
capability evidence/metadata; it does not duplicate generation settings.
A selected model exposes `generate`, `stream`, and local `capabilities`.
`connect(config with model)` is an optional language convenience equivalent to
these two operations. Prefer the client form first; no builders are required.

`client.list_models()` explicitly fetches provider model information when
implemented/supported. It returns a collection of `ModelInfo`:

- `id`: provider identifier, preserved exactly.
- `name?`: provider display name.
- `capabilities?`: known tri-state evidence, not guesses.
- `metadata?`: provider fields (size, family, quantization, owner, timestamps).

Unsupported listing raises `UnsupportedCapabilityError`; do not fabricate a
catalog. Listing does not select a model or maintain a universal database.
Pagination/completeness must be defined by a driver before listing ships.
Clients may expose local capabilities, including `model_listing`.

## Secrets and state

Credentials must be stored separately from ordinary public configuration and
excluded from repr/debug output, JSON serialization, logging, errors, and public
config views. Treat sensitive custom headers the same way. Protect authentication
headers case-insensitively; conflicting custom headers raise `InvalidRequestError`.
Drivers must define their owned headers before accepting custom headers. Do not
forward credentials across redirects to a different origin. Do not log raw
request/response objects or unsanitized provider diagnostics.

Each request supplies its full conversation. No retained conversation, session,
or application state; transport pooling/reuse is allowed. Defer caches until
needed. Cleanup should use native language mechanisms when transport needs it.

## Timeout and cancellation

A timeout covers one entire operation, including connection and response reads;
for a stream it covers consumption through completion, not a fresh timer per
chunk. A per-operation timeout may override the client default. A deadline
failure is `TimeoutError`. Explicit caller cancellation is `CancelledError` and
must stop I/O promptly, releasing resources. Neither produces successful output.
Use native mechanisms (e.g. AbortSignal); Python need not be async for symmetry.
Stopping stream consumption must release the connection/read resources; if the
caller still awaits an explicitly cancelled operation it receives `CancelledError`.
No automatic retries, even for timeout or cancellation.

## First TypeScript slice

Node 22.13+ ESM; native fetch and AbortController, no HTTP runtime dependency.
`connect(config)` returns a client with `.model(id)`; `connect({...config, model})`
returns a selected model with `.generate(request)`. No other operations are
exposed yet. Credentials are an optional bearer-token string; configuration uses
`headers` for custom string-valued headers and optional `timeout` in milliseconds.
Per-request `timeout` overrides the client default. There is no Conduit deadline
unless supplied. Timeouts must be integers from 1 through 2147483647 milliseconds
(the native timer limit); invalid values are rejected, not clamped.
`signal` is a native AbortSignal. The first observed abort source wins; a timeout
is TimeoutError and a caller abort is CancelledError regardless of signal reason.
Pre-aborted signals make no request. Timers/listeners are removed after completion.
