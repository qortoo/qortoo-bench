#!/usr/bin/env python3
"""Run the pinned Action locally against an exported bundle without publishing."""

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess

from contract_support import require
from export_results import ACTION, action_metrics, digest, json_bytes


def check_action(action_dir, bundle, output, node="node"):
    revision = subprocess.check_output(["git", "-C", str(action_dir), "rev-parse", "HEAD"], text=True).strip()
    require(revision == ACTION["revision"], "Action checkout must match pinned revision")
    require(not output.exists(), "PoC output already exists")
    manifest = json.loads((bundle / "manifest.json").read_text())
    for name, sha in manifest["files_sha256"].items():
        require(digest((bundle / name).read_bytes()) == sha, f"bundle hash mismatch: {name}")
    output.mkdir(parents=True)
    sha = manifest["harness_sha"]
    require(isinstance(sha, str) and len(sha) == 40, "PoC needs recorded harness commit")
    event = {"head_commit": {"id": sha, "message": "Local PoC event fixture", "timestamp": "2026-01-01T00:00:00Z",
                             "url": f"https://github.com/qortoo/qortoo-bench/commit/{sha}",
                             "author": {"name": "Local PoC"}, "committer": {"name": "Local PoC"}},
             "repository": {"html_url": "https://github.com/qortoo/qortoo-bench"}}
    event_path = output / "event.json"
    event_path.write_bytes(json_bytes(event))
    groups = []
    for index, group in enumerate(manifest["groups"]):
        groups.append({**group, "input": (bundle / group["output_file"]).resolve(), "history": output / f"history-{index}.json", "repeat": 2})
    fixture = json.loads((Path(__file__).resolve().parents[1] / "tests/fixtures/server-metrics.json").read_text())
    for kind in ("latency", "throughput"):
        tool, metrics = action_metrics(fixture[kind], {"synthetic": True, "server_executed": False})
        path = output / f"server-{kind}.json"
        path.write_bytes(json_bytes(metrics))
        groups.append({"name": f"synthetic/server/{kind}", "tool": tool, "input": path, "history": output / f"server-{kind}-history.json", "repeat": 1})
    checks = []
    for index, group in enumerate(groups):
        expected = json.loads(group["input"].read_text())
        for attempt in range(group["repeat"]):
            inputs = {"name": group["name"], "tool": group["tool"], "output-file-path": str(group["input"]),
                      "external-data-json-path": str(group["history"].resolve()), "gh-pages-branch": "unused",
                      "benchmark-data-dir-path": "unused", "auto-push": "false", "save-data-file": "true",
                      "skip-fetch-gh-pages": "false", "comment-always": "false", "summary-always": "false",
                      "comment-on-alert": "false", "fail-on-alert": "false", "alert-threshold": "200%",
                      "go-force-package-suffix": "false"}
            env = {"PATH": os.environ["PATH"], "GITHUB_EVENT_PATH": str(event_path.resolve()),
                   "GITHUB_REPOSITORY": "qortoo/qortoo-bench", "GITHUB_SHA": sha, "GITHUB_REF": "refs/heads/main",
                   "GITHUB_WORKSPACE": str(output.resolve())}
            env.update({"INPUT_" + key.upper().replace(" ", "_"): value for key, value in inputs.items()})
            with (output / f"action-{index}-{attempt}.log").open("w") as log:
                subprocess.run([shutil.which(node) or node, str(action_dir / "dist/src/index.js")], env=env, cwd=output,
                               stdout=log, stderr=subprocess.STDOUT, check=True)
        history = json.loads(group["history"].read_text())
        entries = history["entries"][group["name"]]
        require(len(entries) == group["repeat"], "Action lost history entries")
        for entry in entries:
            require(entry["commit"]["id"] == sha, "unexpected default Action commit attribution")
            require(entry["tool"] == group["tool"] and entry["benches"] == expected, "Action changed metrics or provenance")
        checks.append({"name": group["name"], "tool": group["tool"], "history_entries": len(entries), "metrics": len(expected)})
    result = {"scope": "local PoC with event fixture; no remote upload", "action": ACTION,
              "node": subprocess.check_output([node, "--version"], text=True).strip(),
              "default_commit_attribution": "central harness", "checks": checks}
    (output / "verification.json").write_bytes(json_bytes(result))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--action-directory", required=True, type=Path)
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--node", default="node")
    args = parser.parse_args()
    result = check_action(args.action_directory.resolve(), args.bundle.resolve(), args.output.resolve(), args.node)
    print(f"Local Action PoC passed: {len(result['checks'])} groups; no upload")


if __name__ == "__main__":
    main()
