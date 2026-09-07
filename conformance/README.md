# Shared conformance fixtures

This area holds language-neutral JSON fixtures for all three implementations.
TypeScript currently consumes the OpenAI-compatible text fixtures directly in
its local HTTP tests. Python/Rust consumers will follow the same cases. JSON keys use the spec's snake_case semantic spelling.

| Directory | Intended cases |
| --- | --- |
| `requests/` | Normalized input/config → expected provider HTTP request |
| `responses/` | Provider status/headers/body → expected normalized response |
| `streams/` | Provider byte fragments → expected normalized event sequence |
| `errors/` | Local validation, HTTP/network/protocol/timeout/cancellation failures |
| `capabilities/` | Protocol/model evidence → effective tri-state capabilities |

A file contains one case or an array of related cases. Each case has `id`, optional `driver`, `operation`, `input`, and `expected`.
Driver-specific input may include `client_config`, `model`, `request`, and `wire`.
Wire requests/responses describe method/path or status, headers, and body.
Expected results use `wire_request`, `response`, `events`, `error`, or
`capabilities` as appropriate to the case. Error expectations include category
and safe factual fields, not platform-specific stack traces. Operations name
semantic actions such as `generate`, `stream`, `list_models`, or `capabilities`.

Illustrative envelope (not an executable fixture):

```json
{
  "id": "empty-capability-evidence",
  "operation": "capabilities",
  "input": {"protocol": {}, "model": {}},
  "expected": {"capabilities": {"tools": "unknown"}}
}
```

Wire JSON bodies use objects; malformed/non-JSON bodies use `body_text` or
`body_base64`, exclusively. Streams use ordered `fragments_base64` entries so
splits inside UTF-8 code points are representable; each decodes to one read's
bytes. Specify expected events and, for failures, a terminal normalized error
with no done. Chunk partitioning must not affect semantic output. Include split
SSE delimiters, multiple records per chunk, split JSON/tool arguments, premature
EOF, and trailing usage when streaming lands.

Object key order is irrelevant; array order and string content are significant.
Absent fields stay absent; null/zero are not substitutes. Compare usage exactly,
never reconstruct missing values. Use synthetic credentials/IDs and local fake
endpoints only. Test redaction with synthetic secrets. Match normalized views
from content, not duplicated mutable text/tool-call storage. Shared fixtures
are the behavioral oracle; language runners may add native cancellation and
resource-cleanup checks. No IDL, code generation, or schema framework.

Implemented files: `requests/openai-text.json` (one request),
`responses/openai-text.json` (six responses), and `errors/openai-http.json`
(thirteen HTTP errors). These synthetic wire examples are regression contracts,
not claims of certification against any live provider. Run all 20 cases with
`npm test --prefix typescript`. Streams/capabilities remain placeholders.
