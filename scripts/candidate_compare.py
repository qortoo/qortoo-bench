#!/usr/bin/env python3
"""Compare PR base/head in isolated containers, interleaved on one host."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import uuid

from bench_support import checksum, git
from candidate_contract import validate_lock
from comparison import build_comparison_summary, generate_markdown_report
from contract_support import require
from export_results import export_run, json_bytes
from suite_registry import VALIDATORS, load_suite

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_PATHS = ('scripts', 'harnesses', 'contracts', 'suites', 'docker', 'components.json', 'sources.lock.json')


def read_json(path):
    require(path.is_file() and not path.is_symlink() and path.stat().st_size <= 25 * 1024 * 1024, 'unsafe/oversized candidate JSON')
    return json.loads(path.read_text())


def runtime_files(root):
    files = []
    for name in RUNTIME_PATHS:
        path = root / name
        files.extend(p for p in path.rglob('*') if p.is_file() and '__pycache__' not in p.parts and 'target' not in p.parts) if path.is_dir() else files.append(path)
    return {p.relative_to(root).as_posix(): checksum(p) for p in files if not p.is_symlink()}


def schedule(resolved, rounds=10):
    for r in range(-1, rounds):
        legs = ('base', 'head') if r % 2 == 0 else ('head', 'base')
        implementations = ('rust', 'go') if r % 2 == 0 else ('go', 'rust')
        for suite, contract, _ in resolved:
            for scenario in contract['scenarios']:
                for implementation in implementations:
                    for leg in legs:
                        yield r, leg, suite['id'], scenario['id'], implementation


def docker_command(name, image, selected):
    # Forward only public runner identity, never the host environment or credentials.
    identity_env = []
    for key in ('BENCH_RUNNER_PROVIDER', 'BENCH_RUNNER_IMAGE'):
        if key in os.environ:
            identity_env.extend(['--env', f'{key}={os.environ[key]}'])
    return ['docker', 'run', '-d', '--name', name, *identity_env, '--read-only', '--user', '1000:1000',
            '--cap-drop=ALL', '--security-opt=no-new-privileges', '--pids-limit=512', '--memory=4g', '--cpus=2',
            '--mount', 'type=volume,dst=/work', '--mount', 'type=volume,dst=/results',
            '--tmpfs', '/tmp:rw,noexec,size=512m', '--mount', f'type=bind,src={selected},dst=/input/selection.json,readonly', image]


def compare(base, head):
    require(base['candidate']['leg'] == 'base' and head['candidate']['leg'] == 'head', 'comparison legs swapped')
    for key in ('environment', 'harness_sha', 'harness_dirty'):
        require(base[key] == head[key], f'candidate comparison conditions differ: {key}')
    for key in ('component', 'pr_number', 'base', 'head', 'dependency', 'source_run_url', 'image_id', 'harness'):
        require(base['candidate'][key] == head['candidate'][key], f'candidate context differs: {key}')


def comparison_report(summaries):
    base, head = summaries['base'], summaries['head']
    compare(base, head)
    rows = []
    require(len(base['suites']) == len(head['suites']), 'candidate suites differ')
    for old, new in zip(base['suites'], head['suites']):
        require(old['contract_id'] == new['contract_id'], 'candidate contracts differ')
        require(old['series_definition']['contract_sha256'] == new['series_definition']['contract_sha256'], 'candidate contract hash differs')
        for key in ('protocol', 'profile', 'build_profile', 'rust_features', 'toolchains', 'rustc', 'go'):
            require(old['series_definition'][key] == new['series_definition'][key], f'candidate suite conditions differ: {key}')
        require(len(old['scenarios']) == len(new['scenarios']), 'candidate case counts differ')
        for a, b in zip(old['scenarios'], new['scenarios']):
            require(a['scenario_id'] == b['scenario_id'], 'candidate cases differ')
            for impl in ('rust', 'go'):
                value = {'suite': old['suite_id'], 'scenario': a['scenario_id'], 'implementation': impl,
                         'base_median_ns': a[impl]['median'], 'head_median_ns': b[impl]['median'],
                         'base_stddev_ns': a[impl]['stddev'], 'head_stddev_ns': b[impl]['stddev']}
                value['change_percent'] = 100 * (value['head_median_ns'] / value['base_median_ns'] - 1)
                rows.append(value)
    return {'schema_version': 1, 'channel': 'candidate', 'context': base['candidate'], 'comparisons': rows,
            'interpretation': 'Positive change means slower. Informational; no performance threshold or significance claim.'}


def markdown(report):
    context = report['context']
    lines = ['# PR candidate comparison', '', f"PR: {context['pr_url']}",
             f"Base: `{context['base']['sha']}`; head: `{context['head']['sha']}`", '',
             'Base/head run in separate disposable containers on one host, alternating leg order each round.',
             'Positive change means slower. Dispersion is the standard deviation across measured round means.',
             'Candidate artifacts are untrusted review evidence and never update official history or the website.', '']
    if context['go_pin_changed']:
        lines += ['Go PR changes its declared Rust pin; both legs use the base pin. Compatibility failures remain failures.', '']
    lines += ['| Suite / case | SDK | Base ns/op | Head ns/op | Change | Base stddev | Head stddev |',
              '| --- | --- | ---: | ---: | ---: | ---: | ---: |']
    for row in report['comparisons']:
        lines.append(f"| {row['suite']} / {row['scenario']} | {row['implementation']} | {row['base_median_ns']:.2f} | {row['head_median_ns']:.2f} | {row['change_percent']:+.2f}% | {row['base_stddev_ns']:.2f} | {row['head_stddev_ns']:.2f} |")
    return '\n'.join(lines) + '\n'


def execute(selection, output, smoke=False):
    require(selection['status'] == 'selected', 'candidate selection is not active')
    require(not output.exists(), 'candidate output must be new')
    output.mkdir(parents=True)
    containers = {}
    image = None
    log_number = 0
    def command(argv, timeout=900):
        nonlocal log_number
        log_number += 1
        prefix = output / f'command-{log_number:04d}'
        with prefix.with_suffix('.stdout.log').open('w') as out, prefix.with_suffix('.stderr.log').open('w') as err:
            subprocess.run(list(map(str, argv)), stdout=out, stderr=err, check=True, timeout=timeout)
        require(prefix.with_suffix('.stdout.log').stat().st_size <= 25 * 1024 * 1024, 'command output exceeds budget')
        return prefix.with_suffix('.stdout.log').read_text().strip()
    selected = output / 'selection.json'
    selected.write_bytes(json_bytes(selection))
    status = {'status': 'running', 'channel': 'candidate', 'smoke': smoke, 'started_at_utc': datetime.now(timezone.utc).isoformat()}
    try:
        for leg in ('base', 'head'):
            validate_lock(selection['locks'][leg])
        require(git(ROOT, 'rev-parse', 'HEAD') == selection['harness']['sha'], 'candidate harness commit changed')
        dirty = bool(git(ROOT, 'status', '--porcelain', '--untracked-files=all'))
        require(selection['harness']['dirty'] or not dirty, 'trusted CI harness is dirty')
        selection['harness']['dirty'] = dirty
        selection['harness']['files_sha256'] = runtime_files(ROOT)
        context = output / 'image-context'
        for name in selection['harness']['files_sha256']:
            target = context / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, target)
        image = f'qortoo-candidate:{uuid.uuid4().hex}'
        print('Building the shared candidate image...', flush=True)
        command(['docker', 'build', '-t', image, '-f', context / 'docker/candidate.Dockerfile', context], timeout=1800)
        selection['image_id'] = command(['docker', 'image', 'inspect', '--format', '{{.Id}}', image])
        selected.write_bytes(json_bytes(selection))
        resolved = [load_suite(ROOT, selection['locks']['base'], suite) for suite in sorted(VALIDATORS)]
        rows = {leg: {'samples': [], 'warmup-samples': []} for leg in ('base', 'head')}
        for leg in ('base', 'head'):
            print(f'Preparing isolated {leg} SDKs...', flush=True)
            name = 'qortoo-candidate-' + uuid.uuid4().hex
            containers[leg] = name
            command(docker_command(name, image, selected.resolve()))
            command(['docker', 'exec', name, 'python3', '/opt/harness/scripts/candidate_leg.py', 'prepare', '--leg', leg], timeout=1800)
            command(['docker', 'network', 'disconnect', 'bridge', name])
            command(['docker', 'pause', name])
        order = []
        previous_round = None
        for r, leg, suite, scenario, impl in schedule(resolved, rounds=0 if smoke else 10):
            if previous_round != r:
                print('Running discarded warmup...' if r == -1 else f'Running paired round {r + 1}/10...', flush=True)
                previous_round = r
            name = containers[leg]
            command(['docker', 'unpause', name])
            value = json.loads(command(['docker', 'exec', name, 'python3', '/opt/harness/scripts/candidate_leg.py', 'sample',
                                        '--suite', suite, '--scenario', scenario, '--implementation', impl,
                                        '--profile', 'smoke' if smoke else 'full'], timeout=300))
            command(['docker', 'pause', name])
            value['warmup'] = r == -1
            if r >= 0:
                value['round_index'] = r
            rows[leg]['warmup-samples' if r == -1 else 'samples'].append(value)
            order.append({'round': r, 'leg': leg, 'suite': suite, 'scenario': scenario, 'implementation': impl})
        (output / 'execution-order.json').write_bytes(json_bytes(order))
        summaries = {}
        for leg, name in containers.items():
            raw = output / f'raw-{leg}'
            raw.mkdir()
            for kind, values in rows[leg].items():
                path = output / f'{leg}-{kind}.json'
                path.write_bytes(json_bytes(values))
                command(['docker', 'cp', path, f'{name}:/results/{kind}.json'])
            if not smoke:
                command(['docker', 'unpause', name])
                command(['docker', 'exec', name, 'python3', '/opt/harness/scripts/candidate_leg.py', 'finalize'])
                command(['docker', 'pause', name])
            command(['docker', 'cp', f'{name}:/results/.', raw])
            require(all(not p.is_symlink() for p in raw.rglob('*')), 'candidate artifact contains symlinks')
            metadata = read_json(raw / 'metadata.json')
            require(metadata['sources'] == selection['locks'][leg]['sources'], 'candidate source provenance changed')
            require(metadata['candidate'] == {**selection['locks'][leg]['candidate'], 'harness': selection['harness'], 'image_id': selection['image_id']}, 'candidate context changed')
            if not smoke:
                for kind, values in rows[leg].items():
                    require(read_json(raw / (kind + '.json')) == values, 'candidate samples changed after collection')
                comparison = build_comparison_summary(rows[leg]['samples'], resolved, metadata)
                (raw / 'comparison.json').write_bytes(json_bytes(comparison))
                (raw / 'comparison.md').write_text(generate_markdown_report(comparison, metadata))
                export_run(raw, output / f'bundle-{leg}', metadata['environment'], 'candidate', os.environ.get('CANDIDATE_RUN_URL'))
                summaries[leg] = read_json(output / f'bundle-{leg}/summary.json')
        if not smoke:
            report = comparison_report(summaries)
            (output / 'comparison.json').write_bytes(json_bytes(report))
            (output / 'comparison.md').write_text(markdown(report))
        require(runtime_files(ROOT) == selection['harness']['files_sha256'], 'trusted host harness changed')
        status['status'] = 'smoke-passed' if smoke else 'passed'
    except Exception as error:
        status.update(status='failed', error=str(error))
        raise
    finally:
        for leg, name in containers.items():
            # Diagnostics stay candidate-only; no write-capable job consumes them.
            destination = output / f'failure-{leg}'
            if status['status'] == 'failed':
                destination.mkdir(exist_ok=True)
                subprocess.run(['docker', 'cp', f'{name}:/results/.', str(destination)], capture_output=True)
                for path in destination.rglob('*'):
                    if path.is_symlink() or (path.is_file() and path.stat().st_size > 25 * 1024 * 1024):
                        path.unlink()
            subprocess.run(['docker', 'rm', '-f', '-v', name], capture_output=True)
        if image:
            subprocess.run(['docker', 'image', 'rm', image], capture_output=True)
        status['completed_at_utc'] = datetime.now(timezone.utc).isoformat()
        (output / 'status.json').write_bytes(json_bytes(status))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--selection', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--smoke', action='store_true', help='validate both legs without exporting performance results')
    args = parser.parse_args()
    execute(read_json(args.selection), args.output.resolve(), args.smoke)


if __name__ == '__main__':
    main()
