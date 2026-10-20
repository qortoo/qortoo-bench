from copy import deepcopy
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from comparison import build_comparison_summary
from export_results import action_metrics, digest, export_run, identity, json_bytes, series_definition


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.run = self.root / "synthetic-run"
        self.run.mkdir()
        self.output = self.root / "bundle"
        self.suite = json.loads((ROOT / "suites/counter.json").read_text())
        self.contract = json.loads((ROOT / "contracts/counter.json").read_text())
        lock = json.loads((ROOT / "sources.lock.json").read_text())
        self.inputs = {"schema_version": 2, "sources": lock["sources"], "toolchains": lock["toolchains"],
                       "profile": "full", "build_profile": "release", "rust_features": ["observability"],
                       "input_files_sha256": {}, "suites": [{"suite": self.suite, "contract": self.contract}]}
        self.write("run-inputs.json", self.inputs)
        self.metadata = {"schema_version": 3, "status": "passed", "profile": "full", "performance_baseline": False,
                         "sources": lock["sources"], "build_profile": "release", "rust_features": ["observability"],
                         "input_files_sha256": {}, "suite_ids": ["counter"], "harness_sha": "a" * 40,
                         "harness_dirty": True, "platform": "synthetic-os", "rustc": "synthetic-rustc", "go": "synthetic-go",
                         "started_at_utc": "20260101T000000Z", "completed_at_utc": "2026-01-01T00:00:01Z",
                         "validated_samples": 100, "run_inputs_sha256": digest((self.run / "run-inputs.json").read_bytes()),
                         "native_sdk": {"manifest": {"profile": "release", "sdk_version": "fixture", "abi_version_major": 1,
                                                       "abi_version_minor": 0, "target": "synthetic-target"},
                                        "header_sha256": "b" * 64, "library_sha256": "c" * 64}}
        self.environment = {"id": "synthetic", "os": "synthetic-os", "cpu": "fixture", "arch": "fixture", "runner": "fixture"}
        self.metadata["environment"] = self.environment
        seed = json.loads((ROOT / "tests/fixtures/counter-samples.json").read_text())["samples"]
        self.samples = [{**deepcopy(s), "round_index": r, "warmup": False} for r in range(10) for s in seed]
        self.warmup = [{**deepcopy(s), "warmup": True} for s in seed]
        self.write("samples.json", self.samples)
        self.write("warmup-samples.json", self.warmup)
        self.refresh_metadata()
        (self.run / "counter.stdout.log").write_text("synthetic raw log\n")

    def write(self, name, value):
        (self.run / name).write_bytes(json_bytes(value))

    def refresh_metadata(self):
        self.write("metadata.json", self.metadata)
        self.write("comparison.json", build_comparison_summary(self.samples, [(self.suite, self.contract, None)], self.metadata))

    def test_export_preserves_provenance_raw_data_and_hashes(self):
        manifest = export_run(self.run, self.output, self.environment)
        self.assertFalse(manifest["performance_baseline"])
        self.assertEqual(manifest["sources"], self.inputs["sources"])
        for name, expected in manifest["files_sha256"].items():
            self.assertEqual(digest((self.output / name).read_bytes()), expected)
        with tarfile.open(fileobj=io.BytesIO((self.output / "raw.tar.gz").read_bytes())) as archive:
            for name, expected in manifest["raw_files_sha256"].items():
                data = archive.extractfile(name).read()
                self.assertEqual(data, (self.run / name).read_bytes())
                self.assertEqual(digest(data), expected)
        summary = json.loads((self.output / "summary.json").read_text())
        self.assertEqual(summary["native_sdk"], self.metadata["native_sdk"])
        metrics = json.loads((self.output / "metrics/counter.json").read_text())
        self.assertEqual(len(metrics), 10)
        self.assertEqual({m["value"] for m in metrics}, {100.0, 150.0})
        self.assertEqual(json.loads(metrics[0]["extra"])["harness_sha"], self.metadata["harness_sha"])
        index = json.loads((self.output / "index-entry.json").read_text())
        self.assertFalse(index["eligible_for_latest"])
        self.assertEqual(index["manifest"]["sha256"], digest((self.output / "manifest.json").read_bytes()))
        second = self.root / "bundle-again"
        export_run(self.run, second, self.environment)
        for file in self.output.rglob("*"):
            if file.is_file():
                self.assertEqual(file.read_bytes(), (second / file.relative_to(self.output)).read_bytes())

    def test_rejects_corrupt_sample_and_comparison_before_creating_bundle(self):
        for mutation in [lambda x: x[0].update(elapsed_ns=1), lambda x: x[0].update(operations=1),
                         lambda x: x[0].update(warmup=True), lambda x: x[10].update(round_index=0)]:
            with self.subTest(mutation=mutation):
                altered = deepcopy(self.samples)
                mutation(altered)
                self.write("samples.json", altered)
                with self.assertRaises(ValueError):
                    export_run(self.run, self.output, self.environment)
                self.assertFalse(self.output.exists())
        self.write("samples.json", self.samples)
        comparison = json.loads((self.run / "comparison.json").read_text())
        comparison["suites"][0]["scenarios"][0]["rust"]["median"] = 1
        self.write("comparison.json", comparison)
        with self.assertRaisesRegex(ValueError, "comparison differs"):
            export_run(self.run, self.output, self.environment)

    def test_rejects_incomplete_warmup_and_mismatched_input_identity(self):
        self.write("warmup-samples.json", self.warmup[:-1])
        with self.assertRaisesRegex(ValueError, "missing warmup"):
            export_run(self.run, self.output, self.environment)
        self.write("warmup-samples.json", self.warmup)
        self.metadata["sources"] = deepcopy(self.metadata["sources"])
        self.metadata["sources"]["rust"]["sha"] = "d" * 40
        self.refresh_metadata()
        with self.assertRaisesRegex(ValueError, "metadata/input mismatch"):
            export_run(self.run, self.output, self.environment)

    def test_official_channel_requires_clean_baseline_and_run_url(self):
        url = "https://github.com/qortoo/qortoo-bench/actions/runs/123/attempts/1"
        with self.assertRaisesRegex(ValueError, "clean baseline"):
            export_run(self.run, self.output, self.environment, "official", url)
        self.metadata.update(harness_dirty=False, performance_baseline=True)
        self.refresh_metadata()
        with self.assertRaisesRegex(ValueError, "Actions run URL"):
            export_run(self.run, self.output, self.environment, "official")
        manifest = export_run(self.run, self.output, self.environment, "official", url)
        self.assertTrue(manifest["performance_baseline"])
        self.assertTrue(manifest["groups"][0]["name"].startswith("official/"))

    def test_rejects_invalid_native_sdk_identity_and_snapshot_hash(self):
        self.metadata["native_sdk"]["library_sha256"] = "invalid"
        self.refresh_metadata()
        with self.assertRaisesRegex(ValueError, "invalid native SDK"):
            export_run(self.run, self.output, self.environment)
        self.metadata["native_sdk"]["library_sha256"] = "c" * 64
        self.refresh_metadata()
        (self.run / "run-inputs.json").write_bytes(json_bytes({**self.inputs, "profile": "changed"}))
        with self.assertRaises(ValueError):
            export_run(self.run, self.output, self.environment)

    def test_rejects_failed_smoke_unknown_environment_and_overwrite(self):
        for field, value in [("status", "failed"), ("profile", "smoke")]:
            original = self.metadata[field]
            self.metadata[field] = value
            self.write("metadata.json", self.metadata)
            with self.assertRaises(ValueError):
                export_run(self.run, self.output, self.environment)
            self.metadata[field] = original
        self.refresh_metadata()
        with self.assertRaisesRegex(ValueError, "environment OS"):
            export_run(self.run, self.output, {**self.environment, "os": "other"})
        export_run(self.run, self.output, self.environment)
        with self.assertRaisesRegex(ValueError, "already exists"):
            export_run(self.run, self.output, self.environment)

    def test_series_separates_conditions_but_tracks_sdk_revision_changes(self):
        args = [self.suite, self.contract, self.inputs, self.metadata, self.environment]
        base = identity(series_definition(*args))
        for index, key, value in [(1, "transaction_operations", 20), (2, "toolchains", {"rust": "other", "go": "other"}),
                                  (2, "rust_features", []), (4, "cpu", "other"), (3, "rustc", "other")]:
            changed = deepcopy(args)
            changed[index][key] = value
            self.assertNotEqual(base, identity(series_definition(*changed)))
        changed = deepcopy(args)
        changed[2]["sources"]["rust"]["sha"] = "d" * 40
        self.assertEqual(base, identity(series_definition(*changed)))

    def test_dependency_snapshot_is_hashed_exported_and_separates_series(self):
        data = b"locked dependency fixture\n"
        (self.run / "rust-0.lock").write_bytes(data)
        deps = {"rust-0.lock": digest(data)}
        before = identity(series_definition(self.suite, self.contract, self.inputs, self.metadata, self.environment))
        self.inputs["build_dependencies"] = self.metadata["build_dependencies"] = deps
        self.write("run-inputs.json", self.inputs)
        self.metadata["run_inputs_sha256"] = digest((self.run / "run-inputs.json").read_bytes())
        self.refresh_metadata()
        after = identity(series_definition(self.suite, self.contract, self.inputs, self.metadata, self.environment))
        self.assertNotEqual(before, after)
        manifest = export_run(self.run, self.output, self.environment)
        self.assertEqual(manifest["build_dependencies"], deps)
        self.assertIn("rust-0.lock", manifest["raw_files_sha256"])
        (self.run / "rust-0.lock").write_bytes(b"tampered\n")
        with self.assertRaisesRegex(ValueError, "dependency snapshot hash"):
            export_run(self.run, self.root / "tampered-export", self.environment)

    def test_server_extension_keeps_latency_and_throughput_separate(self):
        fixture = json.loads((ROOT / "tests/fixtures/server-metrics.json").read_text())
        for name, tool in [("latency", "customSmallerIsBetter"), ("throughput", "customBiggerIsBetter")]:
            actual, rows = action_metrics(fixture[name], {"synthetic": True})
            self.assertEqual(actual, tool)
            self.assertEqual(rows[0]["unit"], fixture[name][0]["unit"])
        with self.assertRaisesRegex(ValueError, "mixed improvement directions"):
            action_metrics(fixture["latency"] + fixture["throughput"], {})


if __name__ == "__main__":
    unittest.main()
