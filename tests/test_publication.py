from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from central_inputs import resolve
from export_results import export_run, json_bytes
from publish_results import append_bundle, command, latest_eligible, publish, read_json, verify_bundle
import test_export_results as export_tests


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = export_tests.ExportTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        f = self.fixture
        f.metadata.update(harness_dirty=False, performance_baseline=True, harness_files_sha256={})
        f.refresh_metadata()
        self.url = "https://github.com/qortoo/qortoo-bench/actions/runs/123/attempts/1"
        self.manifest = export_run(f.run, f.output, f.environment, "official", self.url)
        self.expected = {key: self.manifest[key] for key in ("run_id", "run_url", "harness_sha", "sources")}
        self.expected["harness_files_sha256"] = {}
        self.action_calls = 0

    def action(self, group, metrics, history):
        self.action_calls += 1
        data = read_json(history) if history.exists() else {"lastUpdate": 1, "repoUrl": "fixture", "entries": {}}
        data["entries"].setdefault(group["name"], []).append({"commit": {"id": "a" * 40}, "tool": group["tool"], "date": 1,
                                                           "benches": read_json(metrics)})
        history.write_bytes(json_bytes(data))

    def test_resolver_rejects_refs_shell_text_and_unapproved_repositories(self):
        lock = json.loads((ROOT / "sources.lock.json").read_text())
        self.assertEqual(resolve(lock), lock)
        self.assertEqual(resolve(lock, "d" * 40)["sources"]["rust"]["sha"], "d" * 40)
        for value in ["main", "HEAD", "$(echo bad)", "A" * 40, "abc\n"]:
            with self.assertRaises(ValueError):
                resolve(lock, value)
        lock["sources"]["rust"]["repository"] = "https://example.invalid/other.git"
        with self.assertRaises(ValueError):
            resolve(lock)

    def test_rebuilds_artifact_and_rejects_changed_metrics_or_context(self):
        f = self.fixture
        verify_bundle(f.output, self.expected)
        with self.assertRaisesRegex(ValueError, "publication context"):
            verify_bundle(f.output, {**self.expected, "harness_sha": "b" * 40})
        with self.assertRaisesRegex(ValueError, "trusted commit"):
            verify_bundle(f.output, {**self.expected, "harness_files_sha256": {"other": "b" * 64}})
        path = f.output / "metrics/counter.json"
        path.write_bytes(path.read_bytes() + b" ")
        with self.assertRaisesRegex(ValueError, "bundle hash mismatch"):
            verify_bundle(f.output, self.expected)

    def test_rejects_symlink_bundle_members(self):
        f = self.fixture
        (f.output / "link.json").symlink_to(f.output / "summary.json")
        with self.assertRaisesRegex(ValueError, "symlink"):
            verify_bundle(f.output, self.expected)

    def test_atomic_data_commit_and_identical_retry_do_not_duplicate_history(self):
        f = self.fixture
        remote = f.root / "remote.git"
        command(["git", "init", "--bare", "-q", remote])
        first = publish(f.output, self.manifest, str(remote), [123, 1], self.action, lambda *args: True)
        second = publish(f.output, self.manifest, str(remote), [123, 1], self.action, lambda *args: True)
        self.assertEqual(first["data_sha"], second["data_sha"])
        self.assertTrue(second["already_published"])
        self.assertEqual(self.action_calls, 1)
        root = json.loads(command(["git", "--git-dir", remote, "show", "bench-data:index.json"]))
        series = self.manifest["groups"][0]["name"]
        self.assertEqual(root["latest"][series]["run_id"], f.run.name)
        stored = command(["git", "--git-dir", remote, "show", f"bench-data:runs/{f.run.name}/manifest.json"])
        self.assertEqual(json.loads(stored), self.manifest)

    def test_corrupt_history_and_changed_run_id_content_fail_without_append(self):
        f = self.fixture
        root = f.root / "data"
        root.mkdir()
        append_bundle(root, f.output, self.manifest, [123, 1], self.action, lambda *args: True)
        group = self.manifest["groups"][0]
        history = root / group["history_file"]
        history.write_text("corrupt JSON")
        with self.assertRaises(ValueError):
            append_bundle(root, f.output, self.manifest, [123, 1], self.action, lambda *args: True)
        self.assertEqual(self.action_calls, 1)
        self.assertEqual(history.read_text(), "corrupt JSON")

    def test_latest_uses_source_ancestry_and_request_order_not_finish_time(self):
        sources = {"rust": {"repository": "rust", "sha": "a"}, "go": {"repository": "go", "sha": "b"}}
        previous = {"sources": sources, "request_order": [200, 1]}
        older_request = {"sources": deepcopy(sources), "request_order": [100, 1]}
        self.assertFalse(latest_eligible(older_request, previous, lambda *args: True))
        retry = {"sources": deepcopy(sources), "request_order": [200, 2]}
        self.assertTrue(latest_eligible(retry, previous, lambda *args: True))
        newer = {"sources": deepcopy(sources), "request_order": [100, 1]}
        newer["sources"]["rust"]["sha"] = "c"
        ancestry = lambda repo, old, new: old == new or (old, new) == ("a", "c")
        self.assertTrue(latest_eligible(newer, previous, ancestry))
        self.assertFalse(latest_eligible(previous, newer, ancestry))


if __name__ == "__main__":
    unittest.main()
