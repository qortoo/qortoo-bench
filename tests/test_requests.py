import base64
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from bench_requests import claim, finish, load, selection
from data_store import command, transaction
from export_results import export_run, json_bytes
from publish_results import append_bundle, catalog, publish
import test_publication


class RequestTests(unittest.TestCase):
    def setUp(self):
        self.lock = json.loads((ROOT / "sources.lock.json").read_text())
        self.rust, self.go = "a" * 40, "b" * 40
        self.pin = "c" * 40
        self.origin = {"event": "push", "head_branch": "main", "head_sha": self.rust,
                       "path": ".github/workflows/benchmark.yml", "repository": {"full_name": "qortoo/qortoo-rs"}}
        self.calls = []

    def api(self, endpoint):
        self.calls.append(endpoint)
        if "/actions/runs/" in endpoint:
            return deepcopy(self.origin)
        if endpoint.endswith("/commits/main"):
            return {"sha": self.go}
        if "/contents/" in endpoint:
            return {"content": base64.b64encode(f"  QORTOO_RS_REF: {self.pin}\n".encode()).decode()}
        raise AssertionError(endpoint)

    def test_rust_event_pins_changed_sha_and_resolves_go_once(self):
        key, event, lock = selection(self.lock, "rust", self.rust, "12", "20", self.api)
        self.assertEqual(key, f"rust-{self.rust}")
        self.assertEqual(lock["sources"]["rust"]["sha"], self.rust)
        self.assertEqual(lock["sources"]["go"]["sha"], self.go)
        self.assertEqual(event["go_ci_rust_sha"], self.pin)
        self.assertNotEqual(self.rust, self.pin)
        self.assertEqual(sum(x.endswith("/commits/main") for x in self.calls), 1)

    def test_go_event_uses_its_pin_instead_of_rust_main(self):
        self.origin.update(head_sha=self.go, repository={"full_name": "qortoo/qortoo-go"})
        _, _, lock = selection(self.lock, "go", self.go, "12", "20", self.api)
        self.assertEqual(lock["sources"]["rust"]["sha"], self.pin)
        self.assertEqual(lock["sources"]["go"]["sha"], self.go)
        self.assertFalse(any(x.endswith("/commits/main") for x in self.calls))

    def test_untrusted_or_mismatched_events_rejected(self):
        for key, value in [("event", "pull_request"), ("head_branch", "feature"),
                           ("head_sha", "f" * 40), ("path", ".github/workflows/ci.yml"),
                           ("repository", {"full_name": "other/repo"})]:
            saved = deepcopy(self.origin)
            self.origin[key] = value
            with self.assertRaisesRegex(ValueError, "not the claimed"):
                selection(self.lock, "rust", self.rust, "12", "20", self.api)
            self.origin = saved
        with self.assertRaises(ValueError):
            selection(self.lock, "rust", "main", "12", "20", self.api)

    def test_duplicate_active_request_and_retry_freeze_original_selection(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            key, event, lock = selection(self.lock, "rust", self.rust, "12", "20", self.api)
            first, run = claim(root, key, event, lock, "20-1", lambda _: False)
            self.assertTrue(run)
            replacement = deepcopy(lock)
            replacement["sources"]["go"]["sha"] = "d" * 40
            record, run = claim(root, key, event, replacement, "21-1", lambda _: False)
            self.assertFalse(run)
            self.assertEqual(record["active_attempt"], "20-1")
            self.assertEqual(record["lock"], first["lock"])
            self.assertTrue(finish(root, key, "20-1", "incompatible", "code 211"))
            record, run = claim(root, key, event, replacement, "22-1", lambda _: True)
            self.assertTrue(run)
            self.assertEqual(record["lock"], first["lock"])
            self.assertFalse(finish(root, key, "20-1", "failed"))
            self.assertEqual(load(root, key)["status"], "running")
            self.assertEqual(catalog(root)[0]["latest"], {})

    def test_cancelled_attempt_can_be_reclaimed_without_finalizer(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            key, event, lock = selection(self.lock, "rust", self.rust, "12", "20", self.api)
            claim(root, key, event, lock, "20-1", lambda _: False)
            record, run = claim(root, key, event, lock, "20-2", lambda _: True)
            self.assertTrue(run)
            self.assertEqual(record["attempts"]["20-1"]["status"], "abandoned")

    def test_success_and_ledger_commit_together_and_duplicate_never_appends(self):
        f = test_publication.PublicationTests()
        f.setUp()
        self.addCleanup(f.doCleanups)
        fixture = f.fixture
        key, event, lock = selection(self.lock, "rust", self.rust, "12", "20", self.api)
        lock["sources"] = fixture.metadata["sources"]
        request = {"request_id": key, "event": event}
        fixture.metadata["request"] = fixture.inputs["request"] = request
        fixture.refresh_metadata()
        # Refresh the input snapshot and corresponding metadata hash.
        (fixture.run / "run-inputs.json").write_bytes(json_bytes(fixture.inputs))
        from export_results import digest
        fixture.metadata["run_inputs_sha256"] = digest((fixture.run / "run-inputs.json").read_bytes())
        fixture.refresh_metadata()
        output = fixture.root / "request-export"
        manifest = export_run(fixture.run, output, fixture.environment, "official", f.url)
        root = fixture.root / "ledger"
        root.mkdir()
        claim(root, key, event, lock, fixture.run.name, lambda _: False)
        self.assertTrue(append_bundle(root, output, manifest, [123, 1], f.action, lambda *a: True))
        self.assertEqual(load(root, key)["status"], "published")
        count = f.action_calls
        self.assertFalse(append_bundle(root, output, manifest, [124, 1], f.action, lambda *a: True))
        self.assertEqual(f.action_calls, count)
        self.assertFalse(finish(root, key, fixture.run.name, "failed"))

    def test_conflicting_transactions_preserve_both_requests(self):
        with tempfile.TemporaryDirectory() as td:
            remote = Path(td) / "data.git"
            command(["git", "init", "--bare", "-q", remote])
            def first(root):
                (root / "first").write_text("one")
                return "first"
            transaction(str(remote), first, "fixture")
            calls = []
            def racing(root):
                calls.append(1)
                (root / "second").write_text("two")
                if len(calls) == 1:
                    transaction(str(remote), lambda other: (other / "third").write_text("three"), "other")
                return "second"
            self.assertEqual(transaction(str(remote), racing, "fixture"), "second")
            self.assertEqual(len(calls), 2)
            names = command(["git", "--git-dir", remote, "ls-tree", "--name-only", "bench-data"]).splitlines()
            self.assertEqual(names, ["first", "second", "third"])

    def test_request_write_racing_publication_does_not_lose_either(self):
        f = test_publication.PublicationTests()
        f.setUp()
        self.addCleanup(f.doCleanups)
        remote = f.fixture.root / "race.git"
        command(["git", "init", "--bare", "-q", remote])
        injected = []
        def action(group, metrics, history):
            f.action(group, metrics, history)
            if not injected:
                injected.append(True)
                def other_request(root):
                    claim(root, "manual-999", {"kind": "manual"}, self.lock, "999-1", lambda _: False)
                transaction(str(remote), other_request, "fixture request")
        result = publish(f.fixture.output, f.manifest, str(remote), [123, 1], action, lambda *args: True)
        self.assertFalse(result["already_published"])
        index = json.loads(command(["git", "--git-dir", remote, "show", "bench-data:index.json"]))
        series = f.manifest["groups"][0]["name"]
        self.assertEqual(index["latest"][series]["run_id"], f.fixture.run.name)
        request = json.loads(command(["git", "--git-dir", remote, "show", "bench-data:requests/manual-999.json"]))
        self.assertEqual(request["active_attempt"], "999-1")
        history = json.loads(command(["git", "--git-dir", remote, "show", "bench-data:" + f.manifest["groups"][0]["history_file"]]))
        self.assertEqual(len(history["entries"][series]), 1)


if __name__ == "__main__":
    unittest.main()
