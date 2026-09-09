# Conduit

[![npm](https://img.shields.io/npm/v/@krazybean/conduit)](https://www.npmjs.com/package/@krazybean/conduit) [![PyPI](https://img.shields.io/pypi/v/conduit-llm)](https://pypi.org/project/conduit-llm/) [![crates.io](https://img.shields.io/crates/v/conduit-ai)](https://crates.io/crates/conduit-ai) [![License: MIT](https://img.shields.io/github/license/krazybean/Conduit)](https://github.com/krazybean/Conduit/blob/main/LICENSE)

A lightweight AI driver. Infrastructure, not a framework.

One small normalized API — `connect` → `model` → `generate` / `stream` — for local or hosted models across TypeScript, Python, and Rust. No orchestration, agents, or RAG baggage.

```ts
import { connect } from "@krazybean/conduit";
const model = connect({ driver: "ollama", model: "qwen3:8b" });
console.log((await model.generate("Why is the sky blue?")).text);
```

## Install

### TypeScript / JavaScript

```sh
npm install @krazybean/conduit
```

Requires Node 22.13+ ESM. Zero runtime dependencies.

### Python

```sh
pip install conduit-llm
```

Requires Python 3.10+. Zero runtime dependencies. The distribution is `conduit-llm`; the Python package remains `conduit`.

```python
from conduit import connect
```

### Rust

```toml
# Cargo.toml
[dependencies]
conduit-ai = "0.2.0"
```

```sh
cargo add conduit-ai
```

Sync, `ureq` + `serde_json` only. The crates.io package is `conduit-ai`; the Rust crate remains `conduit`.

```rust
use conduit::ollama;
```

## Quick Start

Ollama is the zero-ceremony local path — no endpoint needed (defaults to `http://localhost:11434`).

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

let model = ollama("qwen3:8b").unwrap();
let response = model.generate("Why is the sky blue?").unwrap();
println!("{}", response.text());
```

## Supported Drivers

| Driver | Local / Hosted |
| --- | --- |
| OpenAI-compatible | Both (any OpenAI-compatible base URL) |
| Ollama | Local / remote Ollama (`/api/chat`) |
| Anthropic | Hosted (`/v1/messages`) |
| Gemini | Hosted (`/v1beta/models/...:generateContent`) |

`openai-compatible` covers any provider exposing the OpenAI Chat Completions shape — not only OpenAI itself.

## What Conduit Is / Isn't

**Conduit does:**

- normalized generation and streaming
- model discovery (`listModels` / `list_models`)
- tools and structured output transport
- normalized errors
- provider escape hatches (`providerOptions`)

**Conduit does not:**

- agents
- orchestration
- RAG
- memory
- prompt management
- tool execution
- model routing

Conduit owns transport and normalization. Your application owns history, tool execution, retries, and orchestration.

## Language-specific details

- TypeScript: [typescript/README.md](typescript/README.md) — Node ESM, `fetch`, types, streaming details
- Python: [python/README.md](python/README.md) — stdlib `http.client`, mapping helpers
- Rust: [rust/README.md](rust/README.md) — sync `ureq`, `serde`, `ollama()` helper

Spec is authoritative: [spec/README.md](spec/README.md). Conformance fixtures: [conformance/](conformance/). Examples: [examples/](examples/).

License: MIT. See [CONTRIBUTING.md](CONTRIBUTING.md).
