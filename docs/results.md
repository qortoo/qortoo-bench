# SDK benchmark results

A benchmark run creates a new gitignored `results/<run-id>/` directory. It preserves
an immutable input snapshot, per-command logs, validated per-round samples, and
execution metadata. Once a run directory exists, failures preserve the samples
that passed validation and the error that stopped the run; a partial run is never
marked as passed. Input/toolchain preflight failures stop before a run directory
is created and report their error directly.

## Input identity

`run-inputs.json` records the source commits, component dependencies, selected suites,
complete contracts, selected profile, toolchain versions, build profile/features,
and input file hashes. The runner checks input hashes again before accepting
the run so a contract edited during execution cannot silently change the result.

`metadata.json` distinguishes the execution profile (`smoke` or `full`) from the SDK build
profile (`release`). It adds the SDK/ABI manifest, native header/library checksums, actual
toolchain/platform information, command lines, completion status, and harness
identity. Before an initial commit the harness SHA is null and its files are
identified by content hashes.

SDK sources are separate pinned checkouts, not Git submodules. Existing source
checkouts must match their selected SHA and be clean. Source validation also runs
after measurement. Local SDK worktree modifications are not benchmark inputs.

## Samples and comparisons

A smoke run (`make smoke`) records 1 validated sample per scenario/implementation (34 total across
Counter and Variable) with `performance_baseline: false`.

A full measurement run (`make bench`) executes:
- 1 discarded warmup round per scenario/implementation (saved to `warmup-samples.json`).
- 10 measured rounds per scenario/implementation (340 samples total, saved to `samples.json`).
- Interleaved execution order across measured rounds (`Rust -> Go` on even rounds, `Go -> Rust` on odd rounds) to mitigate host condition drift.
- Statistical summary including min, max, mean, median, standard deviation, and p95 latency.
- Same-host comparison data saved to `comparison.json` and human-readable `comparison.md`.

Each sample includes:
- Contract, profile, implementation, and scenario identity.
- Operation count, elapsed nanoseconds, and nanoseconds per operation.
- Workload counts and logical datatype/client creation and release counts.
- The operation count and observed final value of each read/mutating chunk.

Raw stdout and stderr remain available alongside samples. A BuildCounter sample
has no final-value chunks because every counter is released inside its timed loop.
Variable samples also record payload identity and JSON byte count, their complete
final typed payload per chunk, and the previous sequence for Set/SyncLocal.
Operation and timing boundaries are defined in [Counter measurement](counter-measurement.md)
and [Variable measurement](variable-measurement.md).

## Interpretation

Smoke results always have `performance_baseline: false`. They demonstrate that
the shared workload and validation paths execute against the recorded SDKs.

Full measurement results produced on the same host with clean checkouts and matching
conditions establish a valid comparative baseline (`performance_baseline: true`).
Observed latency differences reflect the total path of the public API calls (including
serialization, native boundary, and runtime coordination), not a pure CGO invocation overhead.
Go runtime allocations and Rust native heap allocations are not measured or reported by this latency harness (latency only).

Comparative measurement requires compatible source/features, contracts, toolchains,
and host conditions, warmup and repeated samples, and controlled execution order.
Results from different CI runners cannot be subtracted to isolate binding costs.

See [Result export and publication](result-publishing.md) for the single tracking
integration, neutral document JSON, series separation, and durable storage contract.
