# 0003 — No LLM dependency in the first vertical slice

**Status:** accepted, 2026-09-23

## Context

Explanations must be human-readable. An LLM would write nicer prose than templates.

## Decision

v0.1 generates explanations deterministically from the evidence trace using templates
tied to reason codes. No LLM at runtime, no network dependency, no API key.

## Consequences

- Explanation fidelity is trivially 1.0 and testable: every sentence is derived from a
  trace field, and a test asserts no explanation contains a reason code absent from the
  trace.
- Decisions are reproducible and the service runs offline.
- Prose is more repetitive than an LLM's. Accepted.
- If an LLM rewriting layer is added later it is strictly a presentation adapter: it
  receives the finished decision, may not alter the decision, may not add evidence, may
  not name a condition, and its output is validated against the trace before being
  returned. That validator is a prerequisite for merging any such layer.
