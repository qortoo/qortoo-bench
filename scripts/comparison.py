"""Statistical summary, validation, and Markdown report generation for benchmark measurements."""

import math
import statistics

from contract_support import require


def compute_statistics(values):
    require(bool(values), "values list must not be empty")
    for v in values:
        require(isinstance(v, (int, float)) and math.isfinite(v) and v >= 0, f"invalid measurement value: {v}")
    sorted_vals = sorted(values)
    count = len(sorted_vals)
    val_min = float(sorted_vals[0])
    val_max = float(sorted_vals[-1])
    val_mean = float(statistics.mean(sorted_vals))
    val_median = float(statistics.median(sorted_vals))
    val_stddev = float(statistics.stdev(sorted_vals)) if count > 1 else 0.0

    # 95th percentile using nearest rank method
    p95_idx = max(0, min(count - 1, math.ceil(0.95 * count) - 1))
    val_p95 = float(sorted_vals[p95_idx])

    return {
        "count": count,
        "min": val_min,
        "max": val_max,
        "mean": val_mean,
        "median": val_median,
        "stddev": val_stddev,
        "p95": val_p95,
    }


def validate_measured_samples(samples, resolved_suites, expected_rounds=10, expected_profile="full"):
    require(expected_rounds > 0, "expected_rounds must be positive")

    expected_cases = {}
    for suite, contract, _ in resolved_suites:
        suite_id = suite["id"]
        contract_id = contract["id"]
        for scenario in contract["scenarios"]:
            sc_id = scenario["id"]
            budget = scenario["budget"] if expected_profile == "full" else scenario["smoke_budget"]
            for impl in sorted(suite["implementations"]):
                key = (suite_id, sc_id, impl)
                expected_cases[key] = {
                    "contract_id": contract_id,
                    "budget": budget,
                }

    total_expected = len(expected_cases) * expected_rounds
    require(
        len(samples) == total_expected,
        f"expected {total_expected} total samples, got {len(samples)}",
    )

    grouped = {}
    for sample in samples:
        require(sample.get("profile") == expected_profile, f"expected profile {expected_profile}, got {sample.get('profile')}")
        require(sample.get("warmup") is False, "warmup sample must not be included in measured samples")

        r_idx = sample.get("round_index")
        require(
            isinstance(r_idx, int) and 0 <= r_idx < expected_rounds,
            f"invalid or missing round_index: {r_idx}",
        )

        contract_id = sample.get("contract_id", "")
        suite_id = contract_id.split("/")[0] if "/" in contract_id else ""
        scenario = sample.get("scenario")
        impl = sample.get("implementation")
        require(suite_id and scenario and impl, "sample missing suite/scenario/implementation")

        key = (suite_id, scenario, impl)
        require(key in expected_cases, f"unexpected or unsupported sample case: {key}")

        spec = expected_cases[key]
        require(
            contract_id == spec["contract_id"],
            f"sample contract_id mismatch: expected {spec['contract_id']}, got {contract_id}",
        )
        require(
            sample.get("operations") == spec["budget"],
            f"sample operations budget mismatch for {key}: expected {spec['budget']}, got {sample.get('operations')}",
        )

        elapsed = sample.get("elapsed_ns")
        require(isinstance(elapsed, int) and elapsed > 0, "invalid elapsed time")
        require(math.isfinite(sample.get("ns_per_op", -1)) and sample["ns_per_op"] > 0, f"invalid sample latency: {sample}")

        grouped.setdefault(key, []).append(sample)

    for key, spec in expected_cases.items():
        case_samples = grouped.get(key, [])
        require(
            len(case_samples) == expected_rounds,
            f"{key[0]}/{key[1]}/{key[2]}: expected exactly {expected_rounds} samples, got {len(case_samples)}",
        )
        round_indices = [s["round_index"] for s in case_samples]
        require(
            sorted(round_indices) == list(range(expected_rounds)),
            f"{key[0]}/{key[1]}/{key[2]}: round_index set must be exactly 0..{expected_rounds - 1}, got {sorted(round_indices)}",
        )


def build_comparison_summary(samples, resolved_suites, metadata):
    validate_measured_samples(samples, resolved_suites, expected_rounds=10, expected_profile="full")
    grouped = {}
    for sample in samples:
        suite_id = sample["contract_id"].split("/")[0]
        key = (suite_id, sample["scenario"], sample["implementation"])
        grouped.setdefault(key, []).append(sample["ns_per_op"])

    suites_summary = []
    for suite, contract, _ in resolved_suites:
        suite_id = suite["id"]
        scenarios_summary = []
        for scenario in contract["scenarios"]:
            sc_id = scenario["id"]
            rust_latencies = grouped[(suite_id, sc_id, "rust")]
            go_latencies = grouped[(suite_id, sc_id, "go")]
            rust_stats = compute_statistics(rust_latencies)
            go_stats = compute_statistics(go_latencies)

            diff_median = go_stats["median"] - rust_stats["median"]
            ratio_median = (go_stats["median"] / rust_stats["median"]) if rust_stats["median"] > 0 else 0.0

            entry = {
                "scenario_id": sc_id,
                "budget": scenario["budget"],
                "rust": rust_stats,
                "go": go_stats,
                "diff_median_ns_per_op": diff_median,
                "ratio_median": ratio_median,
            }
            if "payload" in scenario:
                entry["payload"] = scenario["payload"]
            if "operation" in scenario:
                entry["operation"] = scenario["operation"]
            scenarios_summary.append(entry)

        suites_summary.append({
            "suite_id": suite_id,
            "contract_id": contract["id"],
            "metric": suite["metric"],
            "scenarios": scenarios_summary,
        })

    return {
        "schema_version": 1,
        "performance_baseline": metadata.get("performance_baseline", False),
        "platform": metadata.get("platform"),
        "rustc": metadata.get("rustc"),
        "go": metadata.get("go"),
        "sources": metadata.get("sources"),
        "harness_sha": metadata.get("harness_sha"),
        "harness_dirty": metadata.get("harness_dirty"),
        "started_at_utc": metadata.get("started_at_utc"),
        "completed_at_utc": metadata.get("completed_at_utc"),
        "suites": suites_summary,
    }


def generate_markdown_report(summary, metadata):
    dirty = metadata.get("harness_dirty")
    dirty_str = str(dirty) if dirty is not None else "unknown"
    lines = [
        "# Same-Host Benchmark Comparison Report",
        "",
        f"- **Date**: {metadata.get('completed_at_utc', metadata.get('started_at_utc', 'N/A'))}",
        f"- **Platform**: `{metadata.get('platform', 'unknown')}`",
        f"- **Rust Toolchain**: `{metadata.get('rustc', 'unknown')}`",
        f"- **Go Toolchain**: `{metadata.get('go', 'unknown')}`",
        f"- **Harness SHA**: `{metadata.get('harness_sha', 'null')}` (dirty: `{dirty_str}`)",
        f"- **Rounds**: 10 measured rounds (1 discarded warmup) with interleaved execution order per round to mitigate host condition drift",
        "",
        "## Measurement Interpretation & Contract",
        "",
        "> [!NOTE]",
        "> Latency values are reported in `ns/op` where smaller numbers represent better performance.",
        "> Observed differences reflect the total path of the public API calls (including serialization, FFI, and runtime interactions),",
        "> not a pure CGO invocation overhead.",
        "> Go runtime allocations and Rust native heap allocations are not measured or reported by this latency harness (latency only).",
        "",
    ]

    for suite in summary["suites"]:
        suite_id = suite["suite_id"]
        lines.append(f"## Suite: `{suite_id}` (`{suite['contract_id']}`)")
        lines.append("")
        if suite_id == "counter":
            lines.append("| Scenario | Budget | Rust Median (ns/op) | Go Median (ns/op) | Diff (Go - Rust) | Ratio (Go / Rust) | Rust StdDev | Go StdDev |")
            lines.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
            for sc in suite["scenarios"]:
                rust_med = sc["rust"]["median"]
                go_med = sc["go"]["median"]
                diff = sc["diff_median_ns_per_op"]
                ratio = sc["ratio_median"]
                rust_std = sc["rust"]["stddev"]
                go_std = sc["go"]["stddev"]
                lines.append(
                    f"| {sc['scenario_id']} | {sc['budget']:,} | {rust_med:.1f} | {go_med:.1f} | {diff:+.1f} | {ratio:.2f}x | {rust_std:.1f} | {go_std:.1f} |"
                )
        elif suite_id == "variable":
            lines.append("| Scenario | Payload | Budget | Rust Median (ns/op) | Go Median (ns/op) | Diff (Go - Rust) | Ratio (Go / Rust) | Rust StdDev | Go StdDev |")
            lines.append("| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
            for sc in suite["scenarios"]:
                payload = sc.get("payload", "-")
                rust_med = sc["rust"]["median"]
                go_med = sc["go"]["median"]
                diff = sc["diff_median_ns_per_op"]
                ratio = sc["ratio_median"]
                rust_std = sc["rust"]["stddev"]
                go_std = sc["go"]["stddev"]
                lines.append(
                    f"| {sc['scenario_id']} | {payload} | {sc['budget']:,} | {rust_med:.1f} | {go_med:.1f} | {diff:+.1f} | {ratio:.2f}x | {rust_std:.1f} | {go_std:.1f} |"
                )
        lines.append("")

    return "\n".join(lines) + "\n"
