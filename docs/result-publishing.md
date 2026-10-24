# Result export and publication contract

Qortoo Bench uses **benchmark-action/github-action-benchmark** as its single
benchmark tracking integration, with custom JSON input and external JSON history.
The Action is a community project, not a GitHub first-party benchmark service.
The pinned revision is `4322e5726e6334590d251fc4f92bec0efafc45dc` (`v1.22.2`).
Neutral run bundles are the durable record. The `qortoo` website consumes their
JSON directly and owns presentation. The Action's default Pages dashboard is not
deployed.

## Why this integration

The Action accepts arbitrary named numeric measurements with a unit, dispersion
text, and additional context. Separate smaller/bigger tools support latency and
throughput. External JSON mode lets the publisher control storage and lets the
website use its existing static build without another dashboard service.
No service account is introduced; CI runners, execution minutes, and Git storage
remain operational costs. Stable performance measurements still require a
defined runner environment; a tracking tool does not supply that guarantee.

The local PoC executes the pinned distributed Action against Counter and Variable
exports, appends each twice, and verifies all metrics and SDK provenance survive
JSON history export. Synthetic server latency and throughput fixtures verify both
directions. This establishes local compatibility, not remote CI or publication.
The Action declares Node 24; the PoC records the local Node version separately.
Remote runtime validation belongs to the central workflow.

The following responsibilities remain with Qortoo:

- The default commit link identifies the central harness, not either SDK. The
  Action's `extra` field and neutral summary retain both full SDK SHAs and repository
  URLs, harness identity, contract identity, environment, run ID, and run URL.
- Native alert selection looks for an earlier *different harness commit*. SDK-only
  changes can share that commit, so native alerts are disabled. Measurements are
  collected without performance thresholds or PR blocking.
- External JSON append does not provide concurrency control or durable storage.
  The publisher owns serialization, integrity checks, retries, and immutable runs.
  In particular, the Action can treat unreadable history as empty; the publisher
  must validate existing history before invoking it and fail on corruption.

These limitations fit the current collection and documentation scope. SDKs
dispatch measurement requests to this central workflow, which publishes
validated results for the website.

## Export a measured run

Provide an environment JSON object with `id`, `os`, `arch`, `cpu`, and `runner`.
For example:

```json
{
  "id": "local-m4-pro",
  "os": "macOS-27.0.1-arm64-arm-64bit",
  "arch": "arm64",
  "cpu": "Apple M4 Pro",
  "runner": "local workstation"
}
```

`os` must match the run's recorded platform. Legacy environment details are
operator-supplied and do not retroactively prove CPU identity or host isolation.
The central workflow captures environment details on the measurement host and
retains them in metadata before export. Official export requires this captured
environment.

```sh
python3 scripts/export_results.py results/<run-id> \
  --environment /path/to/environment.json \
  --output results/<run-id>/export
```

The default channel is `exploratory`. Official export additionally requires
`--channel official --run-url https://github.com/qortoo/qortoo-bench/actions/runs/<id>`
and successful full measurements with a clean, committed harness baseline.
PR measurements use the separate `candidate` channel and carry base/head provenance.
The exporter refuses promotion of these runs to either official or exploratory
data, and the official publisher rejects candidate bundles. See [PR candidates](pr-candidates.md).
Channel eligibility does not prove that a request came from an allowed main
revision: the trusted publisher verifies event/source provenance separately.
PR candidates, dirty runs, smoke runs, and failed runs never enter official latest
selection. Successful dirty full runs can be exported as exploratory data.

Export revalidates the recorded input snapshot, all full/warmup samples, exact
round sets, and the comparison summary. It does not resolve today's SDK heads or
replace recorded provenance. Output directories must be new.

Each bundle contains:

| File | Meaning |
| --- | --- |
| `summary.json` | Versioned neutral document input, complete statistics, native SDK/ABI manifest and checksums, series definitions, and provenance |
| `metrics/<suite>.json` | Action custom JSON; one median per scenario/implementation |
| `manifest.json` | File SHA256 values, original input hashes, raw member hashes, Action pin and invocation groups |
| `raw.tar.gz` | Deterministic lossless archive of top-level run JSON, Markdown, and stdout/stderr logs |
| `index-entry.json` | Run identity, channel/latest eligibility, series IDs, and manifest/summary paths and hashes |

Executables and build caches are not archived. Raw samples and logs are retained
indefinitely with successful published runs; gzip compression does not discard
rounds. The exporter rejects raw archives above 25 MiB rather than silently
dropping evidence. Larger workloads require an explicit storage/splitting policy
before publication. Failed/candidate runs remain diagnostic Actions artifacts
for 30 days and do not modify official history. Actual retention configuration
and publication automation are part of the central workflow stage.

## Metric and series identity

Current SDK metrics are latency in `ns/op`, smaller is better. The chart value is
the median of ten **round means**, not the median of individual operation times.
`range` describes standard deviation across those means. The saved p95 is the
nearest-rank p95 of ten round means (therefore their maximum), not a request tail
latency percentile. Warmup is excluded. The neutral summary preserves every
statistic and the raw archive preserves each round.

Series names are `<channel>/<suite>/<sha256>`. The digest covers the complete
suite, canonical contract, protocol version, build profile/features, declared
and actual toolchains, source repository identities, and environment object.
Canonical JSON is UTF-8, sorted keys, two-space indentation, and a final newline.
SDK and harness commit SHAs are point provenance, not series keys; SDK revisions
must vary to show a trend. Bump `qortoo-public-api-round-mean/v1` when harness timing
semantics change without an equivalent contract change. Build-specific header
and library hashes remain in the run, so rebuilding a changed SDK does not create
a new series by itself.

CPU/runner/OS, contract, metric unit/direction, or toolchain changes split series.
Different series must not be presented as one comparable trend. SDK API path
differences include serialization, FFI, and runtime work, not pure binding overhead.
Synthetic [server metrics](../tests/fixtures/server-metrics.json) demonstrate separate
`customSmallerIsBetter` latency and `customBiggerIsBetter` throughput input files.
They do not implement a server workload; its collector/contract remains future work.

## Durable storage and publication transaction

The selected store is a dedicated **`bench-data` branch in `qortoo-bench`**. It
contains data only; it is not a Pages deployment or a generated SDK documentation
branch. Temporary Actions artifacts and cache are not the long-term source.

```text
runs/<run-id>/                 immutable exported bundle
history/<channel>/<suite>/<series-hash>/<year>.json
index/<year-month>.json        append-only run index entries
index.json                    index shard references and per-series latest pointers
```

History uses yearly shards with no Action `max-items-in-chart` truncation.
Individual data files have a 25 MiB budget; stop publication and introduce another
shard before exceeding it. Run bundles and index entries are never overwritten.
The run ID must include an attempt discriminator; retries of the exact bundle
are idempotent, while another attempt receives a distinct ID and remains visible.
An existing run ID with different hashes is an error.

The trusted publisher performs one transaction:

1. Validate the central event, selected SDK commits, environment, bundle schema,
   hashes, baseline eligibility, and existing history/index. Measurement jobs have
   read-only repository permissions and receive no publication credentials.
2. Read the current `bench-data` tip in an optimistic transaction and stage the
   immutable bundle. An already indexed identical bundle needs no history append.
3. Run the pinned Action once per manifest group with that group's `tool`,
   `name`, `output_file`, and yearly `history_file`. Use external JSON mode,
   `auto-push: false`, `save-data-file: true`, no comment/alert flags, and no
   `max-items-in-chart`. Do not let the Action switch/push branches itself.
4. Append the index entry and update eligible per-series latest pointers in the
   same data commit. Latest advancement requires a source revision vector that
   equals or descends from the published vector in every SDK repository. Older
   or incomparable vectors remain in history without replacing latest. A retry
   of the same source vector is selected by its validated request/attempt order,
   not job completion time.
5. Push that commit without force. On a competing update, reload the tip and
   rebuild the entire append/index transaction, including duplicate detection;
   do not overwrite an already merged history file. Bound retries and report an
   explicit failed publication rather than losing history. Queue/retry handling
   must retain every main request; a concurrency group alone is not a FIFO queue.

Request registration/finalization and the publisher have Contents write access
to the data branch, in jobs that never execute SDK code. A separate
website-refresh credential can request the existing `qortoo` documentation build.
The Action's `ref` must identify the measured harness for manual dispatch fallback;
its event `head_commit` takes precedence when present, so verify that attribution
against the manifest. This is never a substitute for the SDK SHA fields.

The [central workflow](central-workflow.md) implements this transaction.
The exporter and local PoC remain local operations and never push data or dispatch
a website build. The request ledger and publisher share optimistic branch transactions. Website
refresh is a separate integration.

## Website consumption

The document build resolves `bench-data` to one immutable commit, verifies the
index schema and manifest/summary SHA256 at that commit, and renders `summary.json`.
It never evaluates Action `data.js` or selects points from mutable branch-head URLs.
The synthetic [summary fixture](examples/benchmark-summary.json) demonstrates the
document input schema without representing measured performance.

The website owns tables/charts. Display source repository/SHA pairs, harness SHA,
contract and series identity, environment, measurement date, and Actions/raw links.
Use only official eligible latest pointers for public current results. On failure,
retain the previous successful revision/date; if none exists, show no measurement.
Record the data commit, run ID, summary hash, contract hash and SDK revisions in
the website's generated source manifest. The website validates index shards and
bundle manifest/summary hashes before generating its Performance tables and
series-specific charts.

`documentation-refresh.yml` runs after the central main benchmark completes,
including failed requests so the website can refresh status without replacing
successful values. It dispatches `qortoo/qortoo`'s `docs.yml` using `WORKFLOW_PAT`
with Actions write access to that repository. The job executes no SDK code or
downloaded artifacts. Merge the website integration before enabling this caller.
The website resolves the current data tip itself; notifications do not select
results. Dispatch acceptance is not deployment success. A manual run of the
refresh workflow retries documentation publication without rerunning benchmarks.
See the website's `docs/benchmark-results.md` for collection and display rules.

## Reproduce the Action compatibility check

Check out the pinned Action outside this repository, then run:

```sh
python3 scripts/check_benchmark_action.py \
  --action-directory /path/to/github-action-benchmark \
  --bundle results/<run-id>/export \
  --output results/action-poc
```

The distributed Action and dependencies are used directly. A local event fixture
supplies harness commit context; tokens, comments, pushes, and external uploads
are disabled. Output includes history JSON, command logs, and `verification.json`.
Its synthetic server points are PoC evidence and never official result bundles.

Upstream references (checked 2026-10-18):
[custom input and external JSON](https://github.com/benchmark-action/github-action-benchmark),
[pinned Action inputs/runtime](https://github.com/benchmark-action/github-action-benchmark/blob/4322e5726e6334590d251fc4f92bec0efafc45dc/action.yml),
[commit attribution](https://github.com/benchmark-action/github-action-benchmark/blob/4322e5726e6334590d251fc4f92bec0efafc45dc/dist/src/extract.js),
[history append and previous-commit selection](https://github.com/benchmark-action/github-action-benchmark/blob/4322e5726e6334590d251fc4f92bec0efafc45dc/dist/src/addBenchmarkEntry.js).

## SDK main request provenance

Central event results include a request ID and the verified SDK main push origin.
The canonical resolved source lock and attempt outcomes live in `requests/` on
`bench-data`. Successful publication updates the request outcome in the same Git
commit as immutable bundles and indexes. Failed/incompatible attempts retain the
previous successful latest pointers. Duplicate published requests do not append
history. See the [central workflow guide](central-workflow.md) for retries.
