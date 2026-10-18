#!/usr/bin/env python3
"""Export a validated run as neutral document data and github-action-benchmark input."""

import argparse
import gzip
import hashlib
import io
import json
import math
from pathlib import Path
import re
import tarfile

from comparison import build_comparison_summary, validate_measured_samples
from contract_support import require
from suite_registry import VALIDATORS

ACTION = {
    "repository": "benchmark-action/github-action-benchmark",
    "revision": "4322e5726e6334590d251fc4f92bec0efafc45dc",
    "version": "v1.22.2",
}


def json_bytes(value):
    return (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def identity(value):
    return digest(json_bytes(value))


def action_metrics(rows, provenance):
    """Keep direction-specific metrics in separate Action invocations."""
    require(bool(rows), "no metrics")
    directions = {row["direction"] for row in rows}
    require(len(directions) == 1, "mixed improvement directions")
    direction = directions.pop()
    require(direction in {"smaller", "bigger"}, "unsupported metric direction")
    names = set()
    result = []
    for row in rows:
        require(row["name"] not in names, "duplicate metric name")
        names.add(row["name"])
        require(bool(row["unit"]), "missing metric unit")
        require(type(row["value"]) in (int, float) and math.isfinite(row["value"]) and row["value"] > 0, "invalid metric value")
        metric = {key: row[key] for key in ("name", "unit", "value")}
        metric["extra"] = json.dumps(provenance, sort_keys=True, allow_nan=False)
        if "stddev" in row:
            metric["range"] = f"stddev {row['stddev']:.6g} {row['unit']} across round means"
        result.append(metric)
    tool = "customSmallerIsBetter" if direction == "smaller" else "customBiggerIsBetter"
    return tool, result


def series_definition(suite, contract, inputs, metadata, environment):
    """SDK revisions vary within a series; contract, toolchain, and environment do not."""
    return {
        "protocol": "qortoo-public-api-round-mean/v1",
        "suite": suite,
        "contract_sha256": identity(contract),
        "environment": environment,
        "profile": inputs["profile"],
        "build_profile": inputs["build_profile"],
        "rust_features": inputs["rust_features"],
        "toolchains": inputs["toolchains"],
        "rustc": metadata["rustc"],
        "go": metadata["go"],
        "source_repositories": {name: source["repository"] for name, source in inputs["sources"].items()},
    }


def validate_run(run_dir, environment, channel, run_url, raw):
    require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", run_dir.name), "invalid run ID")
    inputs = json.loads(raw["run-inputs.json"])
    metadata = json.loads(raw["metadata.json"])
    samples = json.loads(raw["samples.json"])
    warmup = json.loads(raw["warmup-samples.json"])
    require(inputs["schema_version"] == 2 and metadata["schema_version"] == 3, "unsupported run schema")
    require(metadata["status"] == "passed", "only successful runs can be exported")
    require(inputs["profile"] == metadata["profile"] == "full", "only full runs can be exported")
    require(metadata["run_inputs_sha256"] == digest(raw["run-inputs.json"]), "input snapshot hash mismatch")
    for key in ("sources", "build_profile", "rust_features", "input_files_sha256"):
        require(inputs[key] == metadata[key], f"metadata/input mismatch: {key}")
    for source in inputs["sources"].values():
        require(re.fullmatch(r"[0-9a-f]{40}", source["sha"]), "source must use full commit SHA")
    require(inputs["build_profile"] == "release" and inputs["rust_features"] == ["observability"], "unsupported SDK build configuration")
    sdk = metadata["native_sdk"]
    require(sdk["manifest"]["profile"] == inputs["build_profile"], "native SDK profile mismatch")
    for key in ("header_sha256", "library_sha256"):
        require(re.fullmatch(r"[0-9a-f]{64}", sdk[key]), f"invalid native SDK {key}")
    for key in ("id", "os", "arch", "cpu", "runner"):
        require(isinstance(environment.get(key), str) and bool(environment[key].strip()), f"missing environment {key}")
    require(environment["os"] == metadata["platform"], "environment OS does not match measured platform")
    require(channel in {"official", "exploratory"}, "unsupported channel")
    if channel == "official":
        require(metadata.get("environment") == environment, "official export requires measurement-host environment")
        require(metadata["performance_baseline"] is True and metadata["harness_dirty"] is False, "official export requires a clean baseline")
        require(isinstance(metadata["harness_sha"], str) and re.fullmatch(r"[0-9a-f]{40}", metadata["harness_sha"]), "official export requires harness SHA")
        require(run_url and re.fullmatch(r"https://github.com/qortoo/qortoo-bench/actions/runs/[0-9]+(?:/attempts/[0-9]+)?", run_url), "official export requires central Actions run URL")

    resolved = []
    expected_warmup = set()
    for item in inputs["suites"]:
        suite, contract = item["suite"], item["contract"]
        name = suite["id"]
        require(name in VALIDATORS, "unsupported suite")
        VALIDATORS[name].validate_contract(contract)
        require(contract["id"].split("/")[0] == name, "suite/contract mismatch")
        require(set(suite["implementations"]) == {"rust", "go"}, "unsupported implementations")
        require(suite["metric"] == {"id": "latency", "unit": "ns/op", "direction": "smaller"}, "unsupported SDK metric")
        require(contract["execution"]["warmup_rounds"] == 1 and contract["execution"]["measured_rounds"] == 10, "unsupported round protocol")
        resolved.append((suite, contract, None))
        for scenario in contract["scenarios"]:
            for impl in suite["implementations"]:
                expected_warmup.add((contract["id"], scenario["id"], impl))
    require(len({s["id"] for s, _, _ in resolved}) == len(resolved), "duplicate suite")
    require(sorted(metadata["suite_ids"]) == sorted(s["id"] for s, _, _ in resolved), "suite snapshot mismatch")
    by_contract = {contract["id"]: (suite, contract) for suite, contract, _ in resolved}
    seen_warmup = set()
    for collection, is_warmup in ((samples, False), (warmup, True)):
        for sample in collection:
            require(sample["contract_id"] in by_contract, "unexpected sample contract")
            suite, contract = by_contract[sample["contract_id"]]
            scenario = next((s for s in contract["scenarios"] if s["id"] == sample["scenario"]), None)
            require(scenario is not None and sample["implementation"] in suite["implementations"], "unexpected sample case")
            VALIDATORS[suite["id"]].validate_sample(sample, contract, scenario, sample["implementation"], "full")
            require(sample.get("warmup") is is_warmup, "warmup flag mismatch")
            if is_warmup:
                key = (contract["id"], scenario["id"], sample["implementation"])
                require(key not in seen_warmup, "duplicate warmup")
                seen_warmup.add(key)
    require(seen_warmup == expected_warmup, "missing warmup")
    require(metadata["validated_samples"] == len(samples), "validated sample count mismatch")
    validate_measured_samples(samples, resolved)
    comparison = build_comparison_summary(samples, resolved, metadata)
    require(comparison == json.loads(raw["comparison.json"]), "comparison differs from validated samples/metadata")
    return inputs, metadata, comparison, resolved


def archive(files):
    buffer = io.BytesIO()
    with gzip.GzipFile(fileobj=buffer, mode="wb", mtime=0) as compressed:
        with tarfile.open(fileobj=compressed, mode="w") as bundle:
            for name, data in sorted(files.items()):
                entry = tarfile.TarInfo(name)
                entry.size = len(data)
                entry.mode = 0o644
                bundle.addfile(entry, io.BytesIO(data))
    return buffer.getvalue()


def export_run(run_dir, output, environment, channel="exploratory", run_url=None):
    require(not output.exists(), "output already exists; use a new bundle directory")
    raw = {p.name: p.read_bytes() for p in sorted(run_dir.iterdir()) if p.is_file() and p.suffix in {".json", ".md", ".log"}}
    inputs, metadata, comparison, resolved = validate_run(run_dir, environment, channel, run_url, raw)
    provenance = {
        "run_id": run_dir.name, "channel": channel,
        "performance_baseline": channel == "official",
        "measurement_baseline": metadata["performance_baseline"],
        "sources": metadata["sources"], "harness_sha": metadata["harness_sha"],
        "harness_dirty": metadata["harness_dirty"], "run_url": run_url,
        "environment": environment,
        "environment_provenance": "measurement-host" if metadata.get("environment") == environment else "operator-supplied",
        "started_at_utc": metadata["started_at_utc"], "completed_at_utc": metadata["completed_at_utc"],
    }
    summary = {"schema_version": 1, **provenance, "native_sdk": metadata["native_sdk"], "suites": []}
    files = {}
    groups = []
    for (suite, contract, _), stats in zip(resolved, comparison["suites"]):
        definition = series_definition(suite, contract, inputs, metadata, environment)
        series_id = f"{channel}/{suite['id']}/{identity(definition)}"
        rows = []
        for case in stats["scenarios"]:
            for impl in sorted(suite["implementations"]):
                rows.append({"name": f"{case['scenario_id']}/impl={impl}", "unit": suite["metric"]["unit"],
                             "direction": suite["metric"]["direction"], "value": case[impl]["median"], "stddev": case[impl]["stddev"]})
        tool, metrics = action_metrics(rows, {**provenance, "series_id": series_id, "contract_id": contract["id"], "contract_sha256": identity(contract)})
        metric_path = f"metrics/{suite['id']}.json"
        files[metric_path] = json_bytes(metrics)
        groups.append({"name": series_id, "tool": tool, "output_file": metric_path, "history_file": f"history/{series_id}/{metadata['started_at_utc'][:4]}.json"})
        summary["suites"].append({**stats, "series_id": series_id, "series_definition": definition})
    files["summary.json"] = json_bytes(summary)
    files["raw.tar.gz"] = archive(raw)
    require(len(files["raw.tar.gz"]) <= 25 * 1024 * 1024, "compressed raw bundle exceeds 25 MiB; split storage before publishing")
    manifest = {
        "schema_version": 1, **provenance, "action": ACTION, "groups": groups,
        "run_inputs_sha256": metadata["run_inputs_sha256"],
        "input_files_sha256": metadata["input_files_sha256"],
        "raw_files_sha256": {name: digest(data) for name, data in raw.items()},
        "files_sha256": {name: digest(data) for name, data in files.items()},
    }
    files["manifest.json"] = json_bytes(manifest)
    files["index-entry.json"] = json_bytes({
        "schema_version": 1, "run_id": run_dir.name, "channel": channel,
        "eligible_for_latest": channel == "official", "sources": metadata["sources"],
        "series_ids": [group["name"] for group in groups],
        "manifest": {"path": f"runs/{run_dir.name}/manifest.json", "sha256": digest(files["manifest.json"])},
        "summary": {"path": f"runs/{run_dir.name}/summary.json", "sha256": digest(files["summary.json"])},
        "run_url": run_url,
    })
    output.mkdir(parents=True)
    for name, data in files.items():
        path = output / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--environment", required=True, type=Path)
    parser.add_argument("--channel", choices=("exploratory", "official"), default="exploratory")
    parser.add_argument("--run-url")
    args = parser.parse_args()
    manifest = export_run(args.run, args.output, json.loads(args.environment.read_text()), args.channel, args.run_url)
    print(f"Exported {len(manifest['groups'])} series to {args.output}; channel: {args.channel}")


if __name__ == "__main__":
    main()
