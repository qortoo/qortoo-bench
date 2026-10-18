# Central manual benchmark workflow

The public repository is [qortoo/qortoo-bench](https://github.com/qortoo/qortoo-bench).
**Harness CI** validates contracts, aggregation, export, and publication on main
pushes and PRs. **Central benchmark** is manually dispatched on `main`; SDK event
callers and website deployment are separate integrations.

## Run and retry

```sh
gh workflow run benchmark.yml --repo qortoo/qortoo-bench --ref main
```

Blank inputs use `sources.lock.json`. Explicit source selection is also supported:

```sh
gh workflow run benchmark.yml --repo qortoo/qortoo-bench --ref main \
  -f rust_sha=859bb8330d4ffc74241437b7ef4e9685b7bd7ba6 \
  -f go_sha=e54206f329bbbb5fd7852e1de41c68f1a12e9d66
```

Only full lowercase commit SHAs and registered SDK repositories are accepted.
Unknown commits fail checkout; Go's CI Rust pin must match the selected Rust SHA.
The tracked default lock is never modified. The ignored resolved lock and host
environment are hashed into the input snapshot.

Both SDKs run on one fresh `ubuntu-24.04` hosted runner, with Rust 1.97.1, Go 1.27.2,
release builds and `observability`. Native SDK staging precedes forced Go rebuilds
and tests (`-a -count=1`). One warmup and ten alternating measured rounds produce
34 warmup and 340 measured samples. CPU model/count, image, architecture and
OS/kernel are captured before measurement. Different environments create separate
series. A hosted label alone does not provide dedicated hardware or stable timing.

Result IDs are `<Actions-run-id>-<run-attempt>`. Re-run **all jobs** to create
another immutable measurement attempt. If publication failed, retry all jobs so
measurement artifacts and attempt identity agree. Re-publishing an already indexed
identical bundle is idempotent and does not append another history point.

## Permission and publication boundary

The measurement job has `contents: read`, no persisted checkout credentials, and
no publication secret. It builds SDK code and uploads a candidate artifact.

A separate publisher has `contents: write`. It checks out the same trusted harness
commit and never builds or executes downloaded SDK code or binaries. It checks
main/manual context, source selection, run/attempt URL, harness file hashes,
baseline eligibility and bundle hashes. It bounds regular raw archive members,
revalidates all samples and reconstructs the export byte for byte before writing.
Official export requires the environment recorded on the measurement host;
legacy operator-supplied descriptions remain exploratory.

The publisher runs the pinned tracking Action under Node 24 and pushes one data
commit to `bench-data`: immutable bundle, history, monthly index, and latest
pointers together. See the [publication contract](result-publishing.md) for units,
provenance, storage limits, and source ancestry ordering. A push conflict causes a
fresh transaction, with three attempts maximum. Corrupt existing history/index
fails explicitly instead of being treated as empty.

Publisher jobs are serialized with cancellation disabled. GitHub concurrency is
not a durable FIFO queue: superseded pending manual runs may require a full retry.
SDK automation must add request-ledger/recovery behavior before claiming every
main event is preserved. That automation is outside this manual workflow.

## Inspect results

```sh
gh run list --repo qortoo/qortoo-bench --workflow benchmark.yml
gh run view <run-id> --repo qortoo/qortoo-bench
gh run download <run-id> --repo qortoo/qortoo-bench \
  --name candidate-<run-id>-<attempt> --dir /tmp/qortoo-bench-candidate
```

The measurement summary contains same-host comparisons; the publisher summary
records the data commit. Inspect `index.json` and `runs/<run-id>-<attempt>/` at
that immutable `bench-data` commit, including `manifest.json`, `summary.json` and
`raw.tar.gz`. The neutral summary and raw metadata include native ABI/profile and
header/library checksum provenance.

Diagnostics and candidate artifacts expire after 30 days. Published bundles stay
in the data branch. Failed measurement skips publication; failed publication does
not advance the branch. A public Performance page requires the website integration.
