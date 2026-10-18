#!/usr/bin/env python3
"""Validate registered SDK suites against pinned sources and shared contracts."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import uuid

from suite_registry import VALIDATORS, load_suite


ROOT = Path(__file__).resolve().parents[1]


def git(path, *args):
    return subprocess.check_output(
        ["git", "-C", str(path), *args], text=True, stderr=subprocess.PIPE
    ).strip()


def checksum(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_source(path, sha):
    if git(path, "rev-parse", "HEAD") != sha:
        raise RuntimeError(f"source SHA mismatch: {path}")
    if git(path, "status", "--porcelain", "--untracked-files=all"):
        raise RuntimeError(f"source checkout is dirty: {path}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--go", default="go", help="Go executable matching sources.lock.json")
    parser.add_argument("--source-root", type=Path, help="directory containing local qortoo-rs/qortoo-go repositories")
    parser.add_argument("--suite", action="append", choices=sorted(VALIDATORS), help="suite to validate; repeat to select several (default: all)")
    args = parser.parse_args()
    lock = json.loads((ROOT / "sources.lock.json").read_text())
    names = list(dict.fromkeys(args.suite or sorted(VALIDATORS)))
    resolved = [load_suite(ROOT, lock, name) for name in names]
    input_paths = [ROOT / "sources.lock.json", ROOT / "components.json"]
    for suite, contract, contract_path in resolved:
        input_paths.extend([ROOT / f"suites/{suite['id']}.json", contract_path])
    input_hashes = {str(p.relative_to(ROOT)): checksum(p) for p in input_paths}
    env = os.environ.copy()
    for key in ("CARGO_TARGET_DIR", "CARGO_BUILD_TARGET", "RUSTFLAGS", "CGO_CFLAGS", "CGO_LDFLAGS", "GOFLAGS"):
        env.pop(key, None)
    env.update(RUSTUP_TOOLCHAIN=lock["toolchains"]["rust"], GOTOOLCHAIN="local", GOWORK="off", CGO_ENABLED="1")
    go = shutil.which(args.go)
    if go is None:
        raise RuntimeError(f"Go executable not found: {args.go}")
    go_version = subprocess.check_output([go, "version"], text=True, env=env).strip()
    if go_version.split()[2] != "go" + lock["toolchains"]["go"]:
        raise RuntimeError(f"expected Go {lock['toolchains']['go']}; got {go_version}; select it with --go")
    rust_version = subprocess.check_output(["rustc", "--version"], text=True, env=env).strip()
    if rust_version.split()[1] != lock["toolchains"]["rust"]:
        raise RuntimeError(f"unexpected Rust version: {rust_version}")

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
        for name, source in lock["sources"].items():
            path = ROOT / "_sources" / name
            if not path.exists():
                repository_name = source["repository"].rsplit("/", 1)[-1].removesuffix(".git")
                repository = str(args.source_root.resolve() / repository_name) if args.source_root else source["repository"]
                run(f"clone-{name}", ["git", "clone", "--no-checkout", "--no-hardlinks", repository, str(path)])
                run(f"checkout-{name}", ["git", "-C", str(path), "checkout", "--detach", source["sha"]])
            validate_source(path, source["sha"])

        rust = ROOT / "_sources/rust"
        go_source = ROOT / "_sources/go"
        ci = (go_source / ".github/workflows/ci.yml").read_text()
        pins = [line.split(":", 1)[1].strip() for line in ci.splitlines() if line.startswith("  QORTOO_RS_REF:")]
        if pins != [lock["sources"]["rust"]["sha"]]:
            raise RuntimeError("Go CI pin does not match the selected Rust source")
        run("native-sdk", ["make", "native-sdk-stage", "NATIVE_SDK_PROFILE=release"], rust)
        sdk = rust / "target/native-sdk/release"
        sdk_manifest = json.loads((sdk / "manifest.json").read_text())
        if sdk_manifest["profile"] != "release":
            raise RuntimeError("native SDK must use the release profile")
        metadata["native_sdk"] = {
            "manifest": sdk_manifest,
            "header_sha256": checksum(sdk / "include/qortoo.h"),
            "library_sha256": checksum(sdk / "lib/libqortoo_ffi.a"),
        }
        env.update(CGO_CFLAGS=f"-I{sdk}/include", CGO_LDFLAGS=f"-L{sdk}/lib")

        binaries = {}
        rust_adapters = {(suite["implementations"]["rust"]["harness"], suite["implementations"]["rust"]["executable"]) for suite, _, _ in resolved}
        for directory in sorted({directory for directory, _ in rust_adapters}):
            harness = ROOT / directory
            run("rust-build", ["cargo", "build", "--release", "--locked", "--bins"], harness)
        for directory, executable in rust_adapters:
            binaries[("rust", directory, executable)] = ROOT / directory / "target/release" / executable
        go_adapters = {(suite["implementations"]["go"]["harness"], suite["implementations"]["go"]["executable"]) for suite, _, _ in resolved}
        for directory, executable in sorted(go_adapters):
            harness = ROOT / directory
            run(f"go-tests-{executable}", [go, "test", "-a", "-count=1", "-mod=readonly", "-v", "."], harness)
            binary = output / f"{executable}-go"
            # External C libraries do not invalidate Go's build cache.
            run(f"go-build-{executable}", [go, "build", "-a", "-mod=readonly", "-trimpath", "-o", str(binary), "."], harness)
            binaries[("go", directory, executable)] = binary
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
        for path in input_paths:
            if checksum(path) != input_hashes[str(path.relative_to(ROOT))]:
                raise RuntimeError(f"input changed during execution: {path}")
        metadata["status"] = "passed"
        print(f"Suite smoke passed: {len(samples)} validated samples across {names}; performance baseline: false", flush=True)
    except Exception as error:
        metadata.update(status="failed", error=str(error))
        raise
    finally:
        try:
            metadata["harness_sha"] = git(ROOT, "rev-parse", "--verify", "HEAD")
        except subprocess.CalledProcessError:
            metadata["harness_sha"] = None
        metadata["harness_dirty"] = bool(git(ROOT, "status", "--porcelain", "--untracked-files=all"))
        names = git(ROOT, "ls-files", "--cached", "--others", "--exclude-standard", "-z").split("\0")
        files = [ROOT / name for name in set(names) if name and (ROOT / name).is_file()]
        metadata["harness_files_sha256"] = {str(p.relative_to(ROOT)): checksum(p) for p in sorted(files)}
        metadata["run_inputs_sha256"] = checksum(output / "run-inputs.json")
        metadata["validated_samples"] = len(samples)
        metadata["completed_at_utc"] = datetime.now(timezone.utc).isoformat()
        (output / "samples.json").write_text(json.dumps(samples, indent=2) + "\n")
        (output / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
        print(f"Smoke evidence: {output}", flush=True)


if __name__ == "__main__":
    main()
