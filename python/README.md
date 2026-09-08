# Conduit (Python)

Small provider-neutral AI driver — Python port of [Conduit](../README.md).

> Scaffold: public API not yet implemented. Packaging is ready; generation/streaming will follow the [spec](../spec/README.md).

## Requirements

- Python 3.10+
- Zero runtime dependencies (stdlib `urllib`/`json` only)

## Install (source)

```sh
pip install -e ./python
# or
pip install ./python
```

## Example (planned API)

```python
from conduit import connect

client = connect(
    driver="openai-compatible",
    endpoint="http://localhost:1234/v1",
    # credentials="sk-..."  # optional, for hosted endpoints
)
model = client.model("my-model")

response = model.generate(
    messages=[{"role": "user", "content": "Hello"}],
)
print(response.text)

# Streaming (planned)
# for event in model.stream(messages=[{"role": "user", "content": "Hello"}]):
#     if event.type == "text_delta":
#         print(event.text, end="")
```

See [spec/generation.md](../spec/generation.md) and [spec/streaming.md](../spec/streaming.md) for request/response semantics. TypeScript usage is documented in [typescript/README.md](../typescript/README.md).
