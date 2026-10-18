from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from suite_registry import VALIDATORS, load_suite
from variable_contract import validate_contract


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.variable = json.loads((ROOT / "contracts/variable.json").read_text())

    def test_registered_suites_load_together(self):
        lock = json.loads((ROOT / "sources.lock.json").read_text())
        for name in VALIDATORS:
            suite, contract, _ = load_suite(ROOT, lock, name)
            self.assertEqual(suite["id"], name)
            self.assertEqual(contract["id"], f"{name}/v1")

    def test_envelope_includes_seed_payload(self):
        # Ten writes plus the seeded value need eleven full serialized payloads.
        c = deepcopy(self.variable)
        c["max_payload_bytes_per_chunk"] = 10 * 16384
        with self.assertRaisesRegex(ValueError, "serialized payload envelope"):
            validate_contract(c)

    def test_transaction_size_changes_payload_envelope(self):
        c = deepcopy(self.variable)
        c["transaction_operations"] = 20
        with self.assertRaisesRegex(ValueError, "serialized payload envelope"):
            validate_contract(c)

    def test_missing_payload_operation_pair_is_rejected(self):
        c = deepcopy(self.variable)
        c["scenarios"].pop()
        with self.assertRaisesRegex(ValueError, "missing Variable cases"):
            validate_contract(c)

    def test_duplicate_case_is_rejected(self):
        c = deepcopy(self.variable)
        c["scenarios"].append(deepcopy(c["scenarios"][0]))
        with self.assertRaisesRegex(ValueError, "invalid/duplicate case"):
            validate_contract(c)

    def test_payload_must_fit_json_shape(self):
        c = deepcopy(self.variable)
        c["payloads"][0]["json_bytes"] = 1
        with self.assertRaisesRegex(ValueError, "invalid payload size"):
            validate_contract(c)

    def test_unknown_payload_is_rejected(self):
        c = deepcopy(self.variable)
        c["scenarios"][0]["payload"] = "missing"
        with self.assertRaisesRegex(ValueError, "unsupported operation/payload"):
            validate_contract(c)

    def test_background_handler_work_is_rejected(self):
        c = deepcopy(self.variable)
        c["execution"]["handler_registered"] = True
        with self.assertRaisesRegex(ValueError, "unsupported execution conditions"):
            validate_contract(c)


if __name__ == "__main__":
    unittest.main()
