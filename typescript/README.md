# @krazybean/conduit

Lightweight AI driver for OpenAI-compatible, Ollama, Anthropic, and Gemini — transport and normalization only, no framework.

## Install

```sh
npm install @krazybean/conduit
```

Requires Node 22.13+ ESM.

## Ollama Quick Start

No endpoint or API key needed — talks to `http://localhost:11434` by default.

```ts
import { connect } from "@krazybean/conduit";

const model = connect({ driver: "ollama", model: "qwen3:8b" });
console.log((await model.generate("Hello")).text);

for await (const e of model.stream("Hello")) {
  if (e.type === "text_delta") process.stdout.write(e.text);
}
```

## OpenAI-compatible

```ts
import { connect } from "@krazybean/conduit";

const model = connect({
  driver: "openai-compatible",
  endpoint: "http://localhost:1234/v1",
  credentials: process.env.CONDUIT_API_KEY,
  model: "my-model",
});
console.log((await model.generate("Hello")).text);
```

## Supported drivers

`openai-compatible` · `ollama` (`/api/chat`) · `anthropic` (`/v1/messages`) · `gemini` (`/v1beta/models/...:generateContent`)

Full API, spec, and conformance: [github.com/krazybean/Conduit](https://github.com/krazybean/Conduit)
