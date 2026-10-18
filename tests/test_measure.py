from copy import deepcopy
import math
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from comparison import (
    build_comparison_summary,
    compute_statistics,
    generate_markdown_report,
    validate_measured_samples,
)
from suite_registry import load_suite


class MeasureTests(unittest.TestCase):
    def setUp(self):
        self.lock = {
            "sources": {
                "rust": {"repository": "https://github.com/qortoo/qortoo-rs.git", "sha": "859bb8330d4ffc74241437b7ef4e9685b7bd7ba6"},
                "go": {"repository": "https://github.com/qortoo/qortoo-go.git", "sha": "e54206f329bbbb5fd7852e1de41c68f1a12e9d66"},
            },
            "profile": "release",
            "rust_features": ["observability"],
        }
        self.counter_suite = load_suite(ROOT, self.lock, "counter")

    def test_compute_statistics_known_values(self):
        values = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0, 100.0]
        stats = compute_statistics(values)
        self.assertEqual(stats["count"], 10)
        self.assertEqual(stats["min"], 10.0)
        self.assertEqual(stats["max"], 100.0)
        self.assertEqual(stats["mean"], 55.0)
        self.assertEqual(stats["median"], 55.0)
        self.assertAlmostEqual(stats["stddev"], 30.2765, places=3)
        self.assertEqual(stats["p95"], 100.0)

    def test_compute_statistics_empty_or_invalid(self):
        with self.assertRaisesRegex(ValueError, "values list must not be empty"):
            compute_statistics([])
        with self.assertRaisesRegex(ValueError, "invalid measurement value"):
            compute_statistics([1.0, float("nan")])
        with self.assertRaisesRegex(ValueError, "invalid measurement value"):
            compute_statistics([1.0, -5.0])

    def _make_mock_samples(self, count_per_case=10, profile="full", warmup=False):
        samples = []
        suite, contract, _ = self.counter_suite
        for scenario in contract["scenarios"]:
            for impl in ("rust", "go"):
                for r in range(count_per_case):
                    latency = 100.0 if impl == "rust" else 150.0
                    samples.append({
                        "schema_version": 1,
                        "contract_id": contract["id"],
                        "implementation": impl,
                        "profile": profile,
                        "scenario": scenario["id"],
                        "operations": scenario["budget"],
                        "elapsed_ns": int(latency * scenario["budget"]),
                        "ns_per_op": latency,
                        "round_index": r,
                        "warmup": warmup,
                    })
        return samples

    def test_validate_measured_samples_success(self):
        samples = self._make_mock_samples(count_per_case=10, profile="full", warmup=False)
        validate_measured_samples(samples, [self.counter_suite], expected_rounds=10, expected_profile="full")

    def test_validate_measured_samples_rejects_insufficient_samples(self):
        samples = self._make_mock_samples(count_per_case=9, profile="full", warmup=False)
        with self.assertRaisesRegex(ValueError, "expected 100 total samples, got 90"):
            validate_measured_samples(samples, [self.counter_suite], expected_rounds=10, expected_profile="full")

    def test_validate_measured_samples_rejects_excess_samples(self):
        samples = self._make_mock_samples(count_per_case=11, profile="full", warmup=False)
        with self.assertRaisesRegex(ValueError, "expected 100 total samples, got 110"):
            validate_measured_samples(samples, [self.counter_suite], expected_rounds=10, expected_profile="full")

    def test_validate_measured_samples_rejects_wrong_profile(self):
        samples = self._make_mock_samples(count_per_case=10, profile="smoke", warmup=False)
        with self.assertRaisesRegex(ValueError, "expected profile full, got smoke"):
            validate_measured_samples(samples, [self.counter_suite], expected_rounds=10, expected_profile="full")

    def test_validate_rejects_warmup_sample_injection(self):
        samples = self._make_mock_samples(count_per_case=10, profile="full", warmup=False)
        samples[0]["warmup"] = True
        with self.assertRaisesRegex(ValueError, "warmup sample must not be included"):
            validate_measured_samples(samples, [self.counter_suite], expected_rounds=10, expected_profile="full")

    def test_validate_rejects_contract_id_mismatch(self):
        samples = self._make_mock_samples(count_per_case=10, profile="full", warmup=False)
        samples[0]["contract_id"] = "counter/v999"
        with self.assertRaisesRegex(ValueError, "sample contract_id mismatch"):
            validate_measured_samples(samples, [self.counter_suite], expected_rounds=10, expected_profile="full")

    def test_validate_rejects_budget_mismatch(self):
        samples = self._make_mock_samples(count_per_case=10, profile="full", warmup=False)
        samples[0]["operations"] = 1
        with self.assertRaisesRegex(ValueError, "sample operations budget mismatch"):
            validate_measured_samples(samples, [self.counter_suite], expected_rounds=10, expected_profile="full")

    def test_validate_rejects_duplicate_round_sample(self):
        samples = self._make_mock_samples(count_per_case=10, profile="full", warmup=False)
        # Duplicate round 0 to round 1 for the first case
        samples[1] = deepcopy(samples[0])
        with self.assertRaisesRegex(ValueError, "round_index set must be exactly 0..9"):
            validate_measured_samples(samples, [self.counter_suite], expected_rounds=10, expected_profile="full")

    def test_validate_rejects_unexpected_scenario(self):
        samples = self._make_mock_samples(count_per_case=10, profile="full", warmup=False)
        samples[0]["scenario"] = "UnknownScenario"
        with self.assertRaisesRegex(ValueError, "unexpected or unsupported sample case"):
            validate_measured_samples(samples, [self.counter_suite], expected_rounds=10, expected_profile="full")

    def test_build_comparison_summary_structure(self):
        samples = self._make_mock_samples(count_per_case=10, profile="full", warmup=False)
        metadata = {
            "platform": "test-os",
            "rustc": "rustc 1.97.1",
            "go": "go version go1.27.2",
            "harness_sha": "dd56c56c4953cf48a514f37568bddd23c635b5ee",
            "harness_dirty": True,
            "performance_baseline": False,
        }
        summary = build_comparison_summary(samples, [self.counter_suite], metadata)
        self.assertEqual(summary["schema_version"], 1)
        self.assertEqual(summary["harness_sha"], "dd56c56c4953cf48a514f37568bddd23c635b5ee")
        self.assertEqual(summary["harness_dirty"], True)
        self.assertEqual(summary["performance_baseline"], False)
        self.assertEqual(len(summary["suites"]), 1)
        suite_res = summary["suites"][0]
        self.assertEqual(suite_res["suite_id"], "counter")
        self.assertEqual(len(suite_res["scenarios"]), 5)
        first_sc = suite_res["scenarios"][0]
        self.assertEqual(first_sc["rust"]["median"], 100.0)
        self.assertEqual(first_sc["go"]["median"], 150.0)
        self.assertEqual(first_sc["diff_median_ns_per_op"], 50.0)
        self.assertEqual(first_sc["ratio_median"], 1.5)

        report = generate_markdown_report(summary, metadata)
        self.assertIn("# Same-Host Benchmark Comparison Report", report)
        self.assertIn("(dirty: `True`)", report)
        self.assertIn("to mitigate host condition drift", report)
        self.assertIn("not measured or reported by this latency harness (latency only)", report)
        self.assertIn("IncreaseBy", report)
        self.assertIn("1.50x", report)


if __name__ == "__main__":
    unittest.main()
