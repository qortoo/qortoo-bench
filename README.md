# Qortoo Bench

Central benchmark harnesses for Qortoo components. Counter and Variable suites
run through the Rust SDK and Go binding's public APIs. Both languages consume
the same suite-specific JSON contracts.

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

This validates both suites. To select one:

```sh
make smoke SUITE=variable
make smoke SUITE=counter
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
It builds each SDK harness once and runs one smoke round per case/implementation
in a separate process. Counter has five cases. Variable has four operations at
64B, 1KiB, and 16KiB of actual serialized JSON, for twelve cases. Mutable smoke
workloads cross their actual reset interval. Final/previous values, operation
counts, resets, payload sizes, and logical releases are validated.

Each run saves stdout/stderr logs, `run-inputs.json`, validated `samples.json`,
and `metadata.json` in gitignored `results/<run-id>/`. Metadata records SDK
revisions, toolchains, native SDK/ABI manifest and checksums, command lines, and
harness file hashes. A run without a harness commit records a null harness SHA
and stays a development result.
Failures keep their logs and failed status; fix the reported input/build problem
and rerun the same command.

Smoke and full budgets use the same workload code. Smoke validates execution;
its timing is not a performance baseline or Rust/Go overhead comparison.

See [Counter measurement](docs/counter-measurement.md), [Variable measurement](docs/variable-measurement.md),
and [Results](docs/results.md) for contracts, timing boundaries, and interpretation.
