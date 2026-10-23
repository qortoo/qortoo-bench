import base64
from copy import deepcopy
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

from candidate_compare import comparison_report, docker_command, schedule
from candidate_contract import validate_lock
from central_inputs import cpu_identity, resolve
from export_results import digest, export_run, json_bytes
from pr_inputs import select
from publish_results import verify_bundle
from suite_registry import VALIDATORS, load_suite
import test_export_results as export_tests


class CandidateTests(unittest.TestCase):
    def setUp(self):
        self.lock = json.loads((ROOT / 'sources.lock.json').read_text())
        self.component = 'rust'
        self.slug = 'qortoo/qortoo-rs'
        self.head_slug = 'alice/qortoo-rs'
        self.base_sha, self.head_sha = 'a' * 40, 'b' * 40
        self.base_pin, self.head_pin = 'c' * 40, 'd' * 40
        self.origin = {'repository': {'full_name': self.slug}, 'event': 'pull_request',
                       'path': '.github/workflows/benchmark-pr.yml', 'head_sha': self.head_sha, 'conclusion': 'success'}
        self.pr = {'state': 'open', 'html_url': f'https://github.com/{self.slug}/pull/4',
                   'base': {'repo': {'full_name': self.slug}, 'ref': 'main', 'sha': self.base_sha},
                   'head': {'sha': self.head_sha, 'repo': {'full_name': self.head_slug, 'private': False}}}
        self.network = self.slug

    def api(self, endpoint):
        if '/actions/runs/' in endpoint:
            return deepcopy(self.origin)
        if '/pulls/' in endpoint:
            return deepcopy(self.pr)
        if endpoint == f'repos/{self.head_slug}':
            return {'fork': True, 'source': {'full_name': self.network}}
        if endpoint.endswith('/commits/main'):
            return {'sha': 'e' * 40}
        if '/contents/' in endpoint:
            pin = self.head_pin if endpoint.endswith('ref=' + self.head_sha) else self.base_pin
            return {'content': base64.b64encode(f'  QORTOO_RS_REF: {pin}\n'.encode()).decode()}
        raise AssertionError(endpoint)

    def selection(self):
        return select(self.lock, self.component, '4', self.head_sha, '9', self.api)

    def test_fork_rust_event_freezes_shared_go_and_exact_sdk_revisions(self):
        result = self.selection()
        base, head = result['locks']['base'], result['locks']['head']
        self.assertEqual(base['sources']['go'], head['sources']['go'])
        self.assertEqual(base['sources']['rust']['sha'], self.base_sha)
        self.assertEqual(head['sources']['rust']['repository'], 'https://github.com/alice/qortoo-rs.git')
        self.assertTrue(result['context']['fork'])
        validate_lock(head)
        with self.assertRaisesRegex(ValueError, 'not allowed'):
            resolve(head)

    def test_go_pr_uses_one_base_rust_pin_even_when_head_pin_changes(self):
        self.component, self.slug, self.head_slug = 'go', 'qortoo/qortoo-go', 'qortoo/qortoo-go'
        self.origin['repository']['full_name'] = self.slug
        self.pr['base']['repo']['full_name'] = self.slug
        self.pr['head']['repo']['full_name'] = self.head_slug
        result = self.selection()
        self.assertTrue(result['context']['go_pin_changed'])
        self.assertFalse(result['context']['fork'])
        self.assertEqual(result['locks']['base']['sources']['rust']['sha'], self.base_pin)
        self.assertEqual(result['locks']['head']['sources']['rust']['sha'], self.base_pin)
        self.assertEqual(result['locks']['head']['candidate']['go_ci_rust_sha'], self.head_pin)

    def test_source_run_authentication_and_fork_network_are_enforced(self):
        for field, value in [('event', 'push'), ('head_sha', 'f' * 40), ('conclusion', 'failure'), ('path', '.github/workflows/ci.yml')]:
            saved = deepcopy(self.origin)
            self.origin[field] = value
            with self.assertRaisesRegex(ValueError, 'not the claimed'):
                self.selection()
            self.origin = saved
        self.network = 'unrelated/repository'
        with self.assertRaisesRegex(ValueError, 'fork network'):
            self.selection()
        with self.assertRaises(ValueError):
            select(self.lock, 'rust', '4; echo bad', self.head_sha, '9', self.api)

    def test_old_head_or_closed_pr_is_superseded_without_replacing_sha(self):
        self.pr['head']['sha'] = 'f' * 40
        self.assertEqual(self.selection()['status'], 'superseded')
        self.pr['head']['sha'] = self.head_sha
        self.pr['state'] = 'closed'
        self.assertEqual(self.selection()['status'], 'superseded')

    def test_candidate_export_cannot_enter_official_or_exploratory_channel(self):
        f = export_tests.ExportTests()
        f.setUp()
        self.addCleanup(f.doCleanups)
        lock = self.selection()['locks']['head']
        f.inputs['candidate'] = f.metadata['candidate'] = lock['candidate']
        f.inputs['sources'] = f.metadata['sources'] = lock['sources']
        f.write('run-inputs.json', f.inputs)
        f.metadata['run_inputs_sha256'] = digest((f.run / 'run-inputs.json').read_bytes())
        f.refresh_metadata()
        for channel in ('official', 'exploratory'):
            with self.assertRaisesRegex(ValueError, 'cannot be promoted'):
                export_run(f.run, f.root / channel, f.environment, channel)
        manifest = export_run(f.run, f.output, f.environment, 'candidate')
        self.assertEqual(manifest['candidate'], lock['candidate'])
        self.assertFalse(manifest['performance_baseline'])
        self.assertTrue(manifest['groups'][0]['name'].startswith('candidate/'))
        self.assertFalse(json.loads((f.output / 'index-entry.json').read_text())['eligible_for_latest'])
        with self.assertRaisesRegex(ValueError, 'integration/channel'):
            verify_bundle(f.output, {})

    def test_comparison_reports_head_change_and_rejects_different_conditions(self):
        f = export_tests.ExportTests()
        f.setUp()
        self.addCleanup(f.doCleanups)
        lock = self.selection()['locks']['base']
        context = {**lock['candidate'], 'harness': {'sha': 'f' * 40}, 'image_id': 'sha256:fixture'}
        f.metadata['candidate'] = f.inputs['candidate'] = context
        f.metadata['sources'] = f.inputs['sources'] = lock['sources']
        f.write('run-inputs.json', f.inputs)
        f.metadata['run_inputs_sha256'] = digest((f.run / 'run-inputs.json').read_bytes())
        f.refresh_metadata()
        export_run(f.run, f.output, f.environment, 'candidate')
        base = json.loads((f.output / 'summary.json').read_text())
        head = deepcopy(base)
        head['candidate']['leg'] = 'head'
        for scenario in head['suites'][0]['scenarios']:
            for impl in ('rust', 'go'):
                scenario[impl]['median'] *= 1.1
        report = comparison_report({'base': base, 'head': head})
        self.assertAlmostEqual(report['comparisons'][0]['change_percent'], 10)
        head['environment']['cpu'] = 'another-host'
        with self.assertRaisesRegex(ValueError, 'conditions differ'):
            comparison_report({'base': base, 'head': head})

    def test_rounds_pair_base_head_and_reverse_order_without_changing_budgets(self):
        suites = [load_suite(ROOT, self.lock, name) for name in sorted(VALIDATORS)]
        rows = list(schedule(suites))
        self.assertEqual(len(rows), 2 * (340 + 34))
        for leg in ('base', 'head'):
            self.assertEqual(sum(row[0] == -1 and row[1] == leg for row in rows), 34)
            self.assertEqual(sum(row[0] >= 0 and row[1] == leg for row in rows), 340)
        for r in range(10):
            selected = [row for row in rows if row[0] == r]
            self.assertEqual([row[1] for row in selected[:2]], ['base', 'head'] if r % 2 == 0 else ['head', 'base'])
            self.assertEqual(selected[0][2:], selected[1][2:])

    @patch.dict(os.environ, {}, clear=True)
    def test_candidate_containers_have_no_host_writable_or_credential_mount(self):
        argv = docker_command('fixture', 'sha256:fixture', '/public/selection.json')
        self.assertIn('--read-only', argv)
        self.assertIn('--cap-drop=ALL', argv)
        self.assertIn('--security-opt=no-new-privileges', argv)
        self.assertIn('1000:1000', argv)
        mounts = [argv[index + 1] for index, item in enumerate(argv) if item == '--mount']
        self.assertEqual(sum('type=bind' in value for value in mounts), 1)
        self.assertTrue(next(value for value in mounts if 'type=bind' in value).endswith(',readonly'))
        self.assertNotIn('/var/run/docker.sock', ' '.join(argv))
        self.assertNotIn('--env', argv)

    def test_cpu_identity_preserves_x86_names_and_identifies_arm_containers(self):
        self.assertEqual(cpu_identity('model name : AMD EPYC\n'), 'AMD EPYC')
        self.assertEqual(cpu_identity('CPU implementer : 0x61\nCPU part : 0x022\n'), 'CPU implementer=0x61; CPU part=0x022')
        with self.assertRaisesRegex(ValueError, 'unavailable'):
            cpu_identity('processor : 0\n')


if __name__ == '__main__':
    unittest.main()
