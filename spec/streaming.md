# Streaming

`model.stream(request)` exposes an idiomatic iterator/async iterator of normalized
events, not token callbacks. It shares request validation, native options,
timeout/cancellation, and error taxonomy with non-streaming generation.

| Event | Semantic payload |
| --- | --- |
| `start` | Optional provider response ID/model |
| `text_delta` | Content index and appended text |
| `tool_call_delta` | Content index and optional ID/name/argument fragments |
| `usage` | Reported usage snapshot |
| `done` | Final fully accumulated `GenerationResponse` |

Indexes identify content positions, not provider tool IDs. Emit exactly one start
before content on successful streams and exactly one terminal done; no events
follow done. Empty successful output may go directly from start to done.
Failures may occur before start or after partial output; raise a normalized error
and emit no done. Consumers must not mistake partial output for success.

Text deltas append in order. Tool argument fragments are strings accumulated by
content index and decoded as JSON only when complete. ID/name fragment mapping
must be specified per driver before tool streaming ships; never mix concurrent
calls. The done response is authoritative, including final ordered content,
finish reason, and usage. Its text/tool-call views match non-streaming semantics.

Usage events are cumulative snapshots, not values to sum. Providers sending
increments must be accumulated by the driver. Absent fields do not reset known
counts; absent usage remains absent. Preserve native details in final metadata.

Incremental wire parsers must handle arbitrary byte fragmentation: split UTF-8,
JSON records, SSE lines/delimiters, and tool arguments, as well as several records
in one read. Network reads are not event boundaries. EOF before the protocol's
required completion is `ProtocolError`; do not fabricate done. Done follows all
required trailing usage records. Driver fixtures define completion signals.

Release resources on completion, failure, timeout, cancellation, or abandoned
iteration. There is no separate `error` event or automatic reconnection/retry.
Image streaming is not specified yet; unsupported content must fail explicitly,
not disappear from the final response.
