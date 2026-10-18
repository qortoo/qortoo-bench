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
and `metadata.json` in gitignored `results/<run-id>/`.

## Run full measurements

To run full budget multi-round comparative benchmarks on the same host:

```sh
make bench SOURCE_ROOT=.. GO=/opt/homebrew/opt/go@1.27/bin/go
```

To benchmark a single suite:

```sh
make bench SUITE=counter SOURCE_ROOT=.. GO=/opt/homebrew/opt/go@1.27/bin/go
make bench SUITE=variable SOURCE_ROOT=.. GO=/opt/homebrew/opt/go@1.27/bin/go
```

Full measurement executes:
- 1 discarded warmup round per scenario/implementation.
- 10 measured rounds per scenario/implementation (340 total samples across both suites).
- Interleaved execution order across measured rounds (`Rust -> Go` on even rounds, `Go -> Rust` on odd rounds).
- Per-scenario statistical summary (min, max, mean, median, stddev, p95) and same-host comparison report (`comparison.md`, `comparison.json`).

Smoke and full budgets use the same workload code. Smoke validates execution;
full measurement produces comparative performance data on a controlled host.

See [Counter measurement](docs/counter-measurement.md), [Variable measurement](docs/variable-measurement.md),
and [Results](docs/results.md) for contracts, timing boundaries, and interpretation.

## Export results

Use `scripts/export_results.py` to create a validated neutral JSON bundle and
custom JSON input for the pinned `github-action-benchmark` integration. Export
defaults to the exploratory channel; official data requires a clean baseline and
verified central publication context. See [Result export and publication](docs/result-publishing.md)
for the command, environment input, metric/series identity, durable storage policy,
and local Action compatibility check.

See [Central manual workflow](docs/central-workflow.md) to measure both SDKs on
GitHub Actions, inspect permanent results, or retry an attempt.
