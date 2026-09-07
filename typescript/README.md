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
`GenerationResponse`, `Usage`, `ErrorCode`, `ErrorDetails`.

Clients expose only `.model(id)`; selected models expose only `.generate(request)`.
Construction/selection performs no I/O. No listModels, stream, capabilities,
tools, structured-output, image, or reasoning API is implemented. Unsupported
common feature requests fail explicitly; unknown fields are invalid requests.

`endpoint` is an HTTP(S) **API base URL**, including any version prefix. Trailing
slashes are removed and `/chat/completions` is appended. `/v1` is never guessed:
`http://host/custom/v1/` becomes `http://host/custom/v1/chat/completions`.
Userinfo, query, and fragment are rejected. No provider autodetection or redirects.

Messages support system/user/assistant with strings or ordered text-part arrays.
Strings become one text part on the wire. Parameters map to `max_tokens`,
`temperature`, `top_p`, and `stop`; `stream` is always false. Token budgets are
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

Credentials are optional nonempty bearer-token strings captured privately, used
only for authorization. Custom headers cannot set authorization, proxy
authorization, cookies, host, content type/length, or connection/transfer headers.
Returned client/model objects contain no serializable configuration or secrets.
Known credential/custom-header values are redacted from provider diagnostics,
response identifiers, and returned text if a provider echoes them. Raw and
URL-encoded known values are redacted; unknown/transformed secrets cannot be
identified automatically. Conduit does not log. Caller-owned input/configuration
objects and application logging remain the caller's responsibility.

The [runnable example](../examples/typescript/generate.mjs) uses the compiled
package directly. Set CONDUIT_ENDPOINT and optionally CONDUIT_MODEL and
CONDUIT_API_KEY; run `node examples/typescript/generate.mjs` after building.
