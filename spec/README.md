# Conduit specification — draft v0

This language-neutral contract is authoritative; implementations must have
equivalent observable behavior, not identical syntax. The TypeScript OpenAI-compatible text generation and streaming slices are implemented;
other operations and languages remain planned. MUST/MUST NOT state requirements; proposals explicitly marked
provisional can be refined with concrete wire fixtures before implementation.
Snake-case field names describe semantics, not mandatory language spellings.
`?` means optional; omission means absent, not null or a fabricated default.

| Document | Contract |
| --- | --- |
| [Client and models](client-and-models.md) | Configuration, discovery, selection, secrets, lifecycle |
| [Generation](generation.md) | Requests, responses, usage, native options |
| [Messages](messages.md) | Roles and ordered content |
| [Streaming](streaming.md) | Events, accumulation, termination |
| [Tools](tools.md) | Schemas, calls, caller-supplied results |
| [Structured output](structured-output.md) | Text, JSON, caller schemas |
| [Capabilities](capabilities.md) | Tri-state evidence and effective support |
| [Errors](errors.md) | Stable categories and safe diagnostics |
| [OpenAI-compatible](drivers/openai-compatible.md) | Chat Completions boundary |
| [Ollama](drivers/ollama.md) | Native API boundary |
| [Anthropic](drivers/anthropic.md) | Messages API boundary |
| [Gemini](drivers/gemini.md) | Stateless Interactions boundary |

## Architecture

A client holds provider connection settings; a selected model holds a client
reference and explicit model identifier/configuration. Construction and selection
are local. Generation flows from caller input through driver encoding, HTTP,
and driver decoding into normalized content or normalized errors. Streaming adds
incremental decoding and accumulation. No public transport/factory/registry
hierarchy is needed. Internal HTTP reuse is allowed, application state is not.

Conduit owns transport and normalization, never application decisions. The full
exclusion list and scope test live in [CONTRIBUTING](../CONTRIBUTING.md).
One package per language, no provider SDKs, schema ecosystem, IDL, or codegen.
TypeScript should use native fetch, Python initially stdlib HTTP/JSON, Rust only
justified HTTP/TLS and serialization dependencies when implemented.

## Decisions still provisional

TypeScript now uses camelCase names, two connect overloads, Node 22.13+ ESM,
opt-in timeouts, and max_tokens mapping. These decisions are documented in the
client and driver specs and tested with shared fixtures. Other language API
spellings, packaging, and transport cleanup syntax remain implementation choices.
Multimodal wire mapping, tool argument delta details, and discovery pagination
need fixtures before their respective slices. No known human decision blocks
the first non-streaming TypeScript slice. Release names are a later decision.
