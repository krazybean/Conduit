# Anthropic driver

Driver ID: `anthropic`. Target the Messages API, preserving its native ordered
content blocks and streaming semantics through normalized content/events.
Do not implement a Chat Completions emulation layer or depend on the provider SDK.

Driver mapping must account explicitly for system instructions, tool-use/result
blocks, split tool JSON, finish reasons, and usage updates. Do not flatten blocks
into text. Provider-specific thinking/cache settings remain native options.
Protect authentication and required protocol headers from custom overrides.
Model listing is exposed only when the implemented provider operation supports it.

Before implementation, add fixtures for message/block mapping, native errors,
stream completion, and usage accumulation. Capability evidence must distinguish
protocol representation from model support. No implementation in this scaffold.
