#!/usr/bin/env python3
"""Validate registered SDK suites against pinned sources and shared contracts."""

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
    validate_compatibility,
    validate_build_dependencies,
)
from suite_registry import VALIDATORS, load_suite


ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--go", default="go", help="Go executable matching sources.lock.json")
    parser.add_argument("--source-root", type=Path, help="directory containing local qortoo-rs/qortoo-go repositories")
    parser.add_argument("--lock-file", type=Path, default=ROOT / "sources.lock.json")
    parser.add_argument("--suite", action="append", choices=sorted(VALIDATORS), help="suite to validate; repeat to select several (default: all)")
    args = parser.parse_args()
    lock_path = args.lock_file.resolve()
    lock_path.relative_to(ROOT)
    lock = json.loads(lock_path.read_text())
    names = list(dict.fromkeys(args.suite or sorted(VALIDATORS)))
    resolved = [load_suite(ROOT, lock, name) for name in names]
    input_paths = [lock_path, ROOT / "components.json"]
    for suite, contract, contract_path in resolved:
        input_paths.extend([ROOT / f"suites/{suite['id']}.json", contract_path])
    input_hashes = {str(p.relative_to(ROOT)): checksum(p) for p in input_paths}

    env = setup_environment(lock)
    go, go_version, rust_version = check_toolchains(lock, args.go, env)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = ROOT / "results" / f"{stamp}-{uuid.uuid4().hex[:8]}"
    output.mkdir(parents=True)
    metadata = {
        "schema_version": 3, "status": "running", "performance_baseline": False,
        "suite_ids": names, "profile": "smoke",
        "input_files_sha256": input_hashes, "sources": lock["sources"],
        "build_profile": lock["profile"], "rust_features": lock["rust_features"],
        "rustc": rust_version, "go": go_version, "platform": platform.platform(),
        "started_at_utc": stamp, "commands": [],
    }
    inputs = {
        "schema_version": 2, "suites": [{"suite": suite, "contract": contract} for suite, contract, _ in resolved],
        "sources": lock["sources"], "toolchains": lock["toolchains"],
        "profile": "smoke", "build_profile": lock["profile"],
        "rust_features": lock["rust_features"], "input_files_sha256": input_hashes,
        "components": json.loads((ROOT / "components.json").read_text()),
    }
    (output / "run-inputs.json").write_text(json.dumps(inputs, indent=2) + "\n")
    samples = []

    def run(label, command, cwd=ROOT):
        print(f"{label}: {' '.join(map(str, command))}", flush=True)
        metadata["commands"].append({"label": label, "argv": list(map(str, command)), "cwd": str(cwd)})
        with (output / f"{label}.stdout.log").open("w") as stdout, (output / f"{label}.stderr.log").open("w") as stderr:
            subprocess.run(command, cwd=cwd, env=env, stdout=stdout, stderr=stderr, check=True)

    try:
        run("contract-tests", [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"])
        prepare_sources(ROOT, lock, args.source_root, run)
        metadata["native_sdk"] = stage_native_sdk(ROOT, env, run)
        validate_compatibility(ROOT, go, run)
        binaries = build_harnesses(ROOT, resolved, output, env, go, run)
        metadata["build_dependencies"] = inputs["build_dependencies"] = json.loads((output / "build-dependencies.json").read_text())
        (output / "run-inputs.json").write_text(json.dumps(inputs, indent=2) + "\n")

        for suite, contract, contract_path in resolved:
            for scenario in contract["scenarios"]:
                for implementation, adapter in suite["implementations"].items():
                    binary = binaries[(implementation, adapter["harness"], adapter["executable"])]
                    label = f"{suite['id']}-{implementation}-smoke-{scenario['id']}"
                    run(label, [str(binary), str(contract_path), "smoke", scenario["id"]])
                    sample = json.loads((output / f"{label}.stdout.log").read_text())
                    VALIDATORS[suite["id"]].validate_sample(sample, contract, scenario, implementation, "smoke")
                    samples.append(sample)
        for name, source in lock["sources"].items():
            validate_source(ROOT / "_sources" / name, source["sha"])
        validate_build_dependencies(ROOT, metadata["build_dependencies"])
        for path in input_paths:
            if checksum(path) != input_hashes[str(path.relative_to(ROOT))]:
                raise RuntimeError(f"input changed during execution: {path}")
        metadata["status"] = "passed"
        print(f"Suite smoke passed: {len(samples)} validated samples across {names}; performance baseline: false", flush=True)
    except Exception as error:
        metadata.update(status="failed", error=str(error))
        raise
    finally:
        finalize_metadata(ROOT, output, metadata, len(samples))
        (output / "samples.json").write_text(json.dumps(samples, indent=2) + "\n")
        (output / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
        print(f"Smoke evidence: {output}", flush=True)


if __name__ == "__main__":
    main()
