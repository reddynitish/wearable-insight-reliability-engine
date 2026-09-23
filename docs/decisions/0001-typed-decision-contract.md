# 0001 — Four typed decisions as the public contract

**Status:** accepted, 2026-09-23

## Context

The engine could return a score (0–1 reliability), a boolean (show/don't), or a typed
decision. Callers are application developers who must branch on the result to build UI.

## Decision

The public API returns exactly one of `SHOW`, `SHOW_WITH_WARNING`, `WAIT_FOR_MORE_DATA`,
`REJECT`, alongside structured evidence, reason codes, and an explanation. The enum is
part of the frozen contract; adding a member is a breaking change.

## Consequences

- Callers get an exhaustive match, and a new engine version cannot silently introduce a
  state their UI does not handle.
- A score alone would have pushed the thresholding decision — the part that determines
  whether users see unjustified claims — into every calling app, which defeats the
  purpose of a shared trust layer.
- `WAIT_FOR_MORE_DATA` must be presented as a first-class product state, not an error.
  That is a documentation burden we accept.
- The four-way enum forces the awkward case (`SHOW_WITH_WARNING` on unsupportable
  evidence) to be scored as a failure in evaluation. See `docs/evaluation-protocol.md` §3.
