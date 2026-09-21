# Conduit specification — draft v0

This language-neutral contract is authoritative; implementations must have
equivalent observable behavior, not identical syntax. TypeScript, Python, and
Rust implementations are published and cover the current Conduit surface across
OpenAI-compatible, Ollama, Anthropic, and Gemini drivers: model discovery,
generation, streaming, tools, structured-output handling where the provider
supports it, normalized usage/errors, timeouts, cancellation, and provider
escape hatches. MUST/MUST NOT state requirements; proposals explicitly marked
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
TypeScript uses native `fetch`, Python uses stdlib HTTP/JSON, and Rust uses only
justified HTTP/TLS and serialization dependencies.

## Decisions still provisional

Public language APIs are intentionally idiomatic rather than syntax-identical,
while observable behavior remains aligned through the shared specification and
conformance fixtures. TypeScript uses camelCase names, two connect overloads,
Node 22.13+ ESM, opt-in timeouts, and max_tokens mapping. Python requires 3.10+
and uses stdlib `http.client` without runtime dependencies. Rust exposes the
`conduit` library from the `conduit-ai` package and uses a synchronous transport.

Multimodal wire mapping, finer-grained tool argument delta behavior, discovery
pagination, and other future slices still require concrete fixtures before their
contracts are promoted from provisional. New behavior should update this spec
and shared conformance cases before or with implementation.
