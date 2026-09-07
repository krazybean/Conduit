# Ollama native driver

Driver ID: `ollama`. Target native chat generation and native model listing,
separately from using Ollama through the OpenAI-compatible driver. Endpoint
configuration must support local or remote installations. No connection-time I/O.

Map native messages/content, tools, completion status, and reported token usage
into the common contract. Stream parsing must handle newline-delimited JSON
across arbitrary byte chunks and assemble the final response. Keep native model
size/family/quantization fields in ModelInfo metadata; do not infer capabilities
from a model identifier. Document precise wire mappings with native fixtures.

Native settings such as keep-alive and model-specific sampling options belong
in provider options, with nested collisions against normalized mappings rejected.
Do not expose model pulling, lifecycle management, embeddings, or app state.
No implementation in this scaffold.
