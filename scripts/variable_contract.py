"""Variable payload and workload validation, independent of either SDK adapter."""

import json
import math
import re

from contract_support import positive, require

OPERATIONS = {"Set", "Get", "Transaction", "SyncLocal"}


def payload(sequence, size):
    value = {"sequence": f"{sequence:016d}", "data": ""}
    overhead = len(json.dumps(value, separators=(",", ":")).encode())
    require(sequence < 10**16 and size >= overhead, "invalid payload size/sequence")
    value["data"] = "x" * (size - overhead)
    return value


def validate_contract(contract):
    require(set(contract) == {"schema_version", "id", "transaction_operations", "max_payload_bytes_per_chunk", "execution", "payloads", "scenarios"}, "unknown/missing Variable field")
    require(contract["schema_version"] == 1 and contract["id"] == "variable/v1", "unsupported Variable contract")
    require(positive(contract["transaction_operations"]), "invalid transaction size")
    require(positive(contract["max_payload_bytes_per_chunk"]), "invalid payload envelope")
    execution = contract["execution"]
    require(set(execution) == {"realtime", "handler_registered", "measurement_threads", "warmup_rounds", "measured_rounds", "smoke_warmup_rounds", "smoke_rounds"}, "unknown/missing execution field")
    require(execution["realtime"] is False and execution["handler_registered"] is False and execution["measurement_threads"] == 1, "unsupported execution conditions")
    require(positive(execution["measured_rounds"]) and execution["smoke_rounds"] == 1, "invalid round counts")
    require(type(execution["warmup_rounds"]) is int and execution["warmup_rounds"] >= 0 and execution["smoke_warmup_rounds"] == 0, "invalid warmup counts")
    payloads = {}
    for spec in contract["payloads"]:
        require(set(spec) == {"id", "json_bytes"}, "unknown/missing payload field")
        require(spec["id"] not in payloads and re.fullmatch(r"[A-Za-z0-9_-]+", spec["id"]), "duplicate/invalid payload ID")
        require(positive(spec["json_bytes"]), "invalid JSON size")
        payload(0, spec["json_bytes"])
        payloads[spec["id"]] = spec["json_bytes"]
    require(bool(payloads), "missing payloads")
    seen = set()
    for scenario in contract["scenarios"]:
        require(set(scenario) == {"id", "operation", "payload", "budget", "smoke_budget", "reset_every"}, "unknown/missing case field")
        name, data = scenario["operation"], scenario["payload"]
        require(name in OPERATIONS and data in payloads, "unsupported operation/payload")
        require(scenario["id"] == f"{name}-{data}" and (name, data) not in seen, "invalid/duplicate case")
        seen.add((name, data))
        require(positive(scenario["budget"]) and positive(scenario["smoke_budget"]) and scenario["smoke_budget"] <= scenario["budget"], "invalid budget")
        multiplier = contract["transaction_operations"] if name == "Transaction" else 1
        require(scenario["budget"] * multiplier < 10**16, "sequence exceeds fixed width")
        if name == "Get":
            require(scenario["reset_every"] is None, "Get does not reset")
            writes = 0
        else:
            require(positive(scenario["reset_every"]) and scenario["smoke_budget"] > scenario["reset_every"], "smoke must exercise reset")
            writes = scenario["reset_every"] * multiplier
        require((writes + 1) * payloads[data] <= contract["max_payload_bytes_per_chunk"], "serialized payload envelope exceeded")
    require(seen == {(operation, data) for operation in OPERATIONS for data in payloads}, "missing Variable cases")


def validate_sample(sample, contract, scenario, implementation, profile):
    budget = scenario["smoke_budget"] if profile == "smoke" else scenario["budget"]
    spec = next(p for p in contract["payloads"] if p["id"] == scenario["payload"])
    require(sample["schema_version"] == 1 and sample["contract_id"] == contract["id"], "sample contract mismatch")
    require(sample["scenario"] == scenario["id"] and sample["profile"] == profile and sample["implementation"] == implementation, "sample identity mismatch")
    require(sample["operations"] == budget and sample["payload_id"] == spec["id"] and sample["payload_json_bytes"] == spec["json_bytes"], "payload/budget mismatch")
    require(type(sample["elapsed_ns"]) is int and sample["elapsed_ns"] > 0, "invalid elapsed time")
    require(math.isfinite(sample["ns_per_op"]) and math.isclose(sample["ns_per_op"], sample["elapsed_ns"] / budget), "invalid latency")
    operation = scenario["operation"]
    read = operation == "Get"
    remaining = budget
    chunks = []
    total_writes = 0
    while remaining:
        count = remaining if read else min(remaining, scenario["reset_every"])
        writes = 0 if read else count * (contract["transaction_operations"] if operation == "Transaction" else 1)
        chunks.append({"operations": count, "value": payload(writes, spec["json_bytes"]),
                       "previous_sequence": f"{writes-1:016d}" if operation in {"Set", "SyncLocal"} else None})
        total_writes += writes
        remaining -= count
    require(sample["chunks"] == chunks, "previous/final payload or reset mismatch")
    expected = dict(variable_creations=len(chunks), variable_releases=len(chunks), client_creations=1, client_releases=1,
                    resets=len(chunks)-1, writes=total_writes, reads=budget if read else 0,
                    transactions=budget if operation == "Transaction" else 0, syncs=budget if operation == "SyncLocal" else 0,
                    setup_writes=len(chunks))
    require(sample["stats"] == expected, "Variable operation/release accounting mismatch")
    for chunk in sample["chunks"]:
        require(len(json.dumps(chunk["value"], separators=(",", ":")).encode()) == spec["json_bytes"], "observed JSON byte count mismatch")
