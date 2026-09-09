# Conduit work instructions

Understand the real flow before choosing a fix. Reuse existing code, then stdlib/native features, then installed dependencies; only then write the smallest working change. Fix shared root causes.

Avoid speculative abstractions, dependencies, or application orchestration. Keep validation, security, cancellation, and data-loss handling intact.

The language-neutral [spec](spec/README.md) is authoritative. Follow [CONTRIBUTING.md](CONTRIBUTING.md) for scope, validation, and implementation order.
