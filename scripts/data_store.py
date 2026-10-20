"""Optimistic Git transactions shared by request and result writers."""

import base64
import os
from pathlib import Path
import subprocess
import tempfile

REMOTE = "https://github.com/qortoo/qortoo-bench.git"


def command(argv, cwd=None, env=None):
    result = subprocess.run(list(map(str, argv)), cwd=cwd, env=env, text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError(f"command failed ({result.returncode}): {argv[0]}: {(result.stderr or result.stdout)[-1500:]}")
    return result.stdout.strip()


def git_credentials():
    auth = base64.b64encode(f"x-access-token:{os.environ['GH_TOKEN']}".encode()).decode()
    return {**os.environ, "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
            "GIT_CONFIG_VALUE_0": f"AUTHORIZATION: basic {auth}"}


def transaction(remote, mutate, message, env=None):
    """Retry against the latest branch after conflicts; never force-push."""
    for attempt in range(5):
        with tempfile.TemporaryDirectory(prefix="bench-ledger-") as td:
            root = Path(td)
            command(["git", "init", "-q"], root)
            command(["git", "remote", "add", "origin", remote], root)
            if command(["git", "ls-remote", "origin", "refs/heads/bench-data"], root, env):
                command(["git", "fetch", "-q", "origin", "refs/heads/bench-data"], root, env)
                command(["git", "checkout", "-q", "-B", "bench-data", "FETCH_HEAD"], root)
            else:
                command(["git", "checkout", "-q", "--orphan", "bench-data"], root)
            result = mutate(root)
            command(["git", "add", "."], root)
            if not command(["git", "diff", "--cached", "--name-only"], root):
                return result
            command(["git", "-c", "user.name=github-actions[bot]", "-c", "user.email=41898282+github-actions[bot]@users.noreply.github.com",
                     "-c", "commit.gpgsign=false", "commit", "-qm", message], root)
            pushed = subprocess.run(["git", "push", "origin", "HEAD:refs/heads/bench-data"], cwd=root, env=env, capture_output=True)
            if pushed.returncode == 0:
                return result
            if attempt == 4:
                raise RuntimeError("request transaction failed after five fresh attempts")
