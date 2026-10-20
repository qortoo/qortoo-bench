#!/usr/bin/env python3
"""Resolve, claim, and retain SDK main requests independently of measurement jobs."""

import argparse
import json
import os
from pathlib import Path
import re

from central_inputs import REPOSITORIES, resolve
from contract_support import require
from data_store import REMOTE, command, git_credentials, transaction
from export_results import json_bytes

ROOT = Path(__file__).resolve().parents[1]
SLUGS = {name: url.removeprefix("https://github.com/").removesuffix(".git") for name, url in REPOSITORIES.items()}
KEY = r"(?:rust|go)-[0-9a-f]{40}|manual-[0-9]+"


def api(endpoint):
    return json.loads(command(["gh", "api", endpoint]))


def pin(text):
    pins = re.findall(r"^  QORTOO_RS_REF: ([0-9a-f]{40})$", text, re.M)
    require(len(pins) == 1, "Go commit must declare one full Rust CI pin")
    return pins[0]


def selection(lock, component, sha, source_run, run_id, get=api):
    """The changed component never drifts to main; resolve its dependency once."""
    if not component:
        require(not sha and not source_run, "partial source event")
        return f"manual-{run_id}", {"kind": "manual"}, resolve(lock)
    require(component in SLUGS and re.fullmatch(r"[0-9a-f]{40}", sha), "invalid SDK event")
    require(source_run.isdigit(), "source run ID is required")
    origin = get(f"repos/{SLUGS[component]}/actions/runs/{source_run}")
    require(origin["event"] == "push" and origin["head_branch"] == "main" and origin["head_sha"] == sha
            and origin["path"] == ".github/workflows/benchmark.yml"
            and origin["repository"]["full_name"] == SLUGS[component], "source run is not the claimed SDK main event")
    go_sha = sha if component == "go" else get(f"repos/{SLUGS['go']}/commits/main")["sha"]
    import base64
    ci = get(f"repos/{SLUGS['go']}/contents/.github/workflows/ci.yml?ref={go_sha}")
    go_pin = pin(base64.b64decode(ci["content"]).decode())
    selected = resolve(lock, sha if component == "rust" else go_pin, go_sha)
    event = {"kind": "sdk-main", "component": component, "sha": sha,
             "source_run_url": f"https://github.com/{SLUGS[component]}/actions/runs/{source_run}",
             "go_ci_rust_sha": go_pin}
    return f"{component}-{sha}", event, selected


def request_path(root, key):
    require(re.fullmatch(KEY, key), "invalid request ID")
    return root / "requests" / f"{key}.json"


def load(root, key):
    data = json.loads(request_path(root, key).read_text())
    require(data["schema_version"] == 1 and data["request_id"] == key, "invalid request record")
    resolve(data["lock"])
    return data


def store(root, data):
    path = request_path(root, data["request_id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(json_bytes(data))


def claim(root, key, event, lock, attempt, active_completed):
    path = request_path(root, key)
    data = load(root, key) if path.exists() else {
        "schema_version": 1, "request_id": key, "event": event, "lock": lock,
        "status": "received", "active_attempt": None, "attempts": {}}
    require(data["event"]["kind"] == event["kind"], "request event kind changed")
    if data["status"] == "published":
        return data, False
    active = data["active_attempt"]
    if active and data["attempts"][active]["status"] == "running":
        if active == attempt or not active_completed(active):
            return data, False
        data["attempts"][active]["status"] = "abandoned"
    data["status"] = "running"
    data["active_attempt"] = attempt
    data["attempts"][attempt] = {"status": "running", "run_url": f"https://github.com/qortoo/qortoo-bench/actions/runs/{attempt.replace('-', '/attempts/')}"}
    store(root, data)
    return data, True


def finish(root, key, attempt, status, detail=""):
    require(status in {"failed", "incompatible", "cancelled", "published"}, "invalid request outcome")
    data = load(root, key)
    require(attempt in data["attempts"], "unregistered request attempt")
    # A delayed finalizer must not overwrite a newer attempt or successful publication.
    if data["active_attempt"] != attempt or data["status"] == "published":
        return False
    data["status"] = status
    data["attempts"][attempt].update(status=status, detail=detail[:2000])
    if status == "published":
        data["result_run_id"] = attempt
    store(root, data)
    return True


def completed(attempt):
    run_id, number = attempt.split("-")
    run = api(f"repos/qortoo/qortoo-bench/actions/runs/{run_id}/attempts/{number}")
    return run["status"] == "completed"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["register", "finish"])
    parser.add_argument("--request-id", default=os.environ.get("REQUEST_ID", ""))
    parser.add_argument("--status", choices=["failed", "cancelled"])
    args = parser.parse_args()
    require(os.environ.get("GITHUB_REPOSITORY") == "qortoo/qortoo-bench" and os.environ.get("GITHUB_REF") == "refs/heads/main"
            and os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch", "requests require the central main workflow")
    attempt = f"{os.environ['GITHUB_RUN_ID']}-{os.environ['GITHUB_RUN_ATTEMPT']}"
    require(re.fullmatch(r"[0-9]+-[0-9]+", attempt), "invalid attempt")
    runtime = ROOT / "._runtime"
    runtime.mkdir(exist_ok=True)
    env = git_credentials()
    if args.operation == "register":
        if args.request_id:
            require(not any(os.environ.get(k) for k in ("SOURCE_COMPONENT", "SOURCE_SHA", "SOURCE_RUN_ID", "SELECTED_RUST_SHA", "SELECTED_GO_SHA")), "retry inputs must not change source selection")
        def register(root):
            if args.request_id:
                data = load(root, args.request_id)
                return claim(root, args.request_id, data["event"], data["lock"], attempt, completed)
            # Avoid re-resolving dependencies for duplicate events or full-job retries.
            component, sha, source_run = (os.environ.get(k, "") for k in ("SOURCE_COMPONENT", "SOURCE_SHA", "SOURCE_RUN_ID"))
            key = f"{component}-{sha}" if component else f"manual-{os.environ['GITHUB_RUN_ID']}"
            if re.fullmatch(KEY, key) and request_path(root, key).exists():
                data = load(root, key)
                return claim(root, key, data["event"], data["lock"], attempt, completed)
            require(not component or not (os.environ.get("SELECTED_RUST_SHA") or os.environ.get("SELECTED_GO_SHA")), "SDK events cannot override dependency policy")
            lock = resolve(json.loads((ROOT / "sources.lock.json").read_text()), os.environ.get("SELECTED_RUST_SHA", ""), os.environ.get("SELECTED_GO_SHA", ""))
            key, event, selected = selection(lock, component, sha, source_run, os.environ['GITHUB_RUN_ID'])
            selected["request"] = {"request_id": key, "event": event}
            return claim(root, key, event, selected, attempt, completed)
        data, should_run = transaction(REMOTE, register, "📜docs(requests): register benchmark request", env)
        (runtime / "request.json").write_bytes(json_bytes(data))
        (runtime / "sources.lock.json").write_bytes(json_bytes(data["lock"]))
        with open(os.environ["GITHUB_OUTPUT"], "a") as output:
            output.write(f"request_id={data['request_id']}\nshould_run={str(should_run).lower()}\n")
        print(json.dumps({"request_id": data["request_id"], "should_run": should_run, "sources": data["lock"]["sources"]}))
    else:
        require(bool(args.request_id), "request ID required")
        status = args.status or "failed"
        detail = os.environ.get("FAILURE_DETAIL", "measurement or publication failed; see attempt logs")
        metadata = ROOT / "results" / attempt / "metadata.json"
        if not metadata.exists():
            metadata = ROOT / "._runtime/diagnostics/results" / attempt / "metadata.json"
        if metadata.exists():
            result = json.loads(metadata.read_text())
            if result.get("failure_phase") == "sdk-go-compatibility":
                status = "incompatible"
            detail = result.get("error", detail)
        transaction(REMOTE, lambda root: finish(root, args.request_id, attempt, status, detail), "📜docs(requests): record benchmark outcome", env)


if __name__ == "__main__":
    main()
