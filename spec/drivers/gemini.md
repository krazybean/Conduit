# Gemini driver

Driver ID: `gemini`. Target the Gemini Interactions API for the eventual native
driver. Do not substitute generateContent or an OpenAI-compatible protocol.

Use full caller-supplied history for each request, `store=false`, and no
`previous_interaction_id`. Disallow provider options that enable server-side
conversation chaining or background/agent execution; these violate Conduit's
stateless request contract. No sessions, managed agents, polling workflows, or
automatic tool execution. This is not a claim about provider-wide data retention.

Google's [Interactions documentation](https://ai.google.dev/gemini-api/docs/interactions-overview)
was checked on 2026-09-07: storage defaults on, explicit `store=false` opts out,
and server-managed history is optional. Recheck wire/version details when this
driver is implemented; no SDK or API-version machinery is needed in the scaffold.

Map native content, tool calls, streams, usage, and errors through shared semantic
fixtures. Keep reasoning/safety settings native. Establish model-listing support
and model evidence explicitly; never claim it based on protocol fields alone.
No implementation in this scaffold.
