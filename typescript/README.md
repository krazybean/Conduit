# TypeScript / JavaScript

Local, private ESM package for **Node 22.13+**. The minimum was chosen to use
[native fetch and abort facilities](https://nodejs.org/download/release/v22.15.0/docs/api/globals.html)
without HTTP or compatibility dependencies. Verified on Node 22.13.1. Browser,
Bun, Deno, CommonJS, and older Node versions are not claimed as supported.

From the repository root:

```sh
npm ci --prefix typescript
npm run typecheck --prefix typescript
npm test --prefix typescript
```

`npm test` builds ESM JavaScript and declarations into `dist/`, then uses Node's
built-in test runner against localhost servers and shared fixtures. No real
provider credentials or Internet are needed for tests. TypeScript 5.9.3 is the
only development dependency; there are zero runtime dependencies. No lint or
format framework is configured. Python/Rust implementation is still deferred.

## Public API

```ts
import { connect, ConduitError } from "./typescript/dist/index.js";

const client = connect({
  driver: "openai-compatible",
  endpoint: "http://localhost:1234/v1",
  credentials: process.env.CONDUIT_API_KEY,
  headers: { "x-application": "example" }, // optional
});
const model = client.model("my-model");
// Equivalent: connect({ driver, endpoint, credentials, model: "my-model" })

const response = await model.generate({
  messages: [
    { role: "system", content: "Be concise." },
    { role: "user", content: [{ type: "text", text: "Hello." }] },
  ],
  maxOutputTokens: 64,
  temperature: 0.5,
  topP: 1,
  stop: ["END"],
  timeout: 10000,
  // signal: abortController.signal,
  // providerOptions: { native_flag: true },
});
console.log(response.text);
```

Runtime exports: `connect`, `ConduitError`. Type exports: `ClientConfig`, `Client`,
`Model`, `Message`, `TextPart`, `JsonValue`, `GenerationRequest`,
`GenerationResponse`, `StreamEvent`, `Usage`, `ErrorCode`, `ErrorDetails`.

Clients expose `.model(id)`; selected models expose `.generate(request)` and
`.stream(request)`. Construction/selection performs no I/O. No listModels, capabilities,
tools, structured-output, image, or reasoning API is implemented. Unsupported
common feature requests fail explicitly; unknown fields are invalid requests.

`endpoint` is an HTTP(S) **API base URL**, including any version prefix. Trailing
slashes are removed and `/chat/completions` is appended. `/v1` is never guessed:
`http://host/custom/v1/` becomes `http://host/custom/v1/chat/completions`.
Userinfo, query, and fragment are rejected. No provider autodetection or redirects.

Messages support system/user/assistant with strings or ordered text-part arrays.
Strings become one text part on the wire. Parameters map to `max_tokens`,
`temperature`, `top_p`, and `stop`; the owned `stream` flag is false for generate
and true for stream. Token budgets are
positive safe integers, temperature is 0–2, topP is 0–1, stop is a string array.
Omitted sampling fields stay omitted. The driver makes exactly one HTTP request.

## Results and errors

Responses expose optional `id`, `model`, `usage`, required `content`, `text` as a
view over content, `finishReason`, and `providerMetadata`. Usage maps to optional
`inputTokens`, `outputTokens`, `totalTokens`; missing means absent, never zero.
Finish reasons map stop/length/content_filter directly and tool_calls to tool_call;
unknown strings become other. Metadata preserves raw finish reason and request ID.
Unexpected tool/non-text content is a ProtocolError; nothing is silently discarded.

Errors are `ConduitError` instances with a stable category in `.name`, plus safe
optional `statusCode`, `providerCode`, `requestId`, `providerDetails`, `cause`.

| Condition | Error name |
| --- | --- |
| 401 | AuthenticationError |
| 403 | AuthorizationError |
| 400 / 422 or invalid local input | InvalidRequestError |
| 408 or Conduit deadline | TimeoutError |
| 429 | RateLimitError |
| 404 with exact provider code model_not_found | ModelNotFoundError |
| Other HTTP failures, including generic 404 / 5xx / redirects | ProviderError |
| Fetch/network/DNS/socket failure | ConnectionError |
| Malformed success or unhandled content | ProtocolError |
| Caller abort | CancelledError |
| Unimplemented common feature | UnsupportedCapabilityError |

Canonical JSON error message/type/code are preserved after redaction. Malformed,
plain-text, or empty error bodies retain a useful HTTP status error. Raw bodies
and native exception objects are not attached to errors. There are no retries.

## Timeout, cancellation, and native options

`timeout` is optional on config and each request; a request value overrides the
client default. Omission means no Conduit deadline. Values must be whole
milliseconds from 1 through 2147483647. The deadline covers HTTP connection and
complete body reads. Native transport timeouts may still apply independently.
Caller cancellation uses `signal: AbortSignal`; the first observed abort wins.
A caller's abort reason cannot misclassify cancellation as a timeout. Pre-aborted
calls perform no I/O. Timers and listeners are cleaned up after every operation.

`providerOptions` accepts acyclic JSON data and sends native fields unchanged,
without claiming common support for them. Reject collisions even if the common
option is omitted or equal. Reserved fields:
`model`, `messages`, `stream`, `stream_options`, `max_tokens`,
`max_completion_tokens`, `temperature`, `top_p`, `stop`, `tools`, `tool_choice`,
`functions`, `function_call`, `response_format`, `n`, `modalities`.
Exception: stream_options is allowed as a JSON object only for stream(), such as
`providerOptions: { stream_options: { include_usage: true } }`. Nothing is injected
automatically; generate() continues to reject stream_options.

Credentials are optional nonempty bearer-token strings captured privately, used
only for authorization. Custom headers cannot set authorization, proxy
authorization, cookies, host, content type/length, or connection/transfer headers.
Returned client/model objects contain no serializable configuration or secrets.
Known credential/custom-header values are redacted from provider diagnostics,
response identifiers, and diagnostic metadata. Generated semantic content is
never redacted, including text equal to configured secrets. Raw and
URL-encoded known values are redacted; unknown/transformed secrets cannot be
identified automatically. Conduit does not log. Caller-owned input/configuration
objects and application logging remain the caller's responsibility.

The [runnable example](../examples/typescript/generate.mjs) uses the compiled
package directly. Set CONDUIT_ENDPOINT and optionally CONDUIT_MODEL and
CONDUIT_API_KEY; run `node examples/typescript/generate.mjs` after building.

## Text streaming

```ts
for await (const event of model.stream({
  messages: [{ role: "user", content: "Hello" }],
  timeout: 10000,
})) {
  if (event.type === "text_delta") process.stdout.write(event.text);
  if (event.type === "done") console.log(event.response.usage);
}
```

`stream(request): AsyncGenerator<StreamEvent>` requires no extra await. Validation,
fetch, and the operation timeout start on first iteration. Events:

| Type | Fields |
| --- | --- |
| start | Optional id/model known at the first valid choice |
| text_delta | index: 0, nonempty text |
| usage | Cumulative normalized usage snapshot; missing fields remain absent |
| done | response: the same GenerationResponse used by generate() |

Exactly one start/done on success. IDs/model first reported later appear in done;
metadata holds only raw finish reason and request ID, never a chunk history.
Final text is accumulated once; response.text remains a view over content.
Concatenated text deltas agree with final text. Semantic output is passed through
unchanged, including text that matches known secrets; nothing is delayed for redaction.

Successful completion requires a valid single-choice stream, a string finish
reason, and a blank-line-terminated `[DONE]` event. A finish reason alone or EOF
alone fails with ProtocolError. LF/CRLF/CR, multiline data, comments, BOM, and
split UTF-8/JSON/SSE bytes are handled incrementally. Incomplete final SSE records
are discarded, not treated as completed events. Invalid UTF-8/JSON, conflicting
IDs/models, nonzero/multiple choices, text after finish, and meaningful tool or
other non-text deltas are ProtocolError. HTTP 200 still requires a valid event
stream. In-band JSON error envelopes raise ProviderError with safe diagnostics.

Timeout covers the whole stream, including consumer pauses; expiry after partial
output throws TimeoutError, without done. Caller AbortSignal produces
CancelledError; first abort wins. Body/network failures produce ConnectionError.
HTTP errors before events use the same mappings as generate(). No retry/reconnect.

`break` in for-await (or returning the iterator) cancels the reader and releases
resources silently. Timers/listeners/readers are cleaned up before yielding done,
so consuming another event is unnecessary. Native generator return queues behind
an outstanding next; use AbortSignal to interrupt that read. A dropped iterator
cannot be detected automatically: close it or supply a cancellation signal.

Buffering is limited to the current read/incomplete SSE event and accumulated
final text. There is no background queue or raw event
log. A single unterminated event can grow until termination/cancellation; no
arbitrary provider payload-size limit is imposed in this slice.

Run the [streaming example](../examples/typescript/stream.mjs) after building:
`CONDUIT_ENDPOINT=http://localhost:1234/v1 CONDUIT_MODEL=my-model node examples/typescript/stream.mjs`.
The test suite runs both examples locally and replays shared fragmentation cases.
