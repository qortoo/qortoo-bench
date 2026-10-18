#!/usr/bin/env python3
"""Execute full multi-round benchmark measurements across registered SDK suites."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import platform
import subprocess
import sys
import uuid

from bench_support import (
    build_harnesses,
    check_toolchains,
    checksum,
    finalize_metadata,
    prepare_sources,
    setup_environment,
    stage_native_sdk,
    validate_source,
)
from comparison import (
    build_comparison_summary,
    generate_markdown_report,
    validate_measured_samples,
)
from suite_registry import VALIDATORS, load_suite


ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--go", default="go", help="Go executable matching sources.lock.json")
    parser.add_argument("--source-root", type=Path, help="directory containing local qortoo-rs/qortoo-go repositories")
    parser.add_argument("--suite", action="append", choices=sorted(VALIDATORS), help="suite to measure; repeat to select several (default: all)")
    args = parser.parse_args()

    lock = json.loads((ROOT / "sources.lock.json").read_text())
    names = list(dict.fromkeys(args.suite or sorted(VALIDATORS)))
    resolved = [load_suite(ROOT, lock, name) for name in names]

    input_paths = [ROOT / "sources.lock.json", ROOT / "components.json"]
    for suite, contract, contract_path in resolved:
        input_paths.extend([ROOT / f"suites/{suite['id']}.json", contract_path])
    input_hashes = {str(p.relative_to(ROOT)): checksum(p) for p in input_paths}

    env = setup_environment(lock)
    go, go_version, rust_version = check_toolchains(lock, args.go, env)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = ROOT / "results" / f"{stamp}-{uuid.uuid4().hex[:8]}"
    output.mkdir(parents=True)

    metadata = {
        "schema_version": 3,
        "status": "running",
        "performance_baseline": True,
        "suite_ids": names,
        "profile": "full",
        "input_files_sha256": input_hashes,
        "sources": lock["sources"],
        "build_profile": lock["profile"],
        "rust_features": lock["rust_features"],
        "rustc": rust_version,
        "go": go_version,
        "platform": platform.platform(),
        "started_at_utc": stamp,
        "commands": [],
    }
    inputs = {
        "schema_version": 2,
        "suites": [{"suite": suite, "contract": contract} for suite, contract, _ in resolved],
        "sources": lock["sources"],
        "toolchains": lock["toolchains"],
        "profile": "full",
        "build_profile": lock["profile"],
        "rust_features": lock["rust_features"],
        "input_files_sha256": input_hashes,
        "components": json.loads((ROOT / "components.json").read_text()),
    }
    (output / "run-inputs.json").write_text(json.dumps(inputs, indent=2) + "\n")

    warmup_samples = []
    measured_samples = []

    def run(label, command, cwd=ROOT):
        metadata["commands"].append({"label": label, "argv": list(map(str, command)), "cwd": str(cwd)})
        with (output / f"{label}.stdout.log").open("w") as stdout, (output / f"{label}.stderr.log").open("w") as stderr:
            subprocess.run(command, cwd=cwd, env=env, stdout=stdout, stderr=stderr, check=True)

    try:
        run("unit-tests", [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"])
        prepare_sources(ROOT, lock, args.source_root, run)
        metadata["native_sdk"] = stage_native_sdk(ROOT, env, run)
        binaries = build_harnesses(ROOT, resolved, output, env, go, run)

        # 1. Warmup round (1 discarded round per implementation/scenario)
        print("Executing discarded warmup rounds...", flush=True)
        for suite, contract, contract_path in resolved:
            suite_id = suite["id"]
            for scenario in contract["scenarios"]:
                sc_id = scenario["id"]
                for impl in ("rust", "go"):
                    adapter = suite["implementations"][impl]
                    binary = binaries[(impl, adapter["harness"], adapter["executable"])]
                    label = f"{suite_id}-{impl}-warmup-{sc_id}"
                    run(label, [str(binary), str(contract_path), "full", sc_id])
                    sample = json.loads((output / f"{label}.stdout.log").read_text())
                    VALIDATORS[suite_id].validate_sample(sample, contract, scenario, impl, "full")
                    sample["warmup"] = True
                    warmup_samples.append(sample)

        # 2. Measured rounds (10 rounds with interleaved execution order)
        num_rounds = 10
        print(f"Executing {num_rounds} measured rounds with interleaved implementation order...", flush=True)
        for r in range(num_rounds):
            order = ("rust", "go") if (r % 2 == 0) else ("go", "rust")
            print(f"  Round {r + 1}/{num_rounds} (order: {' -> '.join(order)})", flush=True)
            for suite, contract, contract_path in resolved:
                suite_id = suite["id"]
                for scenario in contract["scenarios"]:
                    sc_id = scenario["id"]
                    for impl in order:
                        adapter = suite["implementations"][impl]
                        binary = binaries[(impl, adapter["harness"], adapter["executable"])]
                        label = f"{suite_id}-{impl}-full-{sc_id}-r{r:02d}"
                        run(label, [str(binary), str(contract_path), "full", sc_id])
                        sample = json.loads((output / f"{label}.stdout.log").read_text())
                        VALIDATORS[suite_id].validate_sample(sample, contract, scenario, impl, "full")
                        sample["round_index"] = r
                        sample["warmup"] = False
                        measured_samples.append(sample)

        for name, source in lock["sources"].items():
            validate_source(ROOT / "_sources" / name, source["sha"])
        for path in input_paths:
            if checksum(path) != input_hashes[str(path.relative_to(ROOT))]:
                raise RuntimeError(f"input changed during execution: {path}")

        # Validate measured sample consistency
        validate_measured_samples(measured_samples, resolved, expected_rounds=num_rounds, expected_profile="full")

        # Build comparison summary and Markdown report
        comparison_summary = build_comparison_summary(measured_samples, resolved, metadata)
        report_md = generate_markdown_report(comparison_summary, metadata)

        (output / "warmup-samples.json").write_text(json.dumps(warmup_samples, indent=2) + "\n")
        (output / "samples.json").write_text(json.dumps(measured_samples, indent=2) + "\n")
        (output / "comparison.json").write_text(json.dumps(comparison_summary, indent=2) + "\n")
        (output / "comparison.md").write_text(report_md)

        metadata["status"] = "passed"
        print(f"\nMeasurement passed: {len(measured_samples)} validated samples across {names}; performance baseline: true", flush=True)
        print("\n" + report_md, flush=True)
    except Exception as error:
        metadata.update(status="failed", error=str(error))
        raise
    finally:
        finalize_metadata(ROOT, output, metadata, len(measured_samples))
        (output / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
        print(f"Benchmark run evidence saved at: {output}", flush=True)


if __name__ == "__main__":
    main()

