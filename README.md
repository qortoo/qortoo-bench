# Qortoo Bench

Central benchmark harnesses for Qortoo components. The Counter suite runs
IncreaseBy, GetValue, Transaction, SyncLocal, and BuildCounter through the Rust
SDK and Go binding's public APIs. Both adapters consume one shared JSON contract.

## Requirements

- Git, Make, Python 3.9 or newer, and a C compiler.
- Rust 1.97.1 through rustup (selected by `rust-toolchain.toml`).
- Go 1.27.2 with cgo enabled.

`sources.lock.json` selects full SDK commits. The Rust revision matches the native
SDK pin in the selected Go revision's CI. Both Rust paths enable `observability`
and use release builds; no telemetry pipeline or handler is installed.

## Run the smoke check

From this repository:

```sh
make smoke
```

The runner clones the pinned SDK commits into gitignored `_sources/`. To use
existing local Git repositories as clone sources and select a Go installation:

```sh
make smoke SOURCE_ROOT=.. GO=/opt/homebrew/opt/go@1.27/bin/go
```

`SOURCE_ROOT` must contain `qortoo-rs` and `qortoo-go`. Only committed objects are
copied, so local changes in those repositories are not inputs. Existing source
checkouts must be clean and match the lock; the runner refuses mismatches rather
than resetting them. Gitignored build output is allowed.

The runner stages a release native SDK, builds the Rust harness with locked
dependencies, and uses `go test -a -count=1` and `go build -a` against that SDK.
It runs one smoke round for each scenario/implementation in a separate process.
Mutable smoke workloads cross the actual reset interval. Final values, operation
counts, resets, and logical releases are validated against the contract.

Each run saves stdout/stderr logs, `run-inputs.json`, validated `samples.json`,
and `metadata.json` in gitignored `results/<run-id>/`. Metadata records SDK
revisions, toolchains, native SDK/ABI manifest and checksums, command lines, and
harness file hashes. A run without a harness commit records a null harness SHA
and stays a development result.
Failures keep their logs and failed status; fix the reported input/build problem
and rerun the same command.

Smoke and full budgets use the same workload code. Smoke validates execution;
its timing is not a performance baseline or Rust/Go overhead comparison.

See [Measurement](docs/measurement.md) for workloads and timing boundaries, and
[Results](docs/results.md) for evidence and interpretation.
