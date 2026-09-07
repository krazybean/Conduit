# Structured output

`ResponseFormat` has a `type`: `text`, `json`, or `json_schema`.
`json_schema` additionally carries `schema` as caller-supplied JSON Schema data.
`text` requests ordinary output; `json` requests JSON without a supplied schema.
Omission uses provider defaults. Schema options specific to one driver stay in
non-conflicting provider options; exact wire mappings need driver fixtures.

Conduit asks the provider for the requested format. It does not generate schemas,
validate model output against JSON Schema, or require Zod, Pydantic, or serde
schema generation. Generated JSON remains text content; callers may parse it.
An unrepresentable requested format raises `UnsupportedCapabilityError`.
Aggregate `structured_output` support does not promise every schema dialect or
mode; validate the particular requested mode independently.
