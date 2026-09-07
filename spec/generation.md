# Generation

`model.generate(GenerationRequest)` performs a single stateless operation for
that selected model. No routing, model selection, fallback, or retries.

| Request field | Meaning |
| --- | --- |
| `messages` | Ordered messages; required |
| `max_output_tokens?` | Positive integer output budget |
| `temperature?` | Finite sampling value, validated for selected protocol |
| `top_p?` | Finite nucleus-sampling value, validated for selected protocol |
| `stop?` | List of stop strings |
| `tools?` | Caller-provided tool definitions |
| `tool_choice?` | Auto, none, required, or one named supplied tool |
| `response_format?` | Text, JSON, or JSON Schema |
| `provider_options?` | Explicit driver-native request body settings |

Timeout/cancellation are operation controls, not provider body fields. Drivers
must validate input shape and representability before sending. Never silently
drop a requested option. A known unsupported feature raises
`UnsupportedCapabilityError`; invalid values/conflicts raise `InvalidRequestError`.
Unknown model support may be attempted when the protocol can represent it.
Conduit does not invent sampling defaults; omission lets the provider decide.

## Provider options

Reasoning controls, safety, top_k, penalties, seed, keep-alive, service tier,
cache hints, and thinking parameters stay native unless evidence warrants a
common field. Options are JSON-compatible data interpreted by the selected driver.

Conduit-owned wire fields cannot be overridden, even if the normalized option is
omitted or the supplied value is equal. Each driver must reserve its mapped
field names/nested paths, including model, message/input content, stream mode,
tools, tool choice, response format, and common sampling/token fields. A collision
is `InvalidRequestError`, never a merge-order decision. Drivers may also reserve
fields necessary to preserve statelessness/security. Non-conflicting options
pass through; do not promise the provider will accept them. Custom headers are
connection settings, not request body options. Authentication remains protected.

## Responses

`GenerationResponse` has `id?`, `model?`, ordered `content[]`, `finish_reason`,
`usage?`, and `provider_metadata?`. Missing provider IDs/model names stay absent;
do not invent remote values. `text` concatenates text parts in content order;
`tool_calls` selects tool-call parts. These are views, not independent storage.

Finish reasons: `stop`, `length`, `tool_call`, `content_filter`, `cancelled`,
`other`. Preserve an unfamiliar native reason in provider metadata. `cancelled`
can describe a provider-reported completed response; explicit caller cancellation
raises `CancelledError`. A malformed/incomplete response is `ProtocolError`, not
a successful empty response. Single normalized responses represent one output;
multiple candidates are not a common operation and must not be silently discarded.

`Usage` has optional nonnegative integer `input_tokens`, `output_tokens`, and
`total_tokens`. Missing means not reported, never zero. Do not synthesize a total
from partial counts. Provider cache/reasoning/token breakdowns remain metadata.
Metadata and errors must obey credential redaction requirements.
