# Roadmap

Conduit stays a lightweight model-access layer: infrastructure, not a framework.
Roadmap work should strengthen protocol coverage and normalization without adding
application orchestration or a provider integration for every runtime brand.

The next cross-language foundation is recorded in
[`spec/foundation.md`](spec/foundation.md): protocol adapters remain the
behavioral boundary, provider presets stay local convenience metadata, and
provider-reported usage remains distinct from optional caller-side aggregation
and best-effort cost estimates.

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
     that cannot be represented cleanly through the generic OpenAI-compatible contract.

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

## Follow-up

- [ ] Add shared cross-language provider-resolution conformance fixtures and
  runners before release: compare provider-to-driver/protocol and default
  endpoint resolution, explicit endpoint overrides, conflicting driver
  rejection, authentication configuration/header semantics, and consistent
  malformed or unknown provider errors across TypeScript, Python, and Rust.
  Keep credentials synthetic and endpoints local; a manifest copy check alone
  is not sufficient evidence of behavioral parity.
- [ ] Revisit the Python and Rust implementations and, where they are overly centralized in single-file modules, refactor them into clearer modular structure while preserving behavior; avoid speculative architecture.

## Viability: discoverability and trust

Conduit is already the best answer for the developer who doesn't want a
framework. The gap is not the code — it is that its buyers haven't been
introduced to it, and the paper trail doesn't yet prove the quality that is
already there. This section closes the discoverability and trust gap without
changing the product's scope.

HN launch is deferred until the trust signals below are in place.

### Look better on paper

1. **Ship 1.0.** The single biggest trust signal. 0.2.2 says "experiment";
   1.0 says "stable API, safe to build on." Pair it with a short API-stability
   promise.
2. **Publish the conformance matrix.** The existing `conformance/` work should
   be surfaced in the README as a table: driver x capability x verified
   against what. None of the big libraries show their receipts like this.
3. **README rewrite, story-first.** Lead with the anti-framework pitch ("one
   small explicit interface"), then a comparison table where Conduit wins:
   dependencies (0 in Python), install size, API surface, no lock-in. End
   with "Used by" — real applications running on Conduit.
4. **Make the provider list look as big as it is.** Document
   OpenAI-compatible as "verified compatible runtimes: vLLM, llama.cpp,
   LM Studio, LocalAI, Jan, SGLang." No new code, honest, and the list goes
   from 4 to 10.
5. **Unify the three registry pages.** Same README, same keywords, each
   linking to the other two. Add search terms people actually type:
   "lightweight LLM client," "LangChain alternative," "LiteLLM alternative,"
   "Ollama client."
6. **Badges that mean something.** CI passing, conformance matrix, license.
   Skip vanity badges. Add a CHANGELOG link so the release history is one
   click away.

### Discoverability

7. **Three tutorial posts on juancastro.me**, cross-posted to dev.to: local
   AI in 10 minutes with Ollama, tool calling without a framework,
   structured output without the bloat. These rank on search and feed the
   site that already exists.
8. **Niche subreddit posts, not r/programming.** r/LocalLLaMA and
   r/opensource "show your project" threads reach exactly the local-model
   buyer, with a fraction of HN's scrutiny.
9. **Awesome-list PRs.** One PR each to awesome-llm, awesome-ollama,
   awesome-local-ai. Small effort, permanent inbound links.
10. **GitHub topics on the repo.** `llm`, `ollama`, `openai-compatible`,
    `lightweight`, `typescript`, `python`, `rust` — this is how GitHub
    search finds the repo.
11. **A starter template, not just examples.** A "chat with a local model in
    50 lines" repo people clone and share. Templates travel further than
    libraries.
12. **Enable GitHub Discussions.** Lower friction than issues for "does this
    work with X?" questions — and visible activity is its own trust signal.

### Small product moves that pay for themselves

13. **Embeddings.** The one real capability gap. One endpoint, unlocks every
    RAG-adjacent user without becoming a framework. Highest-leverage code
    item on this list. (Consistent with the existing rule: no embeddings
    capability claim before the embedding operation exists.)
14. **Cost tracking, later.** Usage tokens already exist; a small price table
    or provider cost passthrough can wait until someone asks for it.
15. **Image/audio: explicitly out of scope.** Say so in the README. A
    deliberate "we don't do this" reads as focus, not a gap.
