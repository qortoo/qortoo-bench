#!/usr/bin/env python3
"""Validate central artifacts and atomically append them to the data branch."""

import argparse
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile

from central_inputs import REPOSITORIES, resolve
from contract_support import require
from export_results import ACTION, digest, export_run, json_bytes
from suite_registry import VALIDATORS
from bench_requests import load as load_request, finish as finish_request
from data_store import command, git_credentials

LIMIT = 25 * 1024 * 1024
ROOT = Path(__file__).resolve().parents[1]


def read_json(path):
    require(path.stat().st_size <= LIMIT, f"JSON exceeds storage budget: {path.name}")
    return json.loads(path.read_bytes())


def tracked_hashes(root):
    names = command(["git", "ls-files", "-z"], root).split("\0")
    return {name: digest((root / name).read_bytes()) for name in names if name}


def verify_bundle(bundle, expected):
    """Rebuild the export from bounded raw evidence using trusted harness code."""
    require(all(not p.is_symlink() for p in bundle.rglob("*")), "bundle contains symlink")
    manifest = read_json(bundle / "manifest.json")
    require(manifest["action"] == ACTION and manifest["channel"] == "official", "unexpected publication integration/channel")
    for key in ("harness_sha", "run_id", "run_url", "sources"):
        require(manifest[key] == expected[key], f"publication context mismatch: {key}")
    require(manifest.get("request") == expected.get("request"), "publication request mismatch")
    require(manifest["harness_dirty"] is False and manifest["performance_baseline"] is True, "not an official baseline")
    for name, sha in manifest["files_sha256"].items():
        require(name in {"summary.json", "raw.tar.gz"} | {f"metrics/{suite}.json" for suite in VALIDATORS}, "unexpected bundle file")
        path = bundle / name
        require(path.stat().st_size <= LIMIT and digest(path.read_bytes()) == sha, f"bundle hash mismatch: {name}")
    with tempfile.TemporaryDirectory(prefix="verify-bench-") as td:
        raw = Path(td) / manifest["run_id"]
        raw.mkdir()
        with tarfile.open(bundle / "raw.tar.gz", "r:gz") as archive:
            total = 0
            seen = set()
            for member in archive:
                require(member.isfile() and Path(member.name).name == member.name and member.name not in {".", ".."}, "unsafe raw archive member")
                require(member.name not in seen and member.size >= 0, "duplicate/invalid raw archive member")
                total += member.size
                require(total <= 250 * 1024 * 1024, "raw archive exceeds decoded size budget")
                seen.add(member.name)
                data = archive.extractfile(member).read()
                require(manifest["raw_files_sha256"].get(member.name) == digest(data), "raw member hash mismatch")
                (raw / member.name).write_bytes(data)
            require(seen == set(manifest["raw_files_sha256"]), "missing raw evidence")
        metadata = read_json(raw / "metadata.json")
        require(metadata["harness_files_sha256"] == expected["harness_files_sha256"], "measured harness files differ from trusted commit")
        require(metadata.get("environment") == manifest["environment"], "environment was not captured during measurement")
        rebuilt = Path(td) / "rebuilt"
        export_run(raw, rebuilt, manifest["environment"], "official", expected["run_url"])
        actual_names = {p.relative_to(bundle).as_posix() for p in bundle.rglob("*") if p.is_file()}
        expected_names = {p.relative_to(rebuilt).as_posix() for p in rebuilt.rglob("*") if p.is_file()}
        require(actual_names == expected_names, "unexpected or missing export files")
        for name in expected_names:
            require((bundle / name).read_bytes() == (rebuilt / name).read_bytes(), f"export reconstruction mismatch: {name}")
    return manifest


def latest_eligible(candidate, previous, ancestor):
    if previous is None:
        return True
    if candidate["sources"] == previous["sources"]:
        return candidate["request_order"] > previous["request_order"]
    if set(candidate["sources"]) != set(previous["sources"]):
        return False
    for name, source in candidate["sources"].items():
        old = previous["sources"][name]
        if source["repository"] != old["repository"] or not ancestor(source["repository"], old["sha"], source["sha"]):
            return False
    return True


def validate_history(path, group):
    if not path.exists():
        return
    data = read_json(path)
    require(isinstance(data.get("entries"), dict) and set(data["entries"]) == {group["name"]}, "invalid history series")
    for point in data["entries"][group["name"]]:
        require(point["tool"] == group["tool"] and re.fullmatch(r"[0-9a-f]{40}", point["commit"]["id"]), "invalid history point")
        require(bool(point["benches"]), "empty history point")
        for metric in point["benches"]:
            require(isinstance(metric["name"], str) and bool(metric["unit"]), "invalid history metric")
            require(type(metric["value"]) in (float, int) and math.isfinite(metric["value"]) and metric["value"] > 0, "invalid history value")
            require(json.loads(metric["extra"])["series_id"] == group["name"], "history provenance mismatch")


def catalog(root):
    path = root / "index.json"
    if not path.exists():
        require(not any(p.name not in {".git", "requests"} for p in root.iterdir()), "data branch has no index")
        return {"schema_version": 1, "shards": {}, "latest": {}}, {}
    index = read_json(path)
    require(index["schema_version"] == 1 and isinstance(index["latest"], dict), "unsupported index")
    shards = {}
    for month, sha in index["shards"].items():
        require(re.fullmatch(r"[0-9]{4}-[0-9]{2}", month), "invalid index shard name")
        file = root / "index" / f"{month}.json"
        require(digest(file.read_bytes()) == sha, "index shard hash mismatch")
        shards[month] = read_json(file)
        require(shards[month]["schema_version"] == 1 and isinstance(shards[month]["runs"], dict), "invalid index shard")
    return index, shards


def append_bundle(root, bundle, manifest, request_order, run_action, ancestor):
    request = manifest.get("request")
    if request:
        record = load_request(root, request["request_id"])
        require(record["lock"]["sources"] == manifest["sources"] and record["event"] == request["event"], "request selection mismatch")
        if record["status"] == "published":
            return False
        require(record["active_attempt"] == manifest["run_id"] and record["status"] == "running", "request attempt is no longer active")
    index, shards = catalog(root)
    for group in manifest["groups"]:
        require(re.fullmatch(r"history/official/[a-z][a-z0-9_-]*/[0-9a-f]{64}/[0-9]{4}\.json", group["history_file"]), "invalid history path")
        validate_history(root / group["history_file"], group)
    entry = read_json(bundle / "index-entry.json")
    require(entry["eligible_for_latest"] is True and entry["channel"] == "official", "invalid official index entry")
    for shard in shards.values():
        previous = shard["runs"].get(manifest["run_id"])
        if previous:
            require(all(previous[k] == entry[k] for k in entry), "run ID already exists with different content")
            stored = root / "runs" / manifest["run_id"]
            for file in bundle.rglob("*"):
                if file.is_file():
                    require((stored / file.relative_to(bundle)).read_bytes() == file.read_bytes(), "immutable stored run changed")
            return False
    entry["request_order"] = request_order
    target = root / "runs" / manifest["run_id"]
    require(not target.exists(), "unindexed run directory already exists")
    shutil.copytree(bundle, target)
    for group in manifest["groups"]:
        history = root / group["history_file"]
        history.parent.mkdir(parents=True, exist_ok=True)
        run_action(group, target / group["output_file"], history)
        validate_history(history, group)
    month = manifest["completed_at_utc"][:7]
    require(re.fullmatch(r"[0-9]{4}-[0-9]{2}", month), "invalid completion month")
    shard = shards.get(month, {"schema_version": 1, "runs": {}})
    shard["runs"][manifest["run_id"]] = entry
    file = root / "index" / f"{month}.json"
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_bytes(json_bytes(shard))
    require(file.stat().st_size <= LIMIT, "index shard exceeds storage budget")
    index["shards"][month] = digest(file.read_bytes())
    for series in entry["series_ids"]:
        if latest_eligible(entry, index["latest"].get(series), ancestor):
            index["latest"][series] = entry
    (root / "index.json").write_bytes(json_bytes(index))
    require((root / "index.json").stat().st_size <= LIMIT, "root index exceeds storage budget")
    if request:
        finish_request(root, request["request_id"], manifest["run_id"], "published")
    return True


def publish(bundle, manifest, remote, request_order, run_action, ancestor, git_env=None):
    for attempt in range(3):
        with tempfile.TemporaryDirectory(prefix="bench-data-") as td:
            root = Path(td)
            command(["git", "init", "-q"], root)
            command(["git", "remote", "add", "origin", remote], root)
            exists = command(["git", "ls-remote", "origin", "refs/heads/bench-data"], root, git_env)
            if exists:
                command(["git", "fetch", "-q", "origin", "refs/heads/bench-data"], root, git_env)
                command(["git", "checkout", "-q", "-B", "bench-data", "FETCH_HEAD"], root)
            else:
                command(["git", "checkout", "-q", "--orphan", "bench-data"], root)
            changed = append_bundle(root, bundle, manifest, request_order, run_action, ancestor)
            if not changed:
                return {"data_sha": command(["git", "rev-parse", "HEAD"], root), "already_published": True}
            command(["git", "add", "."], root)
            command(["git", "-c", "user.name=github-actions[bot]", "-c", "user.email=41898282+github-actions[bot]@users.noreply.github.com",
                     "-c", "commit.gpgsign=false", "commit", "-qm", f"📜docs(results): publish benchmark run {manifest['run_id']}"], root)
            result = subprocess.run(["git", "push", "origin", "HEAD:refs/heads/bench-data"], cwd=root, env=git_env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if result.returncode == 0:
                return {"data_sha": command(["git", "rev-parse", "HEAD"], root), "already_published": False}
            if attempt == 2:
                raise RuntimeError(f"data publication failed after three fresh transactions: {result.stderr[-1000:]}")
    raise RuntimeError("unreachable publication state")


def action_runner(action_dir, harness_sha):
    action_dir = action_dir.resolve()
    require(command(["git", "-C", action_dir, "rev-parse", "HEAD"]) == ACTION["revision"], "Action revision mismatch")

    def run(group, metrics, history):
        settings = {"name": group["name"], "tool": group["tool"], "output-file-path": str(metrics),
                    "external-data-json-path": str(history), "github-token": os.environ["GH_TOKEN"], "ref": harness_sha,
                    "gh-pages-branch": "unused", "benchmark-data-dir-path": "unused", "auto-push": "false", "save-data-file": "true",
                    "skip-fetch-gh-pages": "false", "comment-always": "false", "summary-always": "false", "comment-on-alert": "false",
                    "fail-on-alert": "false", "alert-threshold": "200%", "go-force-package-suffix": "false"}
        env = {key: value for key, value in os.environ.items() if not key.startswith("INPUT_")}
        env.update({"INPUT_" + key.upper(): value for key, value in settings.items()})
        command(["node", action_dir / "dist/src/index.js"], cwd=history.parent, env=env)
        require(history.exists(), "Action did not store history")
        data = read_json(history)
        point = data["entries"][group["name"]][-1]
        require(point["commit"]["id"] == harness_sha, "Action attributed result to a different harness")
        require(point["benches"] == read_json(metrics), "Action modified exported metrics")
        print(f"Stored {len(point['benches'])} metrics: {group['name']}")
    return run


def ancestor(repository, old, new):
    require(repository in REPOSITORIES.values(), "unapproved source repository")
    if old == new:
        return True
    require(re.fullmatch(r"[0-9a-f]{40}", old) and re.fullmatch(r"[0-9a-f]{40}", new), "invalid source revision")
    slug = repository.removeprefix("https://github.com/").removesuffix(".git")
    status = command(["gh", "api", f"repos/{slug}/compare/{old}...{new}", "--jq", ".status"])
    return status in {"ahead", "identical"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--lock", type=Path, required=True)
    parser.add_argument("--action-directory", type=Path, required=True)
    args = parser.parse_args()
    require(os.environ.get("GITHUB_REPOSITORY") == "qortoo/qortoo-bench" and os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
            and os.environ.get("GITHUB_REF") == "refs/heads/main", "publication requires the central main dispatch workflow")
    run, attempt = os.environ["GITHUB_RUN_ID"], os.environ["GITHUB_RUN_ATTEMPT"]
    require(run.isdigit() and attempt.isdigit(), "invalid Actions attempt identity")
    expected = {"harness_sha": os.environ["GITHUB_SHA"], "run_id": f"{run}-{attempt}",
                "run_url": f"https://github.com/qortoo/qortoo-bench/actions/runs/{run}/attempts/{attempt}",
                "sources": resolve(read_json(args.lock))["sources"], "harness_files_sha256": tracked_hashes(ROOT)}
    selected = read_json(args.lock)
    if "request" in selected:
        expected["request"] = selected["request"]
    manifest = verify_bundle(args.bundle, expected)
    git_env = git_credentials()
    result = publish(args.bundle, manifest, "https://github.com/qortoo/qortoo-bench.git", [int(run), int(attempt)],
                     action_runner(args.action_directory, expected["harness_sha"]), ancestor, git_env)
    print(json.dumps(result))
    output = ROOT / "._runtime/publication.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(json_bytes({**result, "run_id": expected["run_id"]}))


if __name__ == "__main__":
    main()
