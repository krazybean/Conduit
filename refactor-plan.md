# Conduit Architectural Refactor Plan

**Source:** user-provided architectural cleanup assessment
**Target:** separate Rust tests and route Python client behavior through a small provider contract.

## Preconditions

- Current working tree is intentionally dirty from the prior provider extraction; preserve those changes.
- Baseline: Python 77 passed/1 skipped; Rust 68 passed/3 ignored.
- No dependency, CI, or test-only workaround changes.

## Chunk 1: Decentralize Rust tests

Move crate-private tests from rust/src/lib.rs into thematic rust/src/tests/ modules, preserving the single cfg(test) mod tests declaration. Move only tests that can use the public API without private access into rust/tests/; do not duplicate coverage.

Exit criteria: Rust tests, cargo check, and git diff --check pass.

## Chunk 2: Add Python provider contract and lookup

Create a typed provider protocol and explicit for_driver lookup. Replace facade aliases and provider-specific branching in generation/streaming/list-model paths with provider method calls. Keep transport, lifecycle, validation, redaction, timeout, and cancellation in the facade. Add no base class unless shared implementation is proven.

Exit criteria: Python suite and compile/type checks pass; no unnecessary compatibility aliases remain.

## Chunk 3: Fresh architecture review

Review both resulting boundaries and classify remaining work as SHOULD DO, MAYBE LATER, or LEAVE ALONE. Do not implement review recommendations in this pass.

Exit criteria: final report documents moved tests, provider contract, validation, and stop point.
