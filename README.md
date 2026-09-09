# Conduit — lightweight AI driver

Conduit is a lightweight driver/library for local and hosted model APIs — lightweight AI access without adopting an AI framework. One explicit `connect` → `model` → `generate`/`stream` per language, no framework.

Conduit owns model transport and normalization. The application owns history, tool execution, retries, orchestration, etc.

**Product test:** can you add local or hosted AI to a new app in ~10 minutes without dragging in a framework? If yes, Conduit is working.

Drivers (v0): `openai-compatible` · `ollama` (native `/api/chat`) · `anthropic` (`/v1/messages`) · `gemini` (`/v1beta/models/...:generateContent`)

## TypeScript / JavaScript

```sh
npm install @krazybean/conduit
```

Requires Node 22.13+ ESM. Zero runtime dependencies.

```ts
import { connect } from "@krazybean/conduit";

const model = connect({ driver: "ollama", model: "qwen3:8b" });
console.log((await model.generate("Hello")).text);
```

More: [typescript/README.md](typescript/README.md)

## Python

```sh
pip install conduit-ai
```

Requires Python 3.10+. Zero runtime dependencies. Import stays `conduit`.

```python
from conduit import connect

model = connect(driver="ollama", model="qwen3:8b")
print(model.generate("Hello").text)
```

More: [python/README.md](python/README.md)

## Rust

```sh
cargo add conduit-ai
```

Sync, `ureq` + `serde_json` only. Package `conduit-ai` exposes library `conduit`.

```rust
use conduit::ollama;

let model = ollama("qwen3:8b").unwrap();
println!("{}", model.generate("Hello").unwrap().text());
```

More: [rust/README.md](rust/README.md)

## Full API

Explicit `messages` and options still work — use them when you need tools, history, or structured output. Simple `generate("Hello")` is the 80% case; full `GenerationRequest` remains available.

```ts
import { connect } from "@krazybean/conduit";
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

```python
from conduit import connect
client = connect(driver="openai-compatible", endpoint="http://localhost:1234/v1")
model = client.model("my-model")
res = model.generate(messages=[{"role": "user", "content": "Hello"}])
print(res.text, res.usage)
for ev in model.stream(messages=[{"role": "user", "content": "Hello"}]):
    if ev["type"] == "text_delta": print(ev["text"], end="")
```

```rust
use conduit::{connect, ClientConfig, GenerationRequest, Message, ContentPart, TextPart};
let client = connect(ClientConfig { driver: "openai-compatible".into(), endpoint: "http://localhost:1234/v1".into(), ..Default::default() }).unwrap();
let model = client.model("my-model").unwrap();
let res = model.generate(GenerationRequest { messages: vec![Message { role: "user".into(), content: vec![ContentPart::Text(TextPart { part_type: "text".into(), text: "Hello".into() })] }], ..Default::default() }).unwrap();
println!("{} {:?}", res.text(), res.usage);
```

`credentials` is `Authorization: Bearer` (OpenAI/Ollama) or `x-api-key` (Anthropic) / `x-goog-api-key` (Gemini). `providerOptions` is the escape hatch for native fields; Conduit-owned fields (`model`, `messages`, `stream`, `tools`, `format`/`response_format`, etc.) are rejected even when equal. No automatic routing, retries, or tool execution — tools are transport-only (`inputSchema` → provider `parameters`/`parametersJsonSchema`, `id` preserved, never fabricated).

Structured output: `responseFormat: {type:"text"}` (omit), `{type:"json"}` (`json`/`application/json`), `{type:"json_schema", jsonSchema}` (Ollama direct schema, Gemini `responseJsonSchema`, OpenAI `response_format`). Anthropic structured output remains `UnsupportedCapabilityError`.

Errors are normalized `ConduitError` with `name` categories: `InvalidRequestError`, `AuthenticationError`, `AuthorizationError`, `ModelNotFoundError`, `RateLimitError`, `TimeoutError`, `ConnectionError`, `ProtocolError`, `ProviderError`, `UnsupportedCapabilityError`, `CancelledError`.

Timeout is one operation deadline (1..2147483647 ms), not per-chunk; streaming respects the same deadline. Cancellation via `signal`/`AbortSignal`/`AtomicBool`.

Spec is authoritative: [spec/README.md](spec/README.md). Conformance fixtures in [conformance/](conformance/).

| Dir | Purpose |
| --- | --- |
| `spec/` | Semantics, drivers |
| `conformance/` | Request/response/stream/error fixtures |
| `typescript/` | `conduit` npm package |
| `python/` | `conduit` pip package |
| `rust/` | `conduit` crate |
| `examples/` | Runnable TS/Python/Rust for OpenAI-compatible + Ollama |

License: MIT. See [CONTRIBUTING.md](CONTRIBUTING.md).
