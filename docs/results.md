# SDK benchmark results

A smoke run creates a new gitignored `results/<run-id>/` directory. It preserves
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

`metadata.json` distinguishes the execution profile (`smoke`) from the SDK build
profile (`release`). It adds the SDK/ABI manifest, native header/library checksums, actual
toolchain/platform information, command lines, completion status, and harness
identity. Before an initial commit the harness SHA is null and its files are
identified by content hashes. Such results are development evidence.

SDK sources are separate pinned checkouts, not Git submodules. Existing source
checkouts must match their selected SHA and be clean. Source validation also runs
after measurement. Local SDK worktree modifications are not benchmark inputs.

## Samples

`samples.json` contains one validated sample per case/implementation for a
successful smoke run: ten Counter samples and twenty-four Variable samples when
both suites are selected. Each sample includes:

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
They do not establish performance regressions or binding overhead. The latency
metric is supported; neither Rust native allocation counts nor Go allocation
counts are reported by these round adapters.

Comparative measurement requires compatible source/features, contracts, toolchains,
and host conditions, warmup and repeated samples, and controlled execution order.
Results from different CI runners cannot be subtracted to isolate binding costs.
