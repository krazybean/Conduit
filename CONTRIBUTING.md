# Contributing

## Ponytail is mandatory

Load/invoke the Ponytail skill before **every** Conduit task: implementation,
refactoring, debugging, testing, documentation, release, and review. Small,
mechanical, or obvious tasks are not exempt. Follow it throughout the task.
If unavailable, report the blocker. Surface conflicts rather than silently
ignoring either instruction. See [AGENTS.md](AGENTS.md).

Understand the actual flow first; reuse existing helpers and fix shared root
causes. Prefer stdlib/native features and the fewest files. Add a dependency
only with a concrete correctness, TLS, portability, or protocol-handling reason.
Keep security, validation, and error handling intact. Non-trivial logic must
leave one minimal runnable check; do not invent a test framework for scaffolding.
Mark deliberate shortcuts with `ponytail:` plus their ceiling and upgrade path.

## Scope and bloat test

Does this help connect to a provider, discover/select a model, generate/stream,
expose capabilities, normalize provider behavior, or report usage/errors?
If not, leave it out. Is a convenience making application/orchestration
decisions? If yes, leave it to the application.

No agents, orchestration, routing, automatic selection/fallback, RAG, memory,
conversation state, prompt management, workflows, eval frameworks, vector
stores, tool execution/registries, DI, plugins, middleware frameworks,
retry/policy engines, scoring, cost optimization, or persistent application state.
No factory hierarchies, speculative extension points, or separately published
provider packages. Base URL differences alone do not justify another driver.

## Contract and checks

Change [spec](spec/README.md) first when changing observable behavior, and add
shared [conformance cases](conformance/README.md) with implementation. TypeScript
is not the specification. Keep language APIs idiomatic; never silently drop
requested features or turn missing usage/capability evidence into zero/true.
Never put real credentials in fixtures, diagnostics, or examples.

Checks from the repository root (Node 22.13+ for TypeScript):

```sh
npm ci --prefix typescript
npm run typecheck --prefix typescript
npm test --prefix typescript
PYTHONPATH=python python3 -c 'import conduit'
cargo check --offline --manifest-path rust/Cargo.toml
```

TypeScript tests run against local mock HTTP servers and consume shared fixtures;
no live provider account is required. Python/Rust checks remain scaffold checks.
Python supports source imports only; choose a build backend when distributing it.
Do not publish these private/pre-release packages without explicit authorization.

## Next slice

Implement a **native Ollama text driver** using its native chat request/response
and NDJSON stream protocol. Keep the existing client/model, text, usage, error,
timeout/cancellation semantics; add shared native wire fixtures and local-server
checks. No tools, structured output, or speculative provider plugin interface.
OpenAI-compatible generate/stream tests must continue to pass unchanged.

Expected sequence: spec/scaffold → TypeScript/OpenAI-compatible text → TypeScript
streaming → native Ollama → tools/structured output → Anthropic → Gemini → stable
v0 conformance → Python → Rust. Commit/publish only when explicitly requested.
