#!/usr/bin/env python3
"""Authenticate a completed SDK PR event and freeze a candidate comparison."""

import argparse
import base64
from copy import deepcopy
import json
import os
from pathlib import Path
import re
import subprocess

from candidate_contract import REPOSITORIES, validate_lock
from central_inputs import resolve
from contract_support import require
from export_results import json_bytes

ROOT = Path(__file__).resolve().parents[1]
SLUGS = {name: url.removeprefix("https://github.com/").removesuffix(".git") for name, url in REPOSITORIES.items()}


def api(endpoint):
    return json.loads(subprocess.check_output(["gh", "api", endpoint]))


def go_pin(repository, sha, get):
    ci = get(f"repos/{repository}/contents/.github/workflows/ci.yml?ref={sha}")
    text = base64.b64decode(ci["content"], validate=False).decode()
    pins = re.findall(r"^  QORTOO_RS_REF: ([0-9a-f]{40})$", text, re.M)
    require(len(pins) == 1, "Go revision must declare one full Rust CI pin")
    return pins[0]


def select(lock, component, number, head_sha, source_run, get=api):
    require(component in SLUGS and re.fullmatch(r"[1-9][0-9]*", number), "invalid SDK/PR")
    require(re.fullmatch(r"[0-9a-f]{40}", head_sha) and source_run.isdigit(), "invalid source event identity")
    slug = SLUGS[component]
    origin = get(f"repos/{slug}/actions/runs/{source_run}")
    require(origin["repository"]["full_name"] == slug and origin["event"] == "pull_request"
            and origin["path"].split("@", 1)[0] == ".github/workflows/benchmark-pr.yml"
            and origin["head_sha"] == head_sha and origin["conclusion"] == "success",
            "source run is not the claimed successful SDK PR event")
    pr = get(f"repos/{slug}/pulls/{number}")
    require(pr["base"]["repo"]["full_name"] == slug and pr["base"]["ref"] == "main", "PR must target the SDK main branch")
    if pr["state"] != "open" or pr["head"]["sha"] != head_sha:
        return {"schema_version": 1, "status": "superseded", "reason": "PR closed or head changed", "pr_url": pr["html_url"]}
    head_repo = pr["head"]["repo"]
    require(head_repo is not None and not head_repo["private"], "candidate source must be public")
    head_slug = head_repo["full_name"]
    require(re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", head_slug), "invalid head repository")
    if head_slug != slug:
        details = get(f"repos/{head_slug}")
        require(details["fork"] and details.get("source", {}).get("full_name") == slug, "PR head is outside the SDK fork network")
    base = {"repository": REPOSITORIES[component], "sha": pr["base"]["sha"]}
    head = {"repository": f"https://github.com/{head_slug}.git", "sha": head_sha}
    resolved = resolve(lock)
    if component == "rust":
        dependency = {"repository": REPOSITORIES["go"], "sha": get(f"repos/{SLUGS['go']}/commits/main")["sha"]}
        base_pin = head_pin = go_pin(SLUGS["go"], dependency["sha"], get)
    else:
        base_pin = go_pin(slug, base["sha"], get)
        head_pin = go_pin(head_slug, head["sha"], get)
        dependency = {"repository": REPOSITORIES["rust"], "sha": base_pin}
    context = {"schema_version": 1, "channel": "candidate", "component": component, "pr_number": int(number),
               "pr_url": pr["html_url"], "base": base, "head": head, "dependency": dependency,
               "source_run_url": f"https://github.com/{slug}/actions/runs/{source_run}",
               "fork": head_slug != slug, "go_pin_changed": base_pin != head_pin}
    locks = {}
    for leg, selected, pin in (("base", base, base_pin), ("head", head, head_pin)):
        item = deepcopy(resolved)
        item["sources"][component] = selected
        item["sources"]["go" if component == "rust" else "rust"] = dependency
        item["candidate"] = {**context, "leg": leg, "go_ci_rust_sha": pin}
        validate_lock(item)
        locks[leg] = item
    return {"schema_version": 1, "status": "selected", "context": context, "locks": locks}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    require(os.environ.get("GITHUB_REPOSITORY") == "qortoo/qortoo-bench" and os.environ.get("GITHUB_REF") == "refs/heads/main", "candidate receiver requires central main")
    result = select(json.loads((ROOT / "sources.lock.json").read_text()), os.environ["SOURCE_COMPONENT"],
                    os.environ["SOURCE_PR"], os.environ["SOURCE_SHA"], os.environ["SOURCE_RUN_ID"])
    result["harness"] = {"sha": os.environ["GITHUB_SHA"], "dirty": False}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(json_bytes(result))
    with open(os.environ["GITHUB_OUTPUT"], "a") as output:
        output.write(f"should_run={str(result['status'] == 'selected').lower()}\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
