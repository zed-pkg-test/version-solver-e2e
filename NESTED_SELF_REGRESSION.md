# Nested and self dependency registry regression

This repository independently exercises the nested/self dependency behavior
merged in `zed-pkg/zed-cli#263`. It does not call product-unit-test helpers or
copy the product repository's Rust test implementation. Instead, it builds the
exact merged CLI and drives public commands against a fresh `file://` registry.

## Immutable source pins

| Source | Commit |
|---|---|
| `zed-pkg/zed-cli` | `14bc2fb1bdc2b85e5545e60e70fc94f047188662` |
| `zed-pkg/zed-interfaces` | `60a8ab55f8a55eb212a72dcb334c1c118047c7ef` |

The workflow checks out both repositories as siblings because the CLI uses the
local `zed-interfaces` Rust package during compilation.

## Scenarios

### Sixteen-package nested graph

The harness publishes a linear graph in dependency-first order:

```text
acme/nested-00 -> acme/nested-01 -> ... -> acme/nested-15
```

A clean consumer then verifies:

1. normal symlink installation resolves all 16 coordinates once;
2. the lock contains the exact transitive closure;
3. frozen copy installation preserves the lock byte-for-byte;
4. deleting the global store and project modules still permits a cold frozen
   copy replay from immutable registry artifacts;
5. the same lock can transition back to frozen symlink mode;
6. every installed package retains its expected payload.

On Windows, requested symlink mode is expected to normalize to an independent
copy, matching the product contract.

### Published package self-loop

The registry receives `acme/self-loop@1.0.0` with a compatible dependency on
its own coordinate:

```text
acme/self-loop@1.0.0 -> acme/self-loop@=1.0.0
```

A consumer must resolve exactly one lock entry, install the published payload,
and preserve that lock across a frozen transition from symlink to copy mode.

### Workspace package testing its own published artifact

The harness publishes `acme/self-test@1.0.0`, then creates a workspace member
`acme/self-test@2.0.0` with two direct dependencies:

```text
acme/self-test@2.0.0
├── acme/self-test@=1.0.0         # registry artifact under test
└── acme/workspace-control@=1.0.0 # ordinary workspace source dependency
```

The registry artifact must appear in the lock and must contain the published
v1 payload. The control dependency must remain sourced from the workspace and
must not appear in the registry lock. Frozen copy mode must preserve both
semantics without changing the lock.

## Security and reproducibility properties

The harness uses a temporary registry, temporary global store, fresh package
repositories, immutable Git source pins, and no private credentials. Published
fixtures exclude `.git`, generated locks, pack outputs, and `zed_modules` from
artifacts. Each run emits the final lock hashes and resolved package sets as
JSON evidence.

## Running locally

Place compatible source checkouts next to this repository:

```text
parent/
├── version-solver-e2e/
├── zed-cli/
└── zed-interfaces/
```

Then run:

```sh
cd ../zed-cli
cargo build --locked --bin zed
python ../version-solver-e2e/scripts/nested-self-regression.py \
  --zed-dir target/debug \
  --depth 16
```

The GitHub Actions matrix executes the same command on Linux, macOS, and
Windows nightly, on manual dispatch, and for relevant pull requests.
