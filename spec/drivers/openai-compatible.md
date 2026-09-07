# OpenAI-compatible driver

Driver ID: `openai-compatible`. v0 targets Chat Completions-style
`/v1/chat/completions`, not the OpenAI Responses API. A future native Responses
driver would be a separate decision. No provider SDK dependency.

Use one driver with configurable API base URL for OpenAI Chat Completions,
OpenRouter, Muse-compatible endpoints, LM Studio, vLLM, llama.cpp servers, Ollama
compatibility endpoints, and other sufficiently compatible services. Compatibility
varies; a provider name is not a promise of every feature.

Initial endpoint convention: `endpoint` is the API base including any version
prefix (e.g. `http://localhost:1234/v1`); append `/chat/completions` preserving
base path, with or without a trailing slash. Require an explicit endpoint in the
first slice. Optional credentials supply a bearer token; local services may not
need one. Custom headers must not override Authorization or transport-owned
headers. Reject URL userinfo, query, or fragment rather than ambiguous joining.

Map normalized requests/responses around one completion choice. Reserve wire
model/messages/stream/tools/tool_choice/response_format, mapped sampling/token
fields, and choice-count controls; do not silently discard multiple choices.
The first slice maps the output budget to `max_tokens`, for broad compatibility;
`max_completion_tokens` is reserved as a conflicting alternative. If an
endpoint/model rejects a normalized feature, surface a normalized error.
Do not silently try another field, model, endpoint, or API.

Listing and tools remain future slices. Text streaming is specified below.
Native finish reasons remain diagnostic metadata.

## Implemented first slice

TypeScript supports non-streaming text generation and the text-streaming slice
described below. Text parts are sent as ordered Chat Completions text parts; string input normalizes to a single text
part. Wire `stream` is false for generate and true for stream. Common fields map
directly to `max_tokens`,
`temperature` (0–2), `top_p` (0–1), and `stop` (an array of strings). Output token
budgets are positive safe integers. Unknown common request/config/message fields
are rejected rather than ignored. Native options must be finite, acyclic JSON
data; non-JSON values are rejected instead of silently changed by serialization.

Reserve `model`, `messages`, `stream`, `stream_options`, `max_tokens`,
`max_completion_tokens`, `temperature`, `top_p`, `stop`, `tools`, `tool_choice`,
`functions`, `function_call`, `response_format`, `n`, and `modalities` regardless
of whether the corresponding common field is supplied. Other native fields pass
through without adding normalized support for those features. No retries or
compatibility fallback. Redirects are rejected instead of forwarding credentials.

Successful responses require exactly one assistant message with string content
and a string finish reason. Null content is allowed for a `content_filter` finish
and yields no text parts. Unhandled non-text message content, including tool
calls, is a protocol error; do not silently lose content. A `tool_calls` finish
reason alone still maps to `tool_call`; unfamiliar finish reasons map to `other`.
Optional IDs/model names must be strings. Usage counts must be nonnegative safe
integers when reported; no missing counts are synthesized.

Diagnostic metadata preserves the raw finish reason and request ID when present.
HTTP error details preserve recognized message/type/code strings only. Raw bodies
and native exception objects are not exposed. Known credential and custom-header
values are redacted from diagnostic strings (including header values after HTTP
whitespace normalization), never from generated semantic content; causes
retain only sanitized native
name/message/code. No request configuration is attached to results or errors.
Custom headers cannot set authorization, proxy authorization, cookies, host,
content type/length, or connection/transfer headers. All custom-header values
are treated as sensitive internally.

HTTP 408 maps to TimeoutError. ModelNotFoundError requires HTTP 404 and the exact
provider code `model_not_found`; generic 404 remains ProviderError. Other native
codes remain diagnostic until evidence justifies additional mappings.

## Streaming slice

Reuse the same request mapping. Only stream() permits native `stream_options`
through provider_options; it must be a JSON object. No usage option is injected.
In particular, callers may supply `stream_options: {include_usage: true}`.
Generate still rejects stream_options. The owned `stream` flag and `n` remain
protected in both operations, as do all other reserved fields above.

Require a successful HTTP response with `text/event-stream` media type (parameters
allowed), followed by valid UTF-8 SSE. Parse LF, CRLF, and CR line endings, joining
multiple data lines with newline. Ignore comments and other SSE fields. A blank
line dispatches an event; unfinished data at EOF is not dispatched, per the
[SSE standard](https://html.spec.whatwg.org/multipage/server-sent-events.html#event-stream-interpretation).
Malformed JSON/UTF-8 is ProtocolError, with no raw payload in the error.

Success requires a valid single-choice stream, a string finish reason, and a
blank-line-terminated `[DONE]` data event. EOF alone, a finish reason without
[DONE], and [DONE] without a finish reason are ProtocolError. [DONE] ends the
protocol; cancel remaining body reads rather than waiting for socket closure.
No events after that marker are processed. Meaningful text after a finish reason,
changing response IDs/models, nonzero/multiple choices, or meaningful non-text
content (including tool calls) are ProtocolError. Null/empty content deltas and
assistant-role-only deltas are valid; empty text deltas are not emitted.

A choices-empty chunk is accepted only for reported usage after the stream has
started. Usage may arrive with a choice or before [DONE]; null/omitted usage means
no report, and partial snapshots preserve previously known counts. Start contains
only the ID/model known at the first validated choice; identifiers first reported
later appear in the final response. No model identifier is invented.
A JSON error envelope in an SSE data event raises ProviderError with the existing
sanitized provider diagnostics; without an HTTP failure status, do not guess an
HTTP category from prose. Missing IDs/usage remain absent. Final normalization
reuses generate's response/usage/finish rules. A content_filter finish with no
nonempty text (including an initial role/empty-string delta) produces empty
content, corresponding to a non-streaming filtered response with null content. No raw chunk log is kept as metadata.
