# Messages and content

`Message` consists of `role` and ordered `content[]`. Common roles are `system`,
`user`, `assistant`, and `tool`. No common `developer` role. String content is an
input shorthand for one text part, including an empty string. Preserve message
and content order; do not merge or discard user input to fit a provider.

Initial semantic `ContentPart` variants:

| Kind | Data |
| --- | --- |
| `text` | `text`: string |
| `tool_call` | `id?`, `name`, `arguments`: see [tools](tools.md) |
| `tool_result` | `call_id?`, `name?`, `content`: see [tools](tools.md) |
| `image` | An explicit URL or inline bytes with media type |

The fixture discriminator is `type`. Image source spelling/byte encoding is
provisional until vision fixtures exist; native bytes need not be base64 in
language APIs. Do not fetch image URLs behind the caller's back. Content is
never internally limited to a string.

Tool calls belong to assistant output and supplied assistant history; tool
results use tool messages. Drivers map role/content combinations explicitly.
Invalid combinations are `InvalidRequestError`; valid content the driver cannot
represent is `UnsupportedCapabilityError`. No silent flattening of images or
tool content into text. Unknown/malformed provider content must not be silently
lost; mapping must either preserve it explicitly or raise `ProtocolError`.
