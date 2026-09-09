# conduit-ai

Lightweight AI driver for OpenAI-compatible, Ollama, Anthropic, and Gemini — transport and normalization only, no framework. Crate package `conduit-ai` exposes library `conduit`.

## Install

```sh
cargo add conduit-ai
```

Or in `Cargo.toml`:

```toml
[dependencies]
conduit-ai = "0.1.0"
```

## Ollama Quick Start

No endpoint or API key needed — talks to `http://localhost:11434` by default.

```rust
use conduit::ollama;

let model = ollama("qwen3:8b").unwrap();
println!("{}", model.generate("Hello").unwrap().text());
```

Alternative with explicit client:

```rust
use conduit::{connect, ClientConfig};

let model = connect(ClientConfig { driver: "ollama".into(), model: Some("qwen3:8b".into()), ..Default::default() }).unwrap();
println!("{}", model.generate("Hello").unwrap().text());
```

## OpenAI-compatible

```rust
use conduit::{connect, ClientConfig};

let model = connect(ClientConfig {
    driver: "openai-compatible".into(),
    endpoint: "http://localhost:1234/v1".into(),
    model: Some("my-model".into()),
    ..Default::default()
}).unwrap();
println!("{}", model.generate("Hello").unwrap().text());
```

## Supported drivers

`openai-compatible` · `ollama` (`/api/chat`) · `anthropic` (`/v1/messages`) · `gemini` (`/v1beta/models/...:generateContent`)

Full API, spec, and conformance: [github.com/krazybean/Conduit](https://github.com/krazybean/Conduit)
