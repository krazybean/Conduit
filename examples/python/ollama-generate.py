from conduit import connect
model = connect(driver="ollama", model="qwen3:8b")
print(model.generate("Hello").text)
