"""Exercise the credential-bearing refresh script without network access."""
import json
from pathlib import Path
import subprocess
import unittest


class DocumentationRefreshTests(unittest.TestCase):
    def invoke(self, changes=None, manual=False, snapshot="new", manifest=None):
        workflow = Path(__file__).resolve().parents[1] / ".github/workflows/documentation-refresh.yml"
        script = workflow.read_text().split("          script: |\n", 1)[1]
        script = "\n".join(line[12:] for line in script.splitlines())
        run = {"repository": {"full_name": "qortoo/qortoo-bench"},
               "head_repository": {"full_name": "qortoo/qortoo-bench"},
               "path": ".github/workflows/benchmark.yml", "head_branch": "main",
               "event": "workflow_dispatch", "conclusion": "success"}
        run.update(changes or {})
        context = {"eventName": "workflow_dispatch" if manual else "workflow_run", "runId": 123, "payload": {"workflow_run": run}}
        if manifest is None:
            manifest = {"mode": "committed", "site": {"dirty": False}, "benchmarks": {
                "repository": "qortoo/qortoo-bench", "revision": ("a" if snapshot == "same" else "b") * 40}}
        harness = """
const context = JSON.parse(process.argv[1]);
const snapshot = process.argv[2], manifest = JSON.parse(process.argv[3]);
const calls = [], errors = [], reads = [];
const core = { info() {}, setFailed: message => errors.push(message), summary: { addRaw() { return this; }, async write() {} } };
const github = { rest: {
  repos: { async getCommit(input) {
    reads.push(input);
    if (snapshot === 'branch-error') throw Error('unavailable');
    return {data: {sha: 'a'.repeat(40)}};
  } },
  actions: { async createWorkflowDispatch(input) { calls.push(input); return {status: 204}; } }
} };
const fetch = async (url, options) => {
  reads.push({url, cache: options.cache});
  if (snapshot === 'fetch-error') throw Error('unavailable');
  return {ok: snapshot !== 'http-error', status: snapshot === 'http-error' ? 404 : 200,
    async json() { if (snapshot === 'json-error') throw Error('invalid JSON'); return manifest; }};
};
""" + "await (async () => {\n" + script + "\n})(); console.log(JSON.stringify({calls, errors, reads}));"
        result = subprocess.run(["node", "--input-type=module", "-e", harness, json.dumps(context), snapshot, json.dumps(manifest)],
                                check=True, text=True, capture_output=True)
        return json.loads(result.stdout)

    def test_success_failure_and_manual_retry_dispatch_only_website(self):
        for result in [self.invoke(), self.invoke({"conclusion": "failure"}), self.invoke(manual=True)]:
            self.assertEqual(result["errors"], [])
            self.assertEqual(result["calls"], [{"owner": "qortoo", "repo": "qortoo", "workflow_id": "docs.yml", "ref": "main", "inputs": {"candidate_sdk": "none"}}])

    def test_wrong_repository_workflow_branch_or_event_cannot_dispatch(self):
        for changes in [{"head_repository": {"full_name": "fork/bench"}}, {"repository": {"full_name": "other/repo"}},
                        {"path": ".github/workflows/pr-benchmark.yml"}, {"head_branch": "feature"}, {"event": "pull_request"}]:
            result = self.invoke(changes)
            self.assertEqual(result["calls"], [])
            self.assertEqual(len(result["errors"]), 1)
            self.assertEqual(result["reads"], [])

    def test_already_public_snapshot_skips_success_and_failure_refresh(self):
        for conclusion in ["success", "failure", "cancelled"]:
            result = self.invoke({"conclusion": conclusion}, snapshot="same")
            self.assertEqual(result["calls"], [])
            self.assertEqual(result["errors"], [])
            self.assertEqual(result["reads"], [
                {"owner": "qortoo", "repo": "qortoo-bench", "ref": "bench-data"},
                {"url": "https://qortoo.github.io/qortoo/generated/sources.json?run=123", "cache": "no-store"}])

    def test_missing_or_unreadable_snapshot_keeps_refresh(self):
        for snapshot in ["branch-error", "fetch-error", "http-error", "json-error"]:
            with self.subTest(snapshot=snapshot):
                result = self.invoke(snapshot=snapshot)
                self.assertEqual(len(result["calls"]), 1)
                self.assertEqual(result["errors"], [])
        for manifest in [{}, {"benchmarks": {"revision": "a" * 40}},
                         {"mode": "worktree", "site": {"dirty": False},
                          "benchmarks": {"repository": "qortoo/qortoo-bench", "revision": "a" * 40}},
                         {"mode": "committed", "site": {"dirty": False},
                          "benchmarks": {"repository": "other/repo", "revision": "a" * 40}}]:
            result = self.invoke(manifest=manifest)
            self.assertEqual(len(result["calls"]), 1)

    def test_manual_refresh_bypasses_snapshot_lookup(self):
        result = self.invoke(manual=True, snapshot="same")
        self.assertEqual(len(result["calls"]), 1)
        self.assertEqual(result["reads"], [])
