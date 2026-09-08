import os
from conduit import connect

client = connect(driver="ollama", endpoint=os.getenv("CONDUIT_ENDPOINT", "http://localhost:11434"))
print(client.list_models())
model = client.model(os.getenv("CONDUIT_MODEL", "llama3"))
res = model.generate(messages=[{"role": "user", "content": "Hello"}])
print(res["text"])
for ev in model.stream(messages=[{"role": "user", "content": "Hello"}]):
    if ev["type"] == "text_delta":
        print(ev["text"], end="")
print()
