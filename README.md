# Conduit

[![npm](https://img.shields.io/npm/v/@krazybean/conduit?label=npm)](https://www.npmjs.com/package/@krazybean/conduit) [![PyPI](https://img.shields.io/pypi/v/conduit-llm?label=PyPI)](https://pypi.org/project/conduit-llm/) [![crates.io](https://img.shields.io/crates/v/conduit-ai?label=crates.io)](https://crates.io/crates/conduit-ai) [![License: MIT](https://img.shields.io/github/license/krazybean/Conduit)](LICENSE)

**A lightweight AI driver. Infrastructure, not a framework.**

Conduit provides one small explicit interface for local and hosted model APIs across TypeScript, Python, and Rust — without bringing in agents, orchestration, RAG, memory, or framework machinery.

```
Your application
      │
      ▼
   Conduit
      │
      ├── OpenAI-compatible
      ├── Ollama
      ├── Anthropic
      └── Gemini
```

Can a developer add local or hosted AI access to a new application in roughly ten minutes without adopting an AI framework? That is the design constraint.

## Install

| Language | Distribution | Install |
| --- | --- | --- |
| TypeScript / JavaScript | [`@krazybean/conduit`](https://www.npmjs.com/package/@krazybean/conduit) | `npm install @krazybean/conduit` |
| Python | [`conduit-llm`](https://pypi.org/project/conduit-llm/) | `pip install conduit-llm` |
| Rust | [`conduit-ai`](https://crates.io/crates/conduit-ai) | `cargo add conduit-ai` |

Python distribution is `conduit-llm`, but imports remain `conduit`. Rust distribution is `conduit-ai`, but the crate remains `conduit`.

## Quick Start

**TypeScript**

```ts
import { connect } from "@krazybean/conduit";

const model = connect({
  driver: "ollama",
  model: "qwen3:8b",
});

const response = await model.generate("Why is the sky blue?");
console.log(response.text);
```

**Python**

```python
from conduit import connect

model = connect(
    driver="ollama",
    model="qwen3:8b",
)

response = model.generate("Why is the sky blue?")
print(response.text)
```

**Rust**

```rust
use conduit::ollama;

fn main() {
    let model = ollama("qwen3:8b").unwrap();
    let response = model.generate("Why is the sky blue?").unwrap();
    println!("{}", response.text());
}
```

Ollama defaults to `http://localhost:11434` — no endpoint or key required. Other drivers need an endpoint (and credentials where required).

## Supported Drivers

| Driver | Local / Hosted |
| --- | --- |
| OpenAI-compatible | Both — any OpenAI Chat Completions-compatible base URL |
| Ollama | Local or remote Ollama (`/api/chat`) |
| Anthropic | Hosted (`/v1/messages`) |
| Gemini | Hosted (`/v1beta/models/...:generateContent`) |

## One API, explicit control

### Generate

The simple path is a string:

```ts
await model.generate("Why is the sky blue?")
```

Full `GenerationRequest` remains available when you need messages, tools, or output controls:

```ts
await model.generate({ messages: [{ role: "user", content: "Hello" }], temperature: 0.7 })
```

Same shape in Python (`messages=[...]`) and Rust (`GenerationRequest { messages: vec![...] }`).

### Stream

Equivalent streaming API in every language:

```ts
for await (const event of model.stream("Why is the sky blue?")) {
  if (event.type === "text_delta") process.stdout.write(event.text);
  if (event.type === "done") console.log(event.response.text);
}
```

Python: `for event in model.stream("..."): if event["type"] == "text_delta": ...`  
Rust: `for event in model.stream("...").unwrap() { ... }`

Events: `start`, `text_delta`, `tool_call_delta`, `usage`, `done` (with final `GenerationResponse`).

### Model discovery

```ts
const models = await client.listModels();
```

Python: `client.list_models()` · Rust: `client.list_models(None)`

### Tools

Provide tool schemas, receive normalized tool calls. Conduit never executes tools — the caller does:

```ts
await model.generate({
  messages: [{ role: "user", content: "What's the weather in Paris?" }],
  tools: [{ name: "weather", description: "Get weather", inputSchema: { type: "object", properties: { location: { type: "string" } } } }],
});
```

Response exposes `toolCalls`; next request sends `tool_result`.

### Structured output

Normalized across drivers:

- `text` — default, no format
- `json` — `json` / `application/json`
- `json_schema` — with `schema` (Ollama direct, Gemini `responseJsonSchema`, OpenAI `response_format`)

Provider enforcement differs; Anthropic structured output is currently `UnsupportedCapabilityError`.

### Provider escape hatch

Native fields without waiting for Conduit to abstract them:

```ts
providerOptions: { native_flag: true }
```

Conduit-owned fields (`model`, `messages`, `stream`, `tools`, `format`, etc.) are rejected even when equal.

## Errors

All failures normalize to `ConduitError` with a stable category:

`AuthenticationError` · `AuthorizationError` · `ConnectionError` · `TimeoutError` · `RateLimitError` · `InvalidRequestError` · `UnsupportedCapabilityError` · `ModelNotFoundError` · `ProviderError` · `ProtocolError` · `CancelledError`

Timeout is one operation deadline (1..2147483647 ms), not per-chunk. Cancellation via `signal` / `AbortSignal` / `AtomicBool`.

## What Conduit deliberately does not do

Conduit intentionally does not provide:

- agents
- orchestration
- RAG
- memory
- prompt management
- automatic tool execution
- model routing
- automatic fallback
- workflow engines

Those belong to the application or higher-level libraries. Conduit stays focused on model access.

## Why Conduit

Can a developer add local or hosted AI access to a new application in roughly ten minutes without adopting an AI framework? If yes, Conduit is working.

## Deeper documentation

- TypeScript: [typescript/README.md](typescript/README.md)
- Python: [python/README.md](python/README.md)
- Rust: [rust/README.md](rust/README.md)
- Specification: [spec/README.md](spec/README.md)
- Conformance fixtures: [conformance/](conformance/)
- Examples: [examples/](examples/)

License: MIT
