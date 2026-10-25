# Central benchmark workflow

Use **Central benchmark** for official main measurements and manual runs.
For the overall flow and PR comparisons, see [Performance](performance.md).

## Select inputs

| Request | Rust revision | Go revision |
| --- | --- | --- |
| Rust main push | Exact push SHA | Go main, resolved once |
| Go main push | Rust CI pin in the pushed Go commit | Exact push SHA |
| Manual run | Source lock, or `rust_sha` override | Source lock, or `go_sha` override |

SDK requests must identify the original main push SHA and caller run ID.
The receiver validates that origin before accepting the request. Retries keep the
original resolved inputs, even if main advances. To change a dependency, start a
new manual measurement.

## Set up access and runners

Each SDK caller needs `WORKFLOW_PAT` with **Actions write** access to
`qortoo/qortoo-bench`. Enable the central workflow on main before its callers.
A successful dispatch means the request was accepted, not that measurement or
publication succeeded.

Enable the Ubicloud GitHub App for qortoo-bench. Measurements use one host for
both SDKs; other jobs use GitHub-hosted runners. See the
[workflow](../.github/workflows/benchmark.yml) for runner and toolchain settings,
and [Ubicloud settings](https://www.ubicloud.com/docs/github-actions-integration/runner-types)
for fleet selection. A runner label does not guarantee identical CPU hardware;
compare results only within matching environments.

## Run and recover

```sh
# Run with the committed source lock.
gh workflow run benchmark.yml --repo qortoo/qortoo-bench --ref main

# Select exact SDK revisions.
gh workflow run benchmark.yml --repo qortoo/qortoo-bench --ref main \
  -f rust_sha=<full-rust-sha> -f go_sha=<full-go-sha>

# Retry an existing request with its original inputs.
gh workflow run benchmark.yml --repo qortoo/qortoo-bench --ref main \
  -f request_id=<request-id>

# Recover a Go main request that never reached the central ledger.
gh workflow run benchmark.yml --repo qortoo/qortoo-bench --ref main \
  -f source_component=go -f source_sha=<full-source-sha> \
  -f source_run_id=<original-sdk-main-push-run-id>
```

Use `source_component=rust` for Rust request recovery. The SDK's **Central
benchmark request** workflow also accepts the original `source_sha` and
`source_run_id` for recovery on main.

Use a recovery command or rerun **all jobs**; rerunning only failed jobs does not
register a new attempt. Supply `request_id` alone when retrying an existing request.
Active or already published requests skip duplicate measurement.

## Inspect results

```sh
gh run list --repo qortoo/qortoo-bench --workflow benchmark.yml
gh run view <central-run-id> --repo qortoo/qortoo-bench
```

The run summary and request record on `bench-data` identify the selected inputs,
status, and attempt. SDK request IDs are `rust-<sha>` or `go-<sha>`; manual request
IDs are `manual-<central-run-id>`.

| Outcome | Meaning |
| --- | --- |
| `published` | Measurement and official result publication succeeded. |
| `incompatible` | The selected Go/native SDK combination failed compatibility tests. |
| `failed` / `cancelled` | The attempt did not complete publication; inspect its jobs and diagnostics before retrying. |

Official bundles and request records remain on `bench-data`; diagnostic Actions
artifacts expire after 30 days. Failures retain previous successful results.
For result files, interpretation, and publication rules, see
[Results](results.md) and the [publication contract](result-publishing.md).

After completion, the website refreshes when the current data snapshot is not
already public. A failed snapshot lookup also requests refresh. A manual run of
[Refresh benchmark documentation](../.github/workflows/documentation-refresh.yml)
always requests a rebuild without rerunning measurements. Verify the website's
separate Documentation run before treating results as publicly visible.
