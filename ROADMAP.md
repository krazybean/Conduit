# Roadmap

Conduit stays a lightweight model-access layer: infrastructure, not a framework.
Roadmap work should strengthen protocol coverage and normalization without adding
application orchestration or a provider integration for every runtime brand.

## Durable local and self-hosted inference

**Architectural principle: Conduit should normalize protocols/interfaces rather
than accumulate vendor-specific integrations.**

Ollama remains the first-class native local runtime. The existing
`openai-compatible` driver is the primary compatibility layer for local and
self-hosted servers that expose sufficiently compatible Chat Completions-style
APIs. A runtime name alone does not justify another driver; add a dedicated
driver only when concrete protocol or capability differences cannot be represented
cleanly through the existing normalized interface and provider escape hatch.

```
Conduit
├── ollama                  # native / existing
├── openai-compatible       # generic configurable compatibility layer / existing
│   ├── llama.cpp
│   ├── PrismML/Bonsai
│   ├── LM Studio
│   ├── vLLM
│   ├── LocalAI
│   ├── Jan
│   └── SGLang
└── mlx                     # investigate only if justified
```

### Runtime direction

1. **Ollama — existing/native**
   - Keep first-class support and the native `/api/chat` boundary.
   - Remains the default convenient local model-management/runtime integration.

2. **OpenAI-compatible — existing, highest-priority compatibility layer**
   - Keep the current configurable API-base model: endpoint/base URL, model
     identifier, optional credentials, headers, timeout, and provider options.
   - Make this the default path for local and self-hosted runtimes exposing an
     OpenAI-compatible API.
   - Harden compatibility through fixtures and runtime validation rather than
     adding vendor branches.
   - Do not create separate drivers when the generic adapter is sufficient.

3. **llama.cpp / llama-server — first validation target**
   - Validate the OpenAI-compatible path against llama-server.
   - Prioritize because of its adoption, GGUF ecosystem, Apple Silicon/Metal
     support, and fine-grained inference controls.
   - Add a dedicated driver only if real protocol or capability incompatibilities
     remain after using normalized fields and provider options.

4. **LM Studio — compatibility validation**
   - Validate its local OpenAI-compatible server through the generic driver.
   - Record capability differences as evidence, not as vendor-specific branches.

5. **vLLM — compatibility validation**
   - Validate as an important Linux/NVIDIA and higher-throughput self-hosted target.
   - Prefer the generic OpenAI-compatible path.

6. **LocalAI — compatibility validation**
   - Support through the generic OpenAI-compatible path where its API behavior is
     sufficiently compatible.

7. **Jan — compatibility validation**
   - Validate its local server through the generic compatibility layer.

8. **SGLang — compatibility validation**
   - Treat as a future server/high-throughput inference target, not a
     desktop-specific integration.

9. **MLX / MLX-LM — investigation**
   - First determine whether an OpenAI-compatible server surface is sufficient.
   - Consider a native MLX driver only if Apple-Silicon-specific capabilities,
     performance controls, model management, or APIs provide meaningful behavior
     that cannot be represented cleanly through the generic driver.

### PrismML / Bonsai

PrismML/Bonsai should **not** become a dedicated Conduit driver at this stage.
Its Bonsai llama.cpp fork exposes an OpenAI-style HTTP interface, which is exactly
the kind of runtime the generic compatibility layer is intended to cover. Use it
as a llama.cpp-family compatibility/validation case, not as a permanent
vendor-specific roadmap branch.

### Capability differences

OpenAI compatibility does not mean runtime capabilities are identical. Preserve
the existing rule that protocol evidence and model evidence are separate, and
represent missing evidence as unknown rather than inventing support.

Validation should capture runtime/model evidence for:

- streaming
- tools / function calling
- vision / multimodal input
- structured output, including JSON schemas
- reasoning support / reasoning controls
- model discovery / model listing
- context limits
- embeddings, once Conduit has an embedding operation/capability contract

The current capability contract already covers `streaming`, `tools`,
`structured_output`, `vision`, `reasoning_controls`, and `model_listing`.
Do not add an embeddings capability claim before an embedding operation exists.
Context-limit evidence likewise needs an explicit normalized contract before it
becomes a stable capability field.

### Sequencing

**Phase 1 — Generic compatibility layer hardening**
- Treat the existing `openai-compatible` driver as the durable configurable
  provider for local/self-hosted servers.
- Add conformance evidence only where real runtime behavior exposes gaps in the
  current normalized contract.

**Phase 2 — llama.cpp-family validation**
- Validate llama.cpp / llama-server.
- Validate PrismML/Bonsai through the same path.
- Fix shared protocol-normalization gaps rather than adding vendor-specific code.

**Phase 3 — broader runtime validation**
- Validate LM Studio, vLLM, LocalAI, Jan, and SGLang.
- Keep the list focused on widely adopted runtimes with credible staying power.

**Phase 4 — MLX decision**
- Investigate MLX / MLX-LM.
- Add a native driver only if meaningful Apple-Silicon-specific behavior cannot
  be represented through the generic OpenAI-compatible contract.

## Guardrails

- No runtime/provider implementation is implied by a roadmap validation target.
- No provider SDKs or separately published provider packages.
- No hidden compatibility fallback, automatic routing, or runtime selection.
- Prefer shared conformance fixtures and capability evidence over runtime-name
  conditionals.
- A dedicated driver requires a protocol boundary or meaningful capability
  difference, not branding or a different base URL.
