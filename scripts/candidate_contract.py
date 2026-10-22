"""Validate candidate-only source selections without relaxing official inputs."""

import re

from contract_support import require

REPOSITORIES = {"rust": "https://github.com/qortoo/qortoo-rs.git", "go": "https://github.com/qortoo/qortoo-go.git"}


def source(value):
    require(isinstance(value, dict), "source must be an object")
    require(re.fullmatch(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\.git", value.get("repository", "")), "invalid candidate repository")
    require(re.fullmatch(r"[0-9a-f]{40}", value.get("sha", "")), "candidate source requires a full SHA")


def validate_lock(lock):
    candidate = lock["candidate"]
    require(candidate["schema_version"] == 1 and candidate["channel"] == "candidate", "invalid candidate channel")
    require(candidate["leg"] in {"base", "head"} and candidate["component"] in REPOSITORIES, "invalid candidate leg/component")
    require(type(candidate["pr_number"]) is int and candidate["pr_number"] > 0, "invalid PR number")
    require("request" not in lock, "candidate cannot use the official request ledger")
    require(set(lock["sources"]) == set(REPOSITORIES), "invalid candidate sources")
    for item in lock["sources"].values():
        source(item)
    for leg in ("base", "head"):
        source(candidate[leg])
    component = candidate["component"]
    require(candidate["base"]["repository"] == REPOSITORIES[component], "PR base must be the registered SDK")
    require(lock["sources"][component] == candidate[candidate["leg"]], "candidate revision differs from selection")
    other = "go" if component == "rust" else "rust"
    require(lock["sources"][other] == candidate["dependency"], "candidate dependency differs from selection")
    require(candidate["dependency"]["repository"] == REPOSITORIES[other], "candidate dependency must be the registered SDK")
    source(candidate["dependency"])
    require(re.fullmatch(r"[0-9a-f]{40}", candidate["go_ci_rust_sha"]), "invalid declared Go pin")
    require(lock["profile"] == "release" and lock["rust_features"] == ["observability"], "unsupported candidate build")
    return candidate
