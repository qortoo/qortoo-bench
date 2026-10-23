import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

from candidate_compare import docker_command
from central_inputs import environment


class RunnerEnvironmentTests(unittest.TestCase):
    def capture(self, variables):
        with patch.dict(os.environ, variables, clear=True), \
                patch('central_inputs.cpu_identity', return_value='fixture CPU'), \
                patch('central_inputs.platform.platform', return_value='fixture OS'), \
                patch('central_inputs.platform.machine', return_value='x86_64'), \
                patch('central_inputs.os.cpu_count', return_value=4):
            return environment()

    def test_provider_separates_series_without_changing_observed_hardware(self):
        github = self.capture({'ImageOS': 'ubuntu24'})
        ubicloud = self.capture({'BENCH_RUNNER_PROVIDER': 'ubicloud', 'BENCH_RUNNER_IMAGE': 'ubuntu24'})
        self.assertEqual(github['id'], 'github-hosted-ubuntu24')
        self.assertEqual(github['runner'], 'GitHub hosted ubuntu24; 4 logical CPUs')
        self.assertEqual(ubicloud['id'], 'ubicloud-ubuntu24')
        self.assertEqual(ubicloud['runner'], 'Ubicloud ubuntu24; 4 logical CPUs')
        for key in ('cpu', 'os', 'arch'):
            self.assertEqual(github[key], ubicloud[key])

    def test_candidate_forwards_only_public_identity_and_preserves_it_in_container(self):
        host = {'BENCH_RUNNER_PROVIDER': 'ubicloud', 'BENCH_RUNNER_IMAGE': 'ubuntu24',
                'GH_TOKEN': 'fixture-secret', 'GITHUB_TOKEN': 'fixture-secret', 'ImageOS': 'host-image'}
        with patch.dict(os.environ, host, clear=True):
            argv = docker_command('fixture', 'sha256:fixture', '/public/selection.json')
        forwarded = [argv[i + 1] for i, value in enumerate(argv) if value == '--env']
        self.assertEqual(set(forwarded), {'BENCH_RUNNER_PROVIDER=ubicloud', 'BENCH_RUNNER_IMAGE=ubuntu24'})
        self.assertNotIn('fixture-secret', ' '.join(argv))
        container = dict(value.split('=', 1) for value in forwarded)
        self.assertEqual(self.capture(host), self.capture(container))

    def test_unknown_provider_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'unsupported runner provider'):
            self.capture({'BENCH_RUNNER_PROVIDER': 'unknown'})
