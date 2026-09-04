# DEN-3488 multiversion circular dependency E2E

This repository independently checks the exact dependency graph:

```text
A@1 -> B@1 -> A@2 -> B@0 -> A@2
```

The contract test is intentionally implemented in standalone Rust with no
third-party dependencies. It validates behavior independently of the product
resolver implementation.

## Required invariants

1. Node identity includes registry, organization, package name, and exact
   version. `A@1` and `A@2` are distinct nodes even though they share the same
   package coordinate.
2. Traversal terminates after four exact nodes and recognizes only the closing
   `B@0 -> A@2` edge as the back-edge.
3. The materialization plan contains one consumer root link plus one link per
   dependency edge. It does not create recursively nested package copies.
4. A real Unix filesystem materialization has exactly four canonical node
   directories, and the closing edge is a symlink to the existing canonical
   `A@2` node.
5. Applying the same plan twice is idempotent and preserves all link targets.
6. Diagnostics include exact versions, the closing edge, the selected symlink
   strategy, and an explicit statement that recursive copying stopped.
7. Missing exact nodes fail validation before any materialization plan is
   accepted.

## Product integration

`product-source-pin.json` is an immutable source pin for the corresponding
`zed-pkg/zed-cli` exact-graph implementation. The product job remains staged
until `ready` is set to `true` after the product branch has assembled and passed
its own tests. Once enabled, CI checks out that exact commit and reruns the
public executable cycle tests, exact-node unit tests, formatting, and Clippy.

The pin is never replaced with a branch name or floating tag.
