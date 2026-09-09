# conduit-ai

Lightweight AI driver for OpenAI-compatible, Ollama, Anthropic, and Gemini — transport and normalization only, no framework. Python import remains `conduit`.

## Install

```sh
pip install conduit-ai
```

Requires Python 3.10+. Zero runtime dependencies (stdlib `http.client`).

## Ollama Quick Start

No endpoint or API key needed — talks to `http://localhost:11434` by default.

```python
from conduit import connect

model = connect(driver="ollama", model="qwen3:8b")
print(model.generate("Hello").text)

for event in model.stream("Hello"):
    if event["type"] == "text_delta":
        print(event["text"], end="")
```

## OpenAI-compatible

```python
from conduit import connect

model = connect(driver="openai-compatible", endpoint="http://localhost:1234/v1", model="my-model")
print(model.generate("Hello").text)
```

## Supported drivers

`openai-compatible` · `ollama` (`/api/chat`) · `anthropic` (`/v1/messages`) · `gemini` (`/v1beta/models/...:generateContent`)

Full API, spec, and conformance: [github.com/krazybean/Conduit](https://github.com/krazybean/Conduit)
