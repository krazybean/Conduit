# Conduit

A lightweight, dependency-minimal AI model driver library for local and hosted
providers. Small, explicit model access in the spirit of `requests` or `pg`.

**Implemented:** TypeScript/JavaScript OpenAI-compatible Chat Completions text
generation and streaming. Python, Rust, and other drivers remain scaffolds.
The package is local/private; nothing is published yet.

```ts
import { connect } from "./typescript/dist/index.js";

const model = connect({
  driver: "openai-compatible",
  endpoint: "http://localhost:1234/v1",
  model: "my-model",
  credentials: process.env.CONDUIT_API_KEY, // optional for local endpoints
});
const response = await model.generate({
  messages: [{ role: "user", content: "Hello" }],
});
console.log(response.text);
```

Stream using the same model and request options:

```ts
for await (const event of model.stream({ messages: [{ role: "user", content: "Hello" }] })) {
  if (event.type === "text_delta") process.stdout.write(event.text);
  // event.type === "done" exposes the final GenerationResponse in event.response.
}
```

Or create a client without a model or network request, then select locally:

```ts
const client = connect({ driver: "openai-compatible", endpoint: "http://localhost:1234/v1" });
const model = client.model("my-model");
```

Use Node 22.13+ and ESM. From the repository root:

```sh
npm ci --prefix typescript
npm test --prefix typescript
CONDUIT_ENDPOINT=http://localhost:1234/v1 CONDUIT_MODEL=my-model node examples/typescript/generate.mjs
```

Tests use only local mock HTTP servers and synthetic credentials. The example
requires a running compatible endpoint. See the [TypeScript API](typescript/README.md)
for options, errors, cancellation, and endpoint semantics.

Conduit connects to providers and normalizes generation, content, usage, and
errors. Applications own conversation history. Discovery, transport-only
tools, structured output, and additional drivers are future slices. Conduit does
not provide agents, routing, fallback, RAG, memory, workflows, or retries.

The [language-neutral specification](spec/README.md) is the source of truth.
TypeScript/JavaScript, Python, and Rust will share observable behavior with
idiomatic APIs, one package per language. Initial driver targets remain Chat
Completions, native Ollama, Anthropic Messages, and stateless Gemini Interactions.

| Directory | Purpose |
| --- | --- |
| `spec/` | Common semantics and driver boundaries |
| `conformance/` | Shared request, response, error, and byte-fragment stream fixtures |
| `typescript/` | Working text-generation slice and local-server tests |
| `python/`, `rust/` | Empty language project scaffolds |
| `examples/` | Runnable JavaScript examples; other languages reserved |

[Contributing](CONTRIBUTING.md) requires Ponytail for every task. Zero runtime
dependencies; TypeScript is the only development dependency. Existing
[MIT license](LICENSE) retained. Registry names/release configuration remain open.
