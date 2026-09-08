# Ollama native driver

Driver ID: `ollama`. Native text generation and NDJSON streaming use POST
`/api/chat`. The public client/model and generate/stream APIs are unchanged.
Endpoint is an explicit HTTP(S) server base (e.g. http://localhost:11434); trim
trailing slashes and append /api/chat, preserving custom base paths. Reject
userinfo, query, and fragment. No autodetection, construction I/O, or redirects.
Optional bearer credentials/custom headers use the existing protection rules.

## Request mapping

Text-only system/user/assistant messages map to native string content. Concatenate
ordered text parts without separators; no images/tools are flattened. The selected
model and operation-owned stream flag are explicit. Common fields map as follows:

| Common | Ollama wire |
| --- | --- |
| max_output_tokens | options.num_predict |
| temperature | options.temperature |
| top_p | options.top_p |
| stop | options.stop |

Positive safe integer output budgets, finite nonnegative temperature, top_p in
0–1, and arrays of stop strings are accepted. Omitted values stay omitted.
Provider options are acyclic JSON. Reserve model/messages/stream/tools/format and
options.num_predict/temperature/top_p/stop, even if the common value is absent or
equal. Only one shallow options merge is needed. Non-conflicting native fields
such as keep_alive or options.num_ctx pass through. No think/reasoning API or
implicit think setting is added; native think:false may be supplied explicitly.

## Response and completion

Require done:true for generate. Assistant message.content is a string, preserved
unchanged even if it matches a secret. Model is optional, no response ID is
invented. Stop/length reasons map directly; other or omitted done_reason maps to
other. Only reported native reasons appear as providerMetadata.finishReason.
Usage maps prompt_eval_count to input_tokens and eval_count to output_tokens;
missing means absent. **Do not compute total_tokens**, even if both counts exist.
Timing fields total_duration/load_duration/prompt_eval_duration/eval_duration
remain native nanosecond fields in provider metadata, alongside created_at and
requestId when supplied. Diagnostics are redacted, content is not.

Native stream records require boolean done and valid assistant text messages;
a terminal done:true record may omit message only after a valid message. Reported
model identifiers must not change. Emit start once, nonempty text_delta (index 0),
usage snapshots only for reported counts, and done with the shared final response.
A valid done:true record completes the operation without an OpenAI finish marker
or a required done_reason. Discard subsequent bytes by cancelling the reader.

Use a separate incremental NDJSON reader: LF/CRLF, multiple records per read,
split JSON/UTF-8/newlines, and a complete final EOF record without newline work.
Ignore blank lines. Invalid UTF-8/JSON, partial final records, and EOF without
valid done:true are ProtocolError. Stream media types application/x-ndjson,
application/ndjson, and application/json are accepted. Meaningful tool, image,
or thinking content is ProtocolError in this text-only slice, never silently lost.
Empty thinking strings/tool/image arrays do not carry semantic output.

Timeout, cancellation, first-abort-wins, early-break cleanup, and diagnostic
redaction reuse the existing operation lifecycle. No retry or reconnect.
HTTP mappings remain shared. Native error strings are preserved safely. A 404
is ModelNotFoundError only for the exact native missing-model message naming the
selected model (single/double quotes, including the older pull hint); generic
404 remains ProviderError. In-band error strings are ProviderError, with no done.

## Model listing

`client.listModels()` uses `GET /api/tags` relative to the server base (e.g. `http://localhost:11434/api/tags`), preserving custom base paths and rejecting userinfo/query/fragment like generation. No request body. Map each entry's `name` (preferred) or `model` to `ModelInfo.id` so the returned id is usable with `client.model(id)`; `name` is echoed when present. Remaining fields (`modified_at`, `size`, `digest`, `details` including `family`, `families`, `parameter_size`, `quantization_level`, `format`) stay in `providerMetadata`. Missing `models` array or missing string identifier is `ProtocolError`; an empty array is valid and returns `[]`. HTTP errors reuse the shared categories (`401`→`AuthenticationError`, `403`→`AuthorizationError`, `429`→`RateLimitError`, `5xx`→`ProviderError`, `404` with exact native missing-model message →`ModelNotFoundError` otherwise `ProviderError`). Timeouts, cancellation, first-abort-wins, and redaction reuse the generation lifecycle; credentials are not required for listing.

## Evidence and scope

Checked against the official [chat API](https://docs.ollama.com/api/chat),
[streaming](https://docs.ollama.com/api/streaming),
[errors](https://docs.ollama.com/api/errors),
[option meanings](https://docs.ollama.com/modelfile),
[listing](https://github.com/ollama/ollama/blob/main/docs/api.md#list-local-models), and
[server error handling](https://github.com/ollama/ollama/blob/main/server/routes.go).
Fixtures are synthetic regression contracts, not live provider certification.
Remote capabilities, tools, structured output, vision,
embeddings, model pulling, and lifecycle management remain outside this slice.
