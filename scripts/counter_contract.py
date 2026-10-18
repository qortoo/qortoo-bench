"""Validate the Counter suite and its per-round workload evidence."""

import math

from contract_support import positive, require

SCENARIOS = {"IncreaseBy", "GetValue", "Transaction", "SyncLocal", "BuildCounter"}
MUTATING = {"IncreaseBy", "Transaction", "SyncLocal"}


def load_suite(root, lock):
    from suite_registry import load_suite as load
    return load(root, lock, "counter")


def validate_contract(contract):
    require(set(contract) == {"schema_version", "id", "transaction_operations", "max_counters_per_client", "execution", "scenarios"}, "unknown/missing contract field")
    require(contract["schema_version"] == 1 and contract["id"] == "counter/v1", "unsupported Counter contract")
    require(positive(contract["transaction_operations"]), "invalid transaction size")
    require(positive(contract["max_counters_per_client"]), "invalid client capacity")
    execution = contract["execution"]
    require(set(execution) == {"realtime", "handler_registered", "measurement_threads", "warmup_rounds", "measured_rounds", "smoke_warmup_rounds", "smoke_rounds"}, "unknown/missing execution field")
    require(execution["realtime"] is False and execution["handler_registered"] is False, "unsupported background work")
    require(execution["measurement_threads"] == 1, "measurement must be single-threaded")
    for key in ("measured_rounds", "smoke_rounds"):
        require(positive(execution[key]), f"invalid {key}")
    for key in ("warmup_rounds", "smoke_warmup_rounds"):
        require(type(execution[key]) is int and execution[key] >= 0, f"invalid {key}")
    require(execution["smoke_rounds"] == 1 and execution["smoke_warmup_rounds"] == 0, "smoke executes one validation round")
    names = [s["id"] for s in contract["scenarios"]]
    require(len(names) == len(set(names)) and set(names) == SCENARIOS, "missing/duplicate/unsupported scenario")
    for scenario in contract["scenarios"]:
        require(set(scenario) == {"id", "budget", "smoke_budget", "reset_every"}, "unknown/missing scenario field")
        require(positive(scenario["budget"]) and positive(scenario["smoke_budget"]), "invalid scenario budget")
        require(scenario["smoke_budget"] <= scenario["budget"], "smoke exceeds full budget")
        require(scenario["budget"] * contract["transaction_operations"] < 2**63, "workload exceeds value range")
        if scenario["id"] in MUTATING:
            require(positive(scenario["reset_every"]), "missing reset interval")
            require(scenario["smoke_budget"] > scenario["reset_every"], "smoke must exercise a reset")
        else:
            require(scenario["reset_every"] is None, "read/build scenarios cannot reset")
        if scenario["id"] == "BuildCounter":
            require(scenario["budget"] <= contract["max_counters_per_client"], "BuildCounter exceeds client capacity")


def validate_sample(sample, contract, scenario, implementation, profile):
    budget = scenario["smoke_budget"] if profile == "smoke" else scenario["budget"]
    require(sample["schema_version"] == 1 and sample["contract_id"] == contract["id"], "sample contract mismatch")
    require(sample["implementation"] == implementation and sample["profile"] == profile, "sample identity mismatch")
    require(sample["scenario"] == scenario["id"] and sample["operations"] == budget, "sample workload mismatch")
    elapsed = sample["elapsed_ns"]
    require(type(elapsed) is int and elapsed > 0, "invalid elapsed time")
    require(math.isfinite(sample["ns_per_op"]) and math.isclose(sample["ns_per_op"], elapsed / budget), "invalid per-operation time")
    expected = dict(counter_creations=0, counter_releases=0, client_creations=1, client_releases=1,
                    resets=0, writes=0, reads=0, transactions=0, syncs=0, setup_writes=0)
    chunks = []
    name = scenario["id"]
    if name in MUTATING:
        remaining = budget
        while remaining:
            count = min(remaining, scenario["reset_every"])
            value = count * (contract["transaction_operations"] if name == "Transaction" else 1)
            chunks.append({"operations": count, "value": value})
            remaining -= count
        expected.update(counter_creations=len(chunks), counter_releases=len(chunks), resets=len(chunks) - 1,
                        writes=sum(c["value"] for c in chunks), transactions=budget if name == "Transaction" else 0,
                        syncs=budget if name == "SyncLocal" else 0)
    elif name == "GetValue":
        expected.update(counter_creations=1, counter_releases=1, reads=budget, setup_writes=1)
        chunks = [{"operations": budget, "value": 1}]
    else:
        expected.update(counter_creations=budget, counter_releases=budget)
    require(sample["stats"] == expected, f"{implementation}/{name}: workload/release accounting mismatch")
    require(sample["chunks"] == chunks, f"{implementation}/{name}: reset/final-value mismatch")
