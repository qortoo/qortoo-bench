# Variable measurement contract

The `variable/v1` suite measures typed JSON Variable operations through the Rust
and Go public APIs. Its contract is [`contracts/variable.json`](../contracts/variable.json).
Rust uses `set` and `get<Payload>`; Go uses `Set` and `Get(&Payload)`. These are
complete SDK calls including JSON conversion and native-boundary work, so their
latency difference is not a pure FFI-call measurement.

The suite uses the same SDK preparation, build, execution, manifest, and result
preservation path as [Counter](counter-measurement.md). Its payloads, budgets, reset
intervals, and result validation belong to its own contract and adapter.

## Payload model

Each payload is an object with `sequence` followed by `data`. Sequence is a
16-digit zero-padded decimal string; data is ASCII `x` padding. Both serializers
produce exactly 64, 1,024, or 16,384 UTF-8 JSON bytes, without whitespace. That
size includes the object syntax, keys, quotes, sequence, and padding.

Every fresh Variable is seeded with sequence zero before timing. Writes advance
the sequence by one within that Variable. The fixed width keeps the actual JSON
size constant as values change. A transaction advances it once per Set. Read
rounds retain the seeded zero sequence. The data padding must remain unchanged.

Typed values, sequences, padding, key strings, and payload-size checks are
prepared outside timing. SDK serialization, deserialization, and the dynamic
previous-value return from Set stay inside timing. Creating prepared fixtures
does not replace the typed SDK calls with raw JSON APIs.

## Cases and budgets

Each payload has Set, Get, Transaction, and SyncLocal cases, for twelve cases per
implementation. One Transaction operation commits ten Sets. One SyncLocal
operation performs one Set and one successful synchronous manual sync.

| Actual JSON bytes | Set/Sync full budget | Get full budget | Transaction full budget | Set/Sync reset | Transaction reset |
| --- | ---: | ---: | ---: | ---: | ---: |
| 64 | 4,000 | 100,000 | 400 | 1,000 | 100 |
| 1,024 | 400 | 20,000 | 40 | 100 | 10 |
| 16,384 | 40 | 2,000 | 4 | 10 | 1 |

Mutable smoke budgets are reset-plus-one; read smoke budgets are 100 operations.
Thus smoke exercises a real reset at every payload size. Full and smoke execute
the same workload code with different budgets. These full budgets define the
workload; smoke does not establish whether full repeated measurements are stable.

## Timing and lifecycle

Each invocation executes one round with a fresh client, manual LocalConnectivity,
no handler or telemetry installation, and one thread issuing operations.
The SDK uses release builds and the same Rust revision/features in both paths.
The caller schedules rounds: smoke has zero warmups and one validation round;
full measurement specifies one discarded warmup and ten measured rounds.

Every mutable chunk uses a fresh Variable. Payload preparation, initial seed,
validation Get, Variable release, and client cleanup are outside its timer.
One timer pair measures the entire chunk's operation loop. A Get case uses one
Variable and one timer pair for the read loop. Chunk durations are summed and
divided by the case's operation count.

Transaction callbacks reference prepared typed payloads. Rust captures an owned
Arc of those immutable values to satisfy its callback lifetime contract; Go
captures a slice. Callback capture, invocation, and Set result handling are part
of the measured operation. No payload sequence formatting runs in the callback.

## Payload envelope and outcomes

The contract limits the serialized payload bytes represented by one chunk to
256 KiB, including its seed and every Set in the chunk. A transaction contributes
ten payloads per operation. Large payloads therefore use smaller resets and
budgets. This envelope counts payload bytes, not transaction metadata, retained
server history, allocator overhead, or total process/native heap use. Released
handles do not prove immediate native memory or thread reclamation.

After timing, each chunk's entire typed value is checked against its expected
sequence and padding. For Set and SyncLocal, the last returned previous value is
also checked against the preceding payload. Transaction validates the final
sequence after all ten-write commits; each Set still decodes and returns its
previous value even when the callback discards it. Get validates its last timed
read and its unchanged stored value.

Samples contain the payload ID and actual JSON byte count, per-chunk values and
previous sequence, operation statistics, and logical Variable/client release
counts. The independent Python validator checks these against the contract.
Bad sizes, an incomplete operation/payload matrix, unsafe payload envelopes,
wrong values, or missing releases cannot produce an accepted smoke result.

## Code map

- [`suites/variable.json`](../suites/variable.json): components, adapters, and latency metric.
- [`harnesses/rust/src/bin/variable.rs`](../harnesses/rust/src/bin/variable.rs) and
  [`harnesses/go/variable.go`](../harnesses/go/variable.go): typed round execution.
- [`harnesses/rust/src/lib.rs`](../harnesses/rust/src/lib.rs) and
  [`harnesses/go/client.go`](../harnesses/go/client.go) and
  [`harnesses/go/contract.go`](../harnesses/go/contract.go): shared SDK/client setup and execution conditions.
- [`scripts/suite_registry.py`](../scripts/suite_registry.py) and
  [`scripts/smoke.py`](../scripts/smoke.py): shared suite resolution and orchestration.
- [`scripts/variable_contract.py`](../scripts/variable_contract.py): independent payload and result checks.
- [`harnesses/go/variable_test.go`](../harnesses/go/variable_test.go) and
  [`tests/test_contracts.py`](../tests/test_contracts.py): byte-size, reset-boundary,
  matrix, and envelope validation.

See [Results](results.md) for source identity and interpretation.
