# PR candidate measurements

For the shared workflow overview and result locations, see [Performance](performance.md).

A candidate measurement compares a Qortoo SDK pull request's exact head with a
fixed base revision. Qortoo Bench owns the runner, contracts, comparison, and
artifacts. Candidate results are review evidence; they cannot advance official
history, latest pointers, or website performance data. No numeric threshold blocks
merging a PR.

## Event and trust boundaries

1. The SDK's **Benchmark PR event** workflow handles `opened`, `reopened`, and
   `synchronize` events for PRs targeting `main`. It checks out no SDK code and
   requests no repository permissions or secrets.
2. After that workflow succeeds, the SDK's default-branch **Central PR benchmark
   request** workflow handles `workflow_run`. It reads GitHub API metadata and
   dispatches `candidate.yml` in Qortoo Bench using `WORKFLOW_PAT`. It downloads
   neither candidate code nor artifacts.
3. The central resolver authenticates the original SDK event run's repository,
   workflow path, event type, successful outcome, and head SHA. It reads the PR
   from the API and requires an open PR targeting the registered SDK's `main`.
   A closed or changed head is recorded as superseded and is not measured.
4. The comparison job receives no repository permissions or dispatch secret.
   It fetches the exact trusted central commit over public HTTPS. No official
   request-ledger or publisher job is part of this workflow.

Fork PRs follow the same path. The resolver checks that the head repository is
public and belongs to the registered SDK's GitHub fork network. Fork code runs
only inside the comparison containers. GitHub's approval policy for the source PR
workflow still applies; the notifier does not bypass that policy. A PR that removes
or prevents its source event workflow cannot request an automatic comparison.

## Fixed sources

The base SHA is resolved from the PR's current `base.sha` when the central request
is accepted. The head must still match the original event's SHA. Both source locks
and the dependency selection are saved in the selection artifact before executing
any SDK code.

- Rust PR: compare the base and head Rust revisions with one resolved Go `main`.
- Go PR: compare the base and head Go revisions with the Rust CI pin declared by
  the base Go commit. The head's declared pin is retained as provenance. If the
  head changes that pin, the report states that both measurements still use the
  base pin; an incompatible head fails rather than silently changing dependencies.

The head repository may be a verified fork. Other dependencies remain in their
registered repositories. Official main/manual source resolution continues to
reject arbitrary repository URLs.

## Measurement isolation and ordering

The comparison job uses the same Ubicloud 4 vCPU Ubuntu 24.04 runner label and
account-level fleet selection as [official measurements](central-workflow.md#set-up-access-and-runners).
Only public runner provider/image identity is forwarded into the containers;
CPU and OS are observed inside each container.

The runner builds one trusted Docker image with the locked Rust/Go toolchain
versions and the central harness. Both legs use that exact image ID. Each leg has
its own disposable non-root container, read-only image filesystem, private work
and result volumes, CPU/memory/process limits, and no Linux capabilities. No host
checkout, credential directory, or Docker socket is mounted into the containers.
The only host bind is the public selection JSON, mounted read-only.

Dependency fetches and SDK builds run before measurement. Container network access
is disconnected before warmup. The inactive leg remains paused, including during
the other leg's build and timed samples. The host invokes the trusted image-owned
sample runner; container startup, pause/unpause, Docker calls, and build work are
outside each harness's recorded timing boundaries.

Each leg runs one discarded warmup and ten measured rounds for every scenario and
implementation, with the existing workload budgets and validators. Base/head are
adjacent for each case; their order reverses each measured round. Rust/Go order
also reverses each round. With Counter and Variable, each leg has 340 measured
samples and 34 warmup samples. Both share a host and resource limits, but shared
runner load can still affect observations.

Actual dependency lockfiles and hashes are retained for each leg. Different SDK
dependency graphs are visible inputs, not hidden adjustments. The comparison
checks matching contracts, environment, toolchains, image, and trusted harness.
It reports each median and standard deviation and the head's percentage change;
positive change means slower. It does not claim statistical significance.

SDK code is untrusted, including build scripts. Container isolation protects the
host and the other leg; it does not prove that a malicious PR reports honest timing
values. Treat the output as candidate evidence and review the code with the data.

## Inspect and recover

The SDK notifier's Actions summary links to the central run. In that run, inspect
the comparison job's summary and the `pr-candidate-<run>-<attempt>` artifact. It
contains the selection, actual execution order, candidate-only bundles, comparison
JSON/Markdown, status, and bounded diagnostics. Failed preparation, compatibility,
or measurement produces a failed workflow and diagnostic artifacts, not a success
comparison. Artifacts are retained for 30 days.

Repeated authenticated requests for the same SDK/PR/head may cancel duplicate
comparison jobs. Different heads use separate groups, so delayed events cannot
cancel a newer head. A head that changes before resolution is skipped; an already
accepted comparison may finish and remains labeled with its frozen head. Candidate
artifacts have no shared latest pointer. This policy is separate from the official
main request ledger, which retains accepted official main requests.

To recover a successful source event whose dispatch failed, rerun the SDK's
**Central PR benchmark request** workflow on `main` with `source_run_id` and
`pr_number`, or use the notifier's explicit central recovery command:

```sh
gh workflow run candidate.yml --repo qortoo/qortoo-bench --ref main \
  -f source_component=go -f source_pr=<number> \
  -f source_sha=<full-head-sha> -f source_run_id=<SDK-PR-event-run-id>
```

The successful PR event run must come from `.github/workflows/benchmark-pr.yml`.
Rerunning an old event after the PR head changes does not substitute the new head.
The event workflow must first be available in the SDK, and the central receiver
must be on central `main` before activating the SDK completion notifier.

## Implementation and validation

- [Input authentication and source freezing](../scripts/pr_inputs.py) and
  [candidate source invariants](../scripts/candidate_contract.py).
- [Container orchestration and comparison](../scripts/candidate_compare.py),
  [isolated leg execution](../scripts/candidate_leg.py), and the
  [trusted image](../docker/candidate.Dockerfile).
- [Candidate workflow](../.github/workflows/candidate.yml) and
  [contract/security/ordering tests](../tests/test_candidates.py).
- [Official execution](central-workflow.md) and [result export](result-publishing.md)
  define separate publication boundaries.

Official GitHub references: [workflow-run events](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#workflow_run)
and [untrusted workflow input and privileges](https://docs.github.com/en/actions/reference/security/securely-using-pull_request_target).
