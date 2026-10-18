"""Shared build, source validation, and execution support for benchmark harnesses."""

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess

from contract_support import require


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


def setup_environment(lock):
    env = os.environ.copy()
    for key in ("CARGO_TARGET_DIR", "CARGO_BUILD_TARGET", "RUSTFLAGS", "CGO_CFLAGS", "CGO_LDFLAGS", "GOFLAGS"):
        env.pop(key, None)
    env.update(
        RUSTUP_TOOLCHAIN=lock["toolchains"]["rust"],
        GOTOOLCHAIN="local",
        GOWORK="off",
        CGO_ENABLED="1",
    )
    return env


def check_toolchains(lock, go_executable, env):
    go = shutil.which(go_executable)
    if go is None:
        raise RuntimeError(f"Go executable not found: {go_executable}")
    go_version = subprocess.check_output([go, "version"], text=True, env=env).strip()
    expected_go = "go" + lock["toolchains"]["go"]
    if go_version.split()[2] != expected_go:
        raise RuntimeError(f"expected Go {lock['toolchains']['go']}; got {go_version}; select it with --go")
    rust_version = subprocess.check_output(["rustc", "--version"], text=True, env=env).strip()
    if rust_version.split()[1] != lock["toolchains"]["rust"]:
        raise RuntimeError(f"unexpected Rust version: {rust_version}")
    return go, go_version, rust_version


def prepare_sources(root, lock, source_root, run_fn):
    for name, source in lock["sources"].items():
        path = root / "_sources" / name
        if not path.exists():
            repository_name = source["repository"].rsplit("/", 1)[-1].removesuffix(".git")
            repository = str(source_root.resolve() / repository_name) if source_root else source["repository"]
            run_fn(f"clone-{name}", ["git", "clone", "--no-checkout", "--no-hardlinks", repository, str(path)])
            run_fn(f"fetch-{name}", ["git", "-C", str(path), "fetch", "--no-tags", "origin", source["sha"]])
            run_fn(f"checkout-{name}", ["git", "-C", str(path), "checkout", "--detach", source["sha"]])
        validate_source(path, source["sha"])

    rust = root / "_sources/rust"
    go_source = root / "_sources/go"
    ci = (go_source / ".github/workflows/ci.yml").read_text()
    pins = [line.split(":", 1)[1].strip() for line in ci.splitlines() if line.startswith("  QORTOO_RS_REF:")]
    if pins != [lock["sources"]["rust"]["sha"]]:
        raise RuntimeError("Go CI pin does not match the selected Rust source")


def stage_native_sdk(root, env, run_fn):
    rust = root / "_sources/rust"
    run_fn("native-sdk", ["make", "native-sdk-stage", "NATIVE_SDK_PROFILE=release"], rust)
    sdk = rust / "target/native-sdk/release"
    sdk_manifest = json.loads((sdk / "manifest.json").read_text())
    if sdk_manifest["profile"] != "release":
        raise RuntimeError("native SDK must use the release profile")
    header_path = sdk / "include/qortoo.h"
    library_path = sdk / "lib/libqortoo_ffi.a"
    env.update(CGO_CFLAGS=f"-I{sdk}/include", CGO_LDFLAGS=f"-L{sdk}/lib")
    return {
        "manifest": sdk_manifest,
        "header_sha256": checksum(header_path),
        "library_sha256": checksum(library_path),
    }


def build_harnesses(root, resolved, output_dir, env, go, run_fn):
    binaries = {}
    rust_adapters = {(suite["implementations"]["rust"]["harness"], suite["implementations"]["rust"]["executable"]) for suite, _, _ in resolved}
    for directory in sorted({directory for directory, _ in rust_adapters}):
        harness = root / directory
        run_fn("rust-build", ["cargo", "build", "--release", "--locked", "--bins"], harness)
    for directory, executable in rust_adapters:
        binaries[("rust", directory, executable)] = root / directory / "target/release" / executable

    go_adapters = {(suite["implementations"]["go"]["harness"], suite["implementations"]["go"]["executable"]) for suite, _, _ in resolved}
    for directory, executable in sorted(go_adapters):
        harness = root / directory
        run_fn(f"go-tests-{executable}", [go, "test", "-a", "-count=1", "-mod=readonly", "-v", "."], harness)
        binary = output_dir / f"{executable}-go"
        # External C libraries do not invalidate Go's build cache.
        run_fn(f"go-build-{executable}", [go, "build", "-a", "-mod=readonly", "-trimpath", "-o", str(binary), "."], harness)
        binaries[("go", directory, executable)] = binary

    return binaries


def finalize_metadata(root, output_dir, metadata, samples_count):
    try:
        metadata["harness_sha"] = git(root, "rev-parse", "--verify", "HEAD")
    except subprocess.CalledProcessError:
        metadata["harness_sha"] = None
    metadata["harness_dirty"] = bool(git(root, "status", "--porcelain", "--untracked-files=all"))
    names = git(root, "ls-files", "--cached", "--others", "--exclude-standard", "-z").split("\0")
    files = [root / name for name in set(names) if name and (root / name).is_file()]
    metadata["harness_files_sha256"] = {str(p.relative_to(root)): checksum(p) for p in sorted(files)}
    metadata["run_inputs_sha256"] = checksum(output_dir / "run-inputs.json")
    metadata["validated_samples"] = samples_count
    metadata["completed_at_utc"] = datetime.now(timezone.utc).isoformat()

