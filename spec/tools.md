# Tools: schemas in, calls out

`ToolDefinition`: required `name`, optional `description`, required `input_schema`
(JSON Schema data supplied by the caller). Names must be nonempty and unique in
one request. Do not require schema libraries or introspect functions.

`ToolCall`: optional provider `id`, required `name`, and `arguments` as decoded
JSON data. Do not fabricate IDs. Malformed complete JSON arguments from a provider
are `ProtocolError`; incomplete streaming fragments are not parsed prematurely.
Schema validation of arguments belongs to the caller.

`ToolResult`: optional `call_id`, optional `name`, and `content` consisting of text
or image parts (string shorthand allowed). No recursive tool calls/results inside
a result. Return results through tool-role messages. Preserve correlation IDs;
if the protocol needs an ID/name and the caller omitted it, report
`InvalidRequestError`, not a guessed association.

`tool_choice` semantically allows `auto`, `none`, `required`, or a named tool.
A selected name must be present in the supplied definitions. Unsupported choice
modes raise `UnsupportedCapabilityError`. Exact language syntax is provisional.

Conduit never accepts function pointers as its core tool abstraction, executes
tools, manages loops, retries execution, or maintains tool registries.
