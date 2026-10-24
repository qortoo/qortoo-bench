# Central benchmark workflow

For the shared workflow overview and result locations, see [Performance](performance.md).

[Qortoo Bench](https://github.com/qortoo/qortoo-bench) owns official workloads,
measurement runs, the request ledger, and result history. SDK `main` push callers
only dispatch inputs; they never check out or execute SDK code with a dispatch
credential. This guide covers official main/manual runs. [PR candidate measurements](pr-candidates.md)
use a separate receiver and never publish official results.

## Source selection

A caller sends its component, exact push SHA, and source Actions run ID. The central
resolver verifies that the registered repository's `.github/workflows/benchmark.yml`
run is a `push` to `main` at that SHA. It never replaces the changed source with a
newer commit.

- Rust event: use the event Rust SHA and resolve Go `main` once.
- Go event: use the event Go SHA and read the Rust CI pin at that exact Go commit.
- Manual measurement: use the source lock, with optional full-SHA overrides.

The first accepted request stores the complete resolved lock in
`requests/<component>-<sha>.json` on `bench-data`. Manual requests use
`manual-<central-run-id>`. Duplicate deliveries and retries use that original lock,
including its original dependency revision, even if `main` has advanced.

The source pin is compatibility metadata, not a requirement that prevents testing
a new Rust commit. The central runner stages the selected release native SDK,
checks the Go package's runtime ABI contract, and runs its actual functional tests
with `go test -a -count=1 -race ./...` before benchmarks. A Go/native test failure
records `incompatible`; build, measurement, and publication failures record
`failed`. Both retain evidence and leave successful history/latest pointers intact.

## Credential setup

Each SDK caller uses its `WORKFLOW_PAT` Actions secret. The token must grant
`Actions: write` on **qortoo/qortoo-bench** in addition to any repositories
where that secret is already used. The benchmark caller does not need SDK write
access or a result-publishing credential. Tokens must be provisioned and rotated
through repository or organization secret settings, not committed. A missing
credential or rejected API request fails the SDK notification job with an
explicit recovery command.

The receiver workflow must be present on central `main` before SDK callers are
activated. Dispatch acceptance is not proof of measurement or publication success.

## Run and retry

```sh
# Manual run with locked defaults.
gh workflow run benchmark.yml --repo qortoo/qortoo-bench --ref main

# Manual run with exact source overrides.
gh workflow run benchmark.yml --repo qortoo/qortoo-bench --ref main \
  -f rust_sha=<full-rust-sha> -f go_sha=<full-go-sha>

# Retry a registered failed, incompatible, or abandoned request, keeping its lock.
gh workflow run benchmark.yml --repo qortoo/qortoo-bench --ref main \
  -f request_id=rust-<full-source-sha>

# Recover an SDK dispatch that never reached the central ledger.
gh workflow run benchmark.yml --repo qortoo/qortoo-bench --ref main \
  -f source_component=go -f source_sha=<full-source-sha> \
  -f source_run_id=<original-sdk-main-push-run-id>
```

The SDK's **Central benchmark request** workflow can also be manually dispatched
on `main` with the original `source_sha` and `source_run_id`. A manual caller run
does not masquerade as a new main push. The receiver validates the original event.
Use a recovery command or rerun **all jobs**; rerunning only failed jobs does not
register a new attempt. Retry inputs are exclusive of source-event and SHA override
inputs. To deliberately change a dependency, submit a separate manual measurement.

Every attempt is identified by `<central-run-id>-<run-attempt>`. The ledger records
its central run URL and status. An active duplicate skips measurement. A cancelled
run that could not finalize can be reclaimed after the Actions API confirms its
old attempt is completed. Retries of published requests skip rather than add
another official point. A late failure finalizer cannot overwrite a newer attempt
or a successful publication. Failed request-registration transactions are visible
as failed central jobs and can be resubmitted using the same source event.

## Measurement and publication

Both SDKs run on one fresh Ubicloud x64 Ubuntu 24.04 runner
(`ubicloud-standard-4-ubuntu-2404`, 4 vCPU, 16 GB RAM) with Rust 1.97.1, Go 1.27.2,
release builds and `observability`. Forced Go rebuilds precede measurements. Rust harness builds use a runtime copy
seeded with the selected SDK Cargo.lock, resolved with `cargo update --workspace`
and then built with `--locked`. Runtime Go adapters resolve their module graph
with `go mod tidy` and build/test with `-mod=readonly`. Tracked harness files are
never rewritten. Effective Cargo.lock/go.mod/go.sum snapshots and hashes are
retained in raw evidence and form part of the series identity, so changed build
dependencies do not share a comparison series.
One warmup and ten alternating measured rounds produce 34 warmup and 340 measured
samples. CPU model/count, runner image, architecture, and OS/kernel are captured
on the measurement host. Different environments form separate series; a shared
hosted label does not guarantee comparable hardware or stable timing.

The Ubicloud GitHub App must be enabled for `qortoo-bench`. The runner label
selects size and OS; the account-level **Enable Premium Runners** setting selects
the fleet. Disable it to request Standard CPUs. With Premium enabled, Ubicloud
may fall back to Standard when capacity is busy. The label alone does not pin a
CPU model. Runner images also receive provider updates. See the
[Ubicloud runner documentation](https://www.ubicloud.com/docs/github-actions-integration/runner-types).
Only measurement and PR comparison use Ubicloud; request registration,
publication, failure recording, and harness validation use GitHub-hosted runners.
Results record the Ubicloud provider and actual CPU/OS; they form separate series
from GitHub-hosted measurements.

Request registration has repository write permission but never executes SDK code.
Measurement has `contents: read`, no persisted checkout credentials, and no
publication secret. It uploads a candidate bundle and diagnostics. The publisher
has `contents: write`, checks out the same trusted harness, revalidates raw samples
and provenance, and reconstructs the bundle without executing SDK code/binaries.
The publisher verifies the canonical ledger selection and active attempt as well
as the source SHAs, harness hashes, baseline eligibility, and bounded archive hashes.

Immutable bundle, tracking Action history, monthly index, latest pointers, and
successful request outcome are committed together to `bench-data`. Writers retry
fresh Git transactions on push conflicts and never force-push. There is no
workflow/job concurrency group that silently replaces pending requests. Exhausted
transaction retries fail explicitly and preserve manual recovery. Source ancestry
and request order keep older completed runs from replacing newer successful
latest pointers; see the [publication contract](result-publishing.md).

## Inspect results

```sh
gh run list --repo qortoo/qortoo-bench --workflow benchmark.yml
gh run view <central-run-id> --repo qortoo/qortoo-bench

gh api repos/qortoo/qortoo-bench/contents/requests/go-<sha>.json?ref=bench-data
```

Resolve `bench-data` to an immutable commit when inspecting requests, `index.json`,
`runs/<id>/manifest.json`, `summary.json`, and `raw.tar.gz`. Source event provenance
and request IDs are included in result manifests and summaries. Diagnostics and
candidate artifacts expire after 30 days; published bundles and request records
remain on the data branch. A failed run does not advance successful results.
Website result display/refresh and central PR measurements are separate integrations.

API references: [workflow dispatch permissions and response](https://docs.github.com/en/rest/actions/workflows#create-a-workflow-dispatch-event),
[conservative workspace lock resolution](https://doc.rust-lang.org/cargo/commands/cargo-update.html).
