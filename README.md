# Conduit — lightweight AI driver (v0.1.0)

Conduit is a lightweight AI driver/library for talking to local and hosted model APIs.
One explicit `connect` → `model` → `generate`/`stream` per language, no framework.

Conduit is **not** an agent framework, orchestration, RAG, memory, prompt management, or workflow engine. It owns transport and normalization; your app owns history, tools execution, and retries.

**Product test:** can you add local or hosted AI to a new app in ~10 minutes without dragging in a framework? If yes, Conduit is working.

Drivers (v0): `openai-compatible` · `ollama` (native `/api/chat`) · `anthropic` (`/v1/messages`) · `gemini` (`/v1beta/models/...:generateContent`)

Languages: TypeScript/JavaScript (Node 22.13+ ESM, `fetch`), Python 3.10+ (stdlib `http.client`), Rust (sync `ureq` + `serde_json`)

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

Local Ollama (no key, explicit endpoint):
```ts
connect({ driver: "ollama", endpoint: "http://localhost:11434" }).model("llama3").generate({ messages: [{ role: "user", content: "Hello" }] })
```
```py
connect(driver="ollama", endpoint="http://localhost:11434").model("llama3").generate(messages=[{"role":"user","content":"Hello"}])
```
```rust
connect(ClientConfig { driver: "ollama".into(), endpoint: "http://localhost:11434".into(), ..Default::default() }).unwrap().model("llama3").unwrap().generate(req).unwrap()
```

Explicit driver + endpoint always required. `credentials` is `Authorization: Bearer` (OpenAI/Ollama) or `x-api-key` (Anthropic) / `x-goog-api-key` (Gemini). `providerOptions` is the escape hatch for native fields; Conduit-owned fields (`model`, `messages`, `stream`, `tools`, `format`/`response_format`, etc.) are rejected even when equal. No automatic routing, retries, or tool execution — tools are transport-only (`inputSchema` → provider `parameters`/`parametersJsonSchema`, `id` preserved, never fabricated).

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
