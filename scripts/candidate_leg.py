#!/usr/bin/env python3
"""Execute one candidate leg inside its disposable, unprivileged container."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import subprocess
import sys

# SDK build scripts cannot write this image-owned code or its import directory.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from bench_support import (build_harnesses, check_toolchains, checksum, prepare_sources,
                           setup_environment, stage_native_sdk, validate_build_dependencies,
                           validate_compatibility, validate_source)
from candidate_contract import validate_lock
from central_inputs import environment
from comparison import validate_measured_samples
from contract_support import require
from export_results import json_bytes
from suite_registry import VALIDATORS, load_suite

ROOT = Path('/work/repo')
OUTPUT = Path('/results')
STATE = Path('/work/state.json')


def save(state):
    STATE.write_bytes(json_bytes(state))
    (OUTPUT / 'metadata.json').write_bytes(json_bytes(state['metadata']))


def run(state, label, argv, cwd=ROOT):
    metadata = state['metadata']
    metadata['failure_phase'] = label
    metadata['commands'].append({'label': label, 'argv': list(map(str, argv)), 'cwd': str(cwd)})
    save(state)
    with (OUTPUT / (label + '.stdout.log')).open('w') as out, (OUTPUT / (label + '.stderr.log')).open('w') as err:
        subprocess.run(argv, cwd=cwd, env=state['env'], stdout=out, stderr=err, check=True)
    metadata.pop('failure_phase', None)


def unchanged(state):
    for name, sha in state['trusted_files'].items():
        require(checksum(ROOT / name) == sha, 'trusted harness changed inside candidate container')
    for name, selected in state['lock']['sources'].items():
        validate_source(ROOT / '_sources' / name, selected['sha'])


def prepare(leg):
    selected = json.loads(Path('/input/selection.json').read_text())
    lock = selected['locks'][leg]
    validate_lock(lock)
    shutil.copytree('/opt/harness', ROOT)
    Path('/work/home').mkdir()
    Path('/work/tmp').mkdir()
    (ROOT / 'candidate.lock.json').write_bytes(json_bytes(lock))
    env = setup_environment(lock)
    go, go_version, rust_version = check_toolchains(lock, 'go', env)
    suites = [load_suite(ROOT, lock, name) for name in sorted(VALIDATORS)]
    files = {name: checksum(ROOT / name) for name in selected['harness']['files_sha256']}
    require(files == selected['harness']['files_sha256'], 'container code differs from trusted build context')
    paths = ['candidate.lock.json', 'components.json']
    for suite, _, contract_path in suites:
        paths += [f"suites/{suite['id']}.json", str(contract_path.relative_to(ROOT))]
    hashes = {name: checksum(ROOT / name) for name in paths}
    candidate = {**lock['candidate'], 'harness': selected['harness'], 'image_id': selected['image_id']}
    metadata = {'schema_version': 3, 'status': 'running', 'performance_baseline': False,
                'suite_ids': sorted(VALIDATORS), 'profile': 'full', 'input_files_sha256': hashes,
                'sources': lock['sources'], 'build_profile': lock['profile'], 'rust_features': lock['rust_features'],
                'rustc': rust_version, 'go': go_version, 'environment': environment(),
                'started_at_utc': datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'), 'commands': [],
                'harness_sha': selected['harness']['sha'], 'harness_dirty': selected['harness']['dirty'],
                'harness_files_sha256': files, 'candidate': candidate}
    metadata['platform'] = metadata['environment']['os']
    inputs = {'schema_version': 2, 'suites': [{'suite': s, 'contract': c} for s, c, _ in suites],
              'sources': lock['sources'], 'toolchains': lock['toolchains'], 'profile': 'full',
              'build_profile': lock['profile'], 'rust_features': lock['rust_features'],
              'input_files_sha256': hashes, 'components': json.loads((ROOT / 'components.json').read_text()),
              'candidate': candidate}
    state = {'lock': lock, 'metadata': metadata, 'inputs': inputs, 'trusted_files': files, 'env': env}
    save(state)
    call = lambda label, argv, cwd=ROOT: run(state, label, argv, cwd)
    prepare_sources(ROOT, lock, None, call)
    # stage_native_sdk updates the cgo flags used by subsequent commands.
    metadata['native_sdk'] = stage_native_sdk(ROOT, env, call)
    validate_compatibility(ROOT, go, call)
    binaries = build_harnesses(ROOT, suites, OUTPUT, env, go, call)
    state['binaries'] = {'/'.join(key): str(path) for key, path in binaries.items()}
    state['env'] = env
    inputs['build_dependencies'] = metadata['build_dependencies'] = json.loads((OUTPUT / 'build-dependencies.json').read_text())
    (OUTPUT / 'run-inputs.json').write_bytes(json_bytes(inputs))
    metadata['run_inputs_sha256'] = checksum(OUTPUT / 'run-inputs.json')
    unchanged(state)
    save(state)


def sample(state, suite_id, scenario_id, impl, profile):
    unchanged(state)
    suite, contract, path = load_suite(ROOT, state['lock'], suite_id)
    scenario = next(item for item in contract['scenarios'] if item['id'] == scenario_id)
    adapter = suite['implementations'][impl]
    binary = state['binaries']['/'.join((impl, adapter['harness'], adapter['executable']))]
    result = subprocess.run([binary, str(path), profile, scenario_id], env=state['env'], text=True, capture_output=True, check=True)
    require(len(result.stdout) <= 1024 * 1024, 'candidate sample exceeds budget')
    value = json.loads(result.stdout)
    VALIDATORS[suite_id].validate_sample(value, contract, scenario, impl, profile)
    print(json.dumps(value))


def finalize(state):
    unchanged(state)
    validate_build_dependencies(ROOT, state['metadata']['build_dependencies'])
    for name, sha in state['metadata']['input_files_sha256'].items():
        require(checksum(ROOT / name) == sha, 'candidate inputs changed')
    samples = json.loads((OUTPUT / 'samples.json').read_text())
    suites = [load_suite(ROOT, state['lock'], name) for name in sorted(VALIDATORS)]
    validate_measured_samples(samples, suites, expected_rounds=10, expected_profile='full')
    state['metadata'].update(status='passed', validated_samples=len(samples), performance_baseline=False,
                             completed_at_utc=datetime.now(timezone.utc).isoformat())
    save(state)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['prepare', 'sample', 'finalize'])
    parser.add_argument('--leg', choices=['base', 'head'])
    parser.add_argument('--suite')
    parser.add_argument('--scenario')
    parser.add_argument('--implementation', choices=['rust', 'go'])
    parser.add_argument('--profile', choices=['full', 'smoke'], default='full')
    args = parser.parse_args()
    try:
        if args.operation == 'prepare':
            prepare(args.leg)
        elif args.operation == 'sample':
            sample(json.loads(STATE.read_text()), args.suite, args.scenario, args.implementation, args.profile)
        else:
            finalize(json.loads(STATE.read_text()))
    except Exception as error:
        if STATE.exists():
            state = json.loads(STATE.read_text())
            state['metadata'].update(status='failed', performance_baseline=False, error=str(error),
                                     completed_at_utc=datetime.now(timezone.utc).isoformat())
            save(state)
        raise


if __name__ == '__main__':
    main()
