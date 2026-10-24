"""Exercise the credential-bearing refresh script without network access."""
import json
from pathlib import Path
import subprocess
import unittest


class DocumentationRefreshTests(unittest.TestCase):
    def invoke(self, changes=None, manual=False):
        workflow = Path(__file__).resolve().parents[1] / ".github/workflows/documentation-refresh.yml"
        script = workflow.read_text().split("          script: |\n", 1)[1]
        script = "\n".join(line[12:] for line in script.splitlines())
        run = {"repository": {"full_name": "qortoo/qortoo-bench"},
               "head_repository": {"full_name": "qortoo/qortoo-bench"},
               "path": ".github/workflows/benchmark.yml", "head_branch": "main",
               "event": "workflow_dispatch", "conclusion": "success"}
        run.update(changes or {})
        context = {"eventName": "workflow_dispatch" if manual else "workflow_run", "payload": {"workflow_run": run}}
        harness = """
const context = JSON.parse(process.argv[1]);
const calls = [], errors = [];
const core = { info() {}, setFailed: message => errors.push(message), summary: { addRaw() { return this; }, async write() {} } };
const github = { rest: { actions: { async createWorkflowDispatch(input) { calls.push(input); return {status: 204}; } } } };
""" + "await (async () => {\n" + script + "\n})(); console.log(JSON.stringify({calls, errors}));"
        result = subprocess.run(["node", "--input-type=module", "-e", harness, json.dumps(context)], check=True, text=True, capture_output=True)
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
