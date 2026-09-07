# Conduit work instructions

Every Conduit task MUST load/invoke the Ponytail skill before work begins,
including implementation, refactoring, debugging, testing, documentation,
release, and review. This is permanent and is not waived for small tasks.
If the skill is unavailable, report that blocker; do not silently skip it.
Surface conflicts with task instructions instead of silently choosing one.

Read the task and trace affected code and callers before choosing a fix.
Reuse existing code, then stdlib/native features, then installed dependencies;
only then write the smallest working change. Fix shared root causes.
No speculative abstractions, dependencies, or application orchestration.
Do not simplify away validation, security, cancellation, or data-loss handling.
Non-trivial logic needs one small runnable regression check. Trivial scaffolding
needs no invented tests. Mark deliberate technical ceilings with a `ponytail:`
comment naming the ceiling and upgrade path.

The language-neutral [spec](spec/README.md) is authoritative. Follow
[CONTRIBUTING.md](CONTRIBUTING.md) for scope, validation, and implementation order.
