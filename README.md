# Conduit — lightweight AI driver

Conduit is a lightweight driver/library for local and hosted model APIs — lightweight AI access without adopting an AI framework. One explicit `connect` → `model` → `generate`/`stream` per language, no framework.

Conduit owns model transport and normalization. The application owns history, tool execution, retries, orchestration, etc.

**Product test:** can you add local or hosted AI to a new app in ~10 minutes without dragging in a framework? If yes, Conduit is working.

## Quick Start

Ollama is the zero-ceremony local path — no endpoint or API key required. Simple string `generate("Hello")` is the 80% case; full `GenerationRequest` remains available.

```python
# Python — Ollama (no endpoint)
from conduit import connect

model = connect(driver="ollama", model="qwen3:8b")
print(model.generate("Hello").text)

for event in model.stream("Hello"):
    if event["type"] == "text_delta":
        print(event["text"], end="")
```

```ts
// TypeScript — Ollama (no endpoint)
import { connect } from "conduit";

const model = connect({ driver: "ollama", model: "qwen3:8b" });
console.log((await model.generate("Hello")).text);

for await (const event of model.stream("Hello")) {
  if (event.type === "text_delta") process.stdout.write(event.text);
}
```

```rust
// Rust — Ollama (no endpoint)
use conduit::{connect, ClientConfig};

let model = connect(ClientConfig { driver: "ollama".into(), model: Some("qwen3:8b".into()), ..Default::default() }).unwrap();
println!("{}", model.generate("Hello").unwrap().text());

for event in model.stream("Hello").unwrap() {
    println!("{:?}", event.unwrap());
}
```

Hosted and other drivers need an endpoint (and credentials where required):

```python
from conduit import connect
client = connect(driver="openai-compatible", endpoint="http://localhost:1234/v1", model="my-model")
print(client.generate("Hello").text)
```

```ts
import { connect } from "conduit";
const model = connect({ driver: "openai-compatible", endpoint: "http://localhost:1234/v1", model: "my-model" });
console.log((await model.generate("Hello")).text);
```

Drivers (v0): `openai-compatible` · `ollama` (native `/api/chat`) · `anthropic` (`/v1/messages`) · `gemini` (`/v1beta/models/...:generateContent`)

Languages: TypeScript/JavaScript (Node 22.13+ ESM, `fetch`), Python 3.10+ (stdlib `http.client`), Rust (sync `ureq` + `serde_json`)

## Full API

Explicit `messages` and options still work — use them when you need tools, history, or structured output:

```ts
// TypeScript — OpenAI-compatible
import { connect } from "./typescript/dist/index.js";
const client = connect({ driver: "openai-compatible", endpoint: "http://localhost:1234/v1", credentials: process.env.CONDUIT_API_KEY });
console.log(await client.listModels());
const model = client.model("my-model");
const res = await model.generate({ messages: [{ role: "user", content: "Hello" }] });
console.log(res.text, res.usage);
for await (const e of model.stream({ messages: [{ role: "user", content: "Hello" }] })) {
  if (e.type === "text_delta") process.stdout.write(e.text);
  if (e.type === "done") console.log("\nfinish:", e.response.finishReason);
}
```

```py
# Python — OpenAI-compatible
from conduit import connect
client = connect(driver="openai-compatible", endpoint="http://localhost:1234/v1", credentials=None)
print(client.list_models())
model = client.model("my-model")
res = model.generate(messages=[{"role": "user", "content": "Hello"}])
print(res.text, res.usage)
for ev in model.stream(messages=[{"role": "user", "content": "Hello"}]):
    if ev["type"] == "text_delta": print(ev["text"], end="")
```

```rust
// Rust — OpenAI-compatible (sync)
use conduit::{connect, ClientConfig, GenerationRequest, Message, ContentPart, TextPart};
let client = connect(ClientConfig { driver: "openai-compatible".into(), endpoint: "http://localhost:1234/v1".into(), ..Default::default() }).unwrap();
println!("{:?}", client.list_models(None).unwrap());
let model = client.model("my-model").unwrap();
let res = model.generate(GenerationRequest { messages: vec![Message { role: "user".into(), content: vec![ContentPart::Text(TextPart { part_type: "text".into(), text: "Hello".into() })] }], ..Default::default() }).unwrap();
println!("{} {:?}", res.text(), res.usage);
for ev in model.stream(GenerationRequest { messages: vec![Message { role: "user".into(), content: vec![ContentPart::Text(TextPart { part_type: "text".into(), text: "Hello".into() })] }], ..Default::default() }).unwrap() { println!("{:?}", ev.unwrap()); }
```

`credentials` is `Authorization: Bearer` (OpenAI/Ollama) or `x-api-key` (Anthropic) / `x-goog-api-key` (Gemini). `providerOptions` is the escape hatch for native fields; Conduit-owned fields (`model`, `messages`, `stream`, `tools`, `format`/`response_format`, etc.) are rejected even when equal. No automatic routing, retries, or tool execution — tools are transport-only (`inputSchema` → provider `parameters`/`parametersJsonSchema`, `id` preserved, never fabricated).

Structured output: `responseFormat: {type:"text"}` (omit), `{type:"json"}` (`json`/`application/json`), `{type:"json_schema", jsonSchema}` (Ollama direct schema, Gemini `responseJsonSchema`, OpenAI `response_format`). Anthropic structured output remains `UnsupportedCapabilityError`.

Errors are normalized `ConduitError` with `name` categories: `InvalidRequestError`, `AuthenticationError`, `AuthorizationError`, `ModelNotFoundError`, `RateLimitError`, `TimeoutError`, `ConnectionError`, `ProtocolError`, `ProviderError`, `UnsupportedCapabilityError`, `CancelledError`.

Timeout is one operation deadline (1..2147483647 ms), not per-chunk; pagination and streaming respect the same deadline. Cancellation via `signal`/`AbortSignal`/`AtomicBool`.

Spec is authoritative: [spec/README.md](spec/README.md). Conformance fixtures in [conformance/](conformance/).

| Dir | Purpose |
| --- | --- |
| `spec/` | Semantics, drivers |
| `conformance/` | Request/response/stream/error fixtures |
| `typescript/` | `conduit` npm package, `npm run build && node --test test/*.test.mjs` |
| `python/` | `conduit` pip package, `PYTHONPATH=python:python/tests python3 -m unittest discover -s python/tests -v` (66 tests) |
| `rust/` | `conduit` crate, `cargo test -- --include-ignored` (59 tests) |
| `examples/` | Runnable TS/Python/Rust for OpenAI-compatible + Ollama |

[Contributing](CONTRIBUTING.md) and [AGENTS.md](AGENTS.md) require exact lowercase `ponytail` for every task. License: MIT.
