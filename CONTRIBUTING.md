# Contributing

Understand the actual flow first; reuse existing helpers and fix shared root causes. Prefer stdlib/native features and the fewest files. Add a dependency only with a concrete correctness, TLS, portability, or protocol-handling reason. Keep security, validation, and error handling intact. Non-trivial logic should leave one minimal runnable check.

## Scope and bloat test

Does this help connect to a provider, discover/select a model, generate/stream, expose capabilities, normalize provider behavior, or report usage/errors? If not, leave it out. Is a convenience making application/orchestration decisions? If yes, leave it to the application.

No agents, orchestration, routing, automatic selection/fallback, RAG, memory, conversation state, prompt management, workflows, eval frameworks, vector stores, tool execution/registries, DI, plugins, middleware frameworks, retry/policy engines, scoring, cost optimization, or persistent application state. No factory hierarchies, speculative extension points, or separately published provider packages. Base URL differences alone do not justify another driver.

## Contract and checks

Change [spec](spec/README.md) first when changing observable behavior, and add shared [conformance cases](conformance/README.md) with implementation. TypeScript is not the specification. Keep language APIs idiomatic; never silently drop requested features or turn missing usage/capability evidence into zero/true. Never put real credentials in fixtures, diagnostics, or examples.

Checks from the repository root:

```sh
# TypeScript (Node 22.13+)
npm ci --prefix typescript
npm run typecheck --prefix typescript
npm test --prefix typescript

# Python (stdlib http.client, no runtime deps)
PYTHONPATH=python:python/tests python3 -m unittest discover -s python/tests -v

# Rust (sync ureq + serde_json)
cargo test --manifest-path rust/Cargo.toml
```

TypeScript, Python, and Rust tests run against local mock HTTP servers and consume shared conformance fixtures; no live provider account is required.

## Dependency philosophy

Keep the dependency surface minimal and justified. Each language uses only what it needs for HTTP/TLS and serialization: TypeScript uses native `fetch`, Python uses stdlib `http.client`, Rust uses `ureq` + `serde`/`serde_json`. Do not add frameworks or SDKs for convenience.

## Security

Preserve input validation at trust boundaries, error handling that prevents data loss, and credential redaction. Known secrets and their URL encodings must be redacted from diagnostics; semantic content must never be redacted. Do not log secrets.

## Workflow

Open an issue or discussion before large changes. Keep commits focused and describe observable behavior. Follow the spec as the authoritative contract and keep implementations equivalent across languages.
