"""Resolve declared suites and dispatch their workload-specific validators."""

import json
import re

import counter_contract
import variable_contract
from contract_support import require

VALIDATORS = {"counter": counter_contract, "variable": variable_contract}


def load_suite(root, lock, name="counter"):
    require(name in VALIDATORS, "unsupported suite")
    require(lock["profile"] == "release" and lock["rust_features"] == ["observability"], "unsupported SDK build configuration")
    components = json.loads((root / "components.json").read_text())
    suite = json.loads((root / f"suites/{name}.json").read_text())
    require(components["schema_version"] == suite["schema_version"] == 1, "unsupported component/suite schema")
    registry = components["components"]

    def dependencies(name, ancestors=()):
        require(name in registry, f"unknown component: {name}")
        require(name not in ancestors, "component dependency cycle")
        item = registry[name]
        require(item["source"] in lock["sources"], f"missing source for {name}")
        resolved = {name}
        for dependency in item["depends_on"]:
            resolved.update(dependencies(dependency, ancestors + (name,)))
        return resolved

    for component_name in registry:
        dependencies(component_name)
    require(suite["id"] == name, "suite identity mismatch")
    require(set(suite["implementations"]) == {"rust", "go"}, "suite requires Rust and Go adapters")
    for implementation, adapter in suite["implementations"].items():
        dependencies(adapter["component"])
        require(registry[adapter["component"]]["source"] == implementation, "adapter/source mismatch")
        require((root / adapter["harness"]).is_dir(), "missing harness directory")
        require(re.fullmatch(r"[a-z][a-z0-9_-]*", adapter["executable"]), "invalid executable name")
    require(suite["implementations"]["rust"]["component"] in dependencies(suite["implementations"]["go"]["component"]), "Go adapter must depend on the Rust SDK")
    require(suite["metric"] == {"id": "latency", "unit": "ns/op", "direction": "smaller"}, "unsupported suite metric")
    path = root / suite["contract"]
    contract = json.loads(path.read_text())
    VALIDATORS[name].validate_contract(contract)
    return suite, contract, path
