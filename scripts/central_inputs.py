#!/usr/bin/env python3
"""Resolve fixed source inputs and capture the central measurement environment."""

import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import platform
import re

from contract_support import require
from export_results import json_bytes

REPOSITORIES = {"rust": "https://github.com/qortoo/qortoo-rs.git", "go": "https://github.com/qortoo/qortoo-go.git"}


def resolve(lock, rust_sha="", go_sha=""):
    result = deepcopy(lock)
    require(result["schema_version"] == 1 and set(result["sources"]) == set(REPOSITORIES), "unsupported source registry")
    for name, selected in (("rust", rust_sha), ("go", go_sha)):
        source = result["sources"][name]
        require(source["repository"] == REPOSITORIES[name], "source repository is not allowed")
        source["sha"] = selected or source["sha"]
        require(isinstance(source["sha"], str) and re.fullmatch(r"[0-9a-f]{40}", source["sha"]), "source input must be a full lowercase commit SHA")
    require(result["profile"] == "release" and result["rust_features"] == ["observability"], "unsupported build configuration")
    return result


def cpu_identity(text, fallback=""):
    fields = [line.split(":", 1) for line in text.splitlines() if ":" in line]
    values = {key.strip(): value.strip() for key, value in fields}
    for key in ("model name", "Hardware", "Processor"):
        if values.get(key):
            return values[key]
    if values.get("CPU implementer") and values.get("CPU part"):
        return "; ".join(f"{key}={values[key]}" for key in ("CPU implementer", "CPU architecture", "CPU variant", "CPU part", "CPU revision") if key in values)
    require(bool(fallback), "CPU identity unavailable")
    return fallback


def environment():
    cpuinfo = Path("/proc/cpuinfo")
    cpu = cpu_identity(cpuinfo.read_text() if cpuinfo.exists() else "", platform.processor())
    image = os.environ.get("ImageOS", "unknown-image")
    return {"id": f"github-hosted-{image}", "os": platform.platform(), "arch": platform.machine(),
            "cpu": cpu, "runner": f"GitHub hosted {image}; {os.cpu_count()} logical CPUs"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock", type=Path, default=Path("sources.lock.json"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--environment-output", type=Path)
    parser.add_argument("--rust-sha", default=os.environ.get("SELECTED_RUST_SHA", ""))
    parser.add_argument("--go-sha", default=os.environ.get("SELECTED_GO_SHA", ""))
    args = parser.parse_args()
    selected = resolve(json.loads(args.lock.read_text()), args.rust_sha, args.go_sha)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(json_bytes(selected))
    if args.environment_output:
        args.environment_output.parent.mkdir(parents=True, exist_ok=True)
        args.environment_output.write_bytes(json_bytes(environment()))
    print(json.dumps({name: source["sha"] for name, source in selected["sources"].items()}))


if __name__ == "__main__":
    main()
