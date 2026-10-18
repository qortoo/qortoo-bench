# Counter measurement contract

The Counter suite defines the workload shared by the Rust SDK and Go binding.
Its executable contract is [`contracts/counter.json`](../contracts/counter.json).
Both harnesses load that file at runtime; budgets and reset intervals are not
language-specific constants.

## Components and execution

[`components.json`](../components.json) declares components and dependencies.
The Go SDK depends on the Rust SDK for its native library.
[`suites/counter.json`](../suites/counter.json) associates the Counter contract
with those components, their harnesses, and a latency metric in `ns/op`, where
smaller values are better. Component identities describe build inputs; suite
identities describe workloads. Additional components need a suite and an adapter
that prepares their inputs, executes a round, and reports that suite's metrics.
The Counter runner supports its two declared implementations.

Every round uses one fresh client and manual LocalConnectivity, with realtime
disabled and no registered handler. One thread issues measured operations.
SDK runtime work can use background threads. Rust and the native library use
the same pinned Rust source, the `observability` feature, and release profile.
No observability pipeline is installed.

## Workloads

| Scenario | Full operations | Smoke operations | Reset interval | Required outcome |
| --- | ---: | ---: | ---: | --- |
| IncreaseBy | 200,000 | 50,001 | 50,000 writes | Each counter ends at its chunk's write count |
| GetValue | 2,000,000 | 1,000 | None | A counter seeded with 1 still returns 1 |
| Transaction | 20,000 | 5,001 | 5,000 transactions | Each committed transaction performs 10 writes |
| SyncLocal | 20,000 | 5,001 | 5,000 syncs | Each operation writes once and successfully syncs |
| BuildCounter | 100 | 3 | None | Each constructed counter is released on one fresh client |

An operation in Transaction means a complete transaction. An operation in
SyncLocal means one increase followed by one synchronous manual sync. For these
scenarios the final value of each counter is checked after its timed chunk.
GetValue's setup write and validation reads are outside timing and excluded from
the reported workload read/write counts.

Mutable workloads divide a round into chunks of at most the reset interval.
Each chunk gets a fresh counter with a unique key. The preceding counter is
validated and released before the next is constructed. A round ending exactly
at a reset interval uses one counter; interval-plus-one uses two. Smoke budgets
deliberately cross this boundary without running the full budget.

BuildCounter preformats its keys and constructs a fresh client before timing.
Its entire timed loop includes creating and immediately releasing each counter.
It never exceeds the declared per-client capacity. No live counter remains for
a final-value assertion; successful construction/release counts are its outcome.

## Timing boundaries

The client, counter setup, key formatting, JSON handling, validation, and client
cleanup are outside measured time. Counter construction/release are also outside
timing for read/write/transaction/sync scenarios. BuildCounter includes those two
operations because they are its workload.

Each mutable chunk has one monotonic timer pair around its operation loop.
Read and build scenarios have one timer pair around the round's loop. Chunk
durations are summed and divided by the round's operation count. Each language
retains its API's error handling, and Rust uses `black_box` to preserve read
results and write effects. Validation and accounting run after the timer stops.
Small durations include timer overhead; smoke timings are not comparison data.

The `counter/v1` contract has explicit sample lifetimes and timer boundaries.
A change to workload semantics, timing boundaries, reset behavior, or execution
conditions requires a new contract identity. The run also records the contract's
content hash so input changes remain identifiable.

## Profiles and rounds

Smoke and full profiles select different budgets from the same contract and
execute the same code. A harness invocation executes exactly one round for one
scenario. The caller controls warmup and measured rounds.

The contract specifies zero warmup rounds and one validation round for smoke.
For full measurement it specifies one discarded warmup and ten measured rounds.
`make smoke` schedules the smoke profile. A full multi-round comparison must also
control execution order and environment; calling a binary once with `full` does
not produce that comparison.

## Validation and limits

The central runner checks suite dependencies, supported execution conditions,
unique scenarios, positive budgets and reset intervals before building. It then
validates each output's identity, operation count, timing, chunk values, write/
read/transaction/sync counts, reset count, and logical object releases against
the input contract. Failed API operations terminate that round without a valid
sample. Failed or incomplete smoke runs retain logs and a failed status.

Release counts mean Rust ownership was dropped or Go `Close` returned. They do
not prove that all native allocations or runtime threads have been reclaimed.
No process RSS, native heap, or Go allocation metric is inferred from these counts.

Every invocation runs in a separate process, giving clients and datatypes a
bounded per-round lifetime. Go closes counters before Client and LocalConnectivity.
The Rust client owns its connectivity and is dropped after its counters.

## Code map

- [`harnesses/rust/src/bin/counter.rs`](../harnesses/rust/src/bin/counter.rs) and
  [`harnesses/go/counter.go`](../harnesses/go/counter.go): per-round workloads and timers.
- [`harnesses/go/counter_test.go`](../harnesses/go/counter_test.go): exact-boundary and
  boundary-plus-one value/release behavior for mutable scenarios.
- [`scripts/counter_contract.py`](../scripts/counter_contract.py): Counter contract
  and independent sample validation.
- [`scripts/suite_registry.py`](../scripts/suite_registry.py): shared suite/component input validation.
- [`scripts/smoke.py`](../scripts/smoke.py): pinned SDK preparation, compilation,
  one-round execution, and evidence preservation.

See [Variable measurement](variable-measurement.md) for typed JSON workloads and
[Results](results.md) for source manifests and result interpretation.
