import os
from conduit import connect

client = connect(
    driver="openai-compatible",
    endpoint=os.getenv("CONDUIT_ENDPOINT", "http://localhost:1234/v1"),
    credentials=os.getenv("CONDUIT_API_KEY"),
)
print(client.list_models())
model = client.model(os.getenv("CONDUIT_MODEL", "my-model"))
res = model.generate(messages=[{"role": "user", "content": "Hello"}])
print(res["text"], res.get("usage"))
