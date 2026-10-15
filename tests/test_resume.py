import csv
import json
import unittest
from unittest.mock import Mock, patch

import test_config_main as config_tests
from config import Settings
from main import run_experiment
from workflow_common import WorkflowResult


class ResumeTests(unittest.TestCase):
    setUp = config_tests.ExperimentTests.setUp
    def run_with(self, workflow, *, resume=False, settings=None, continue_run=False):
        with patch.dict('main.WORKFLOWS', {'single-agent': workflow}):
            return run_experiment(settings or Settings(num_of_execution=1), self.llm,
                                  self.dataset, self.output, self.validator, resume=resume,
                                  continue_run=continue_run)

    def test_resume_preserves_success_and_retries_failure(self):
        bundle = {'resourceType': 'Bundle', 'type': 'collection', 'entry': []}
        self.run_with(Mock(side_effect=[WorkflowResult(bundle, True, 1), RuntimeError()]))
        saved = (self.output / 'outputs' / '1_a.json').read_bytes()
        manifest = (self.output / 'experiment.json').read_bytes()
        log_path = self.output / 'logs' / '1_b.jsonl'
        log = log_path.read_text()
        workflow = Mock(return_value=WorkflowResult(bundle, True, 1))
        report = self.run_with(workflow, resume=True)
        self.assertEqual([c.args[0] for c in workflow.call_args_list], ['note B'])
        self.assertEqual(report['skipped'], 1)
        self.assertEqual(report['generation_counts']['completed'], 2)
        self.assertEqual((self.output / 'outputs' / '1_a.json').read_bytes(), saved)
        self.assertEqual((self.output / 'experiment.json').read_bytes(), manifest)
        self.assertTrue(log_path.read_text().startswith(log))
        self.assertEqual([json.loads(line)['sequence'] for line in log_path.read_text().splitlines()],
                         [1, 2, 3, 4])
        with (self.output / 'results.csv').open() as stream:
            self.assertEqual(len(list(csv.DictReader(stream))), 3)
        workflow.reset_mock()
        self.assertEqual(self.run_with(workflow, resume=True)['skipped'], 2)
        workflow.assert_not_called()

    def test_resume_recovers_interruption_and_missing_output(self):
        bundle = {'resourceType': 'Bundle', 'type': 'collection', 'entry': []}
        with self.assertRaises(KeyboardInterrupt):
            self.run_with(Mock(side_effect=[WorkflowResult(bundle, True, 1), KeyboardInterrupt()]))
        (self.output / 'outputs' / '1_a.json').unlink()
        workflow = Mock(return_value=WorkflowResult(bundle, True, 1))
        self.assertEqual(self.run_with(workflow, resume=True)['skipped'], 0)
        self.assertEqual(workflow.call_count, 2)

    def test_resume_rejects_changes_without_modifying_results(self):
        workflow = Mock(return_value=WorkflowResult({'resourceType': 'Bundle'}, True, 1))
        self.run_with(workflow)
        original = (self.output / 'results.csv').read_bytes()
        (self.dataset / 'a.txt').write_text('changed')
        with self.assertRaisesRegex(ValueError, 'differ'):
            self.run_with(workflow, resume=True)
        self.assertEqual((self.output / 'results.csv').read_bytes(), original)
        (self.dataset / 'a.txt').write_text('note A')
        with self.assertRaisesRegex(ValueError, 'differ'):
            self.run_with(workflow, resume=True, settings=Settings(num_of_execution=2))
        self.llm.configuration.return_value = {'model': 'another-model'}
        with self.assertRaisesRegex(ValueError, 'differ'):
            self.run_with(workflow, resume=True)
        (self.output / 'experiment.json').unlink()
        with self.assertRaisesRegex(ValueError, 'missing'):
            self.run_with(workflow, resume=True)

    def test_manifest_contains_prompts_hashes_and_no_credentials(self):
        self.run_with(Mock(return_value=WorkflowResult({'resourceType': 'Bundle'}, True, 1)),
                      settings=Settings(num_of_execution=1, openai_token='secret-key'))
        raw = (self.output / 'experiment.json').read_text()
        identity = json.loads(raw)['identity']
        self.assertNotIn('secret-key', raw)
        self.assertEqual(identity['dataset']['count'], 2)
        self.assertEqual(len(identity['dataset']['sha256']), 64)
        self.assertTrue(identity['prompts']['single']['text'].startswith('Act as '))
        self.assertEqual(identity['llm']['model'], 'fake')

    def test_resume_rejects_prompt_changes(self):
        workflow = Mock(return_value=WorkflowResult({'resourceType': 'Bundle'}, True, 1))
        self.run_with(workflow)
        from agent import single_agent
        altered = single_agent(self.llm)
        altered.system_prompt += '\nchanged prompt'
        with patch('experiment_manifest.single_agent', return_value=altered):
            with self.assertRaisesRegex(ValueError, 'differ'):
                self.run_with(workflow, resume=True)

    def test_resume_regenerates_invalid_and_corrupted_outputs(self):
        bundle = {'resourceType': 'Bundle', 'type': 'collection', 'entry': []}
        self.run_with(Mock(side_effect=[WorkflowResult(bundle, False, 3),
                                       WorkflowResult(bundle, True, 1)]))
        (self.output / 'outputs' / '1_b.json').write_text('{broken')
        workflow = Mock(return_value=WorkflowResult(bundle, True, 1))
        self.run_with(workflow, resume=True)
        self.assertEqual(workflow.call_count, 2)

    def test_resume_ignores_partial_csv_row(self):
        bundle = {'resourceType': 'Bundle', 'type': 'collection', 'entry': []}
        self.run_with(Mock(side_effect=[WorkflowResult(bundle, True, 1), RuntimeError()]))
        path = self.output / 'results.csv'
        with path.open('a') as stream:
            stream.write('partial,b.txt,1')
        workflow = Mock(return_value=WorkflowResult(bundle, True, 1))
        self.run_with(workflow, resume=True)
        self.assertEqual([c.args[0] for c in workflow.call_args_list], ['note B'])
        workflow.reset_mock()
        self.assertEqual(self.run_with(workflow, resume=True)['skipped'], 2)
        workflow.assert_not_called()

    def test_continue_appends_one_run_per_invocation(self):
        bundle = {'resourceType': 'Bundle', 'type': 'collection', 'entry': []}
        workflow = Mock(return_value=WorkflowResult(bundle, True, 1))
        for run in range(1, 4):
            self.run_with(workflow, continue_run=True)
            self.assertEqual(workflow.call_count, 2 * run)
        with (self.output / 'results.csv').open() as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual([r['run-id'] for r in rows], ['1', '1', '2', '2', '3', '3'])
        self.assertEqual(json.loads((self.output / 'experiment.json').read_text())[
            'identity']['num_of_execution'], 3)
        self.assertEqual(len(list((self.output / 'outputs').glob('*.json'))), 6)

    def test_continue_starts_new_run_and_preserves_technical_failure(self):
        bundle = {'resourceType': 'Bundle', 'type': 'collection', 'entry': []}
        self.run_with(Mock(side_effect=[WorkflowResult(bundle, True, 1), RuntimeError()]),
                      continue_run=True)
        csv_before = (self.output / 'results.csv').read_bytes()
        log_before = (self.output / 'logs' / '1_b.jsonl').read_bytes()
        workflow = Mock(return_value=WorkflowResult(bundle, True, 1))
        self.run_with(workflow, continue_run=True)
        self.assertEqual([c.args[0] for c in workflow.call_args_list], ['note A', 'note B'])
        self.assertTrue((self.output / 'outputs' / '2_a.json').exists())
        self.assertTrue((self.output / 'results.csv').read_bytes().startswith(csv_before))
        self.assertEqual((self.output / 'logs' / '1_b.jsonl').read_bytes(), log_before)
        with (self.output / 'results.csv').open() as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual([(r['run-id'], r['fhir-valid']) for r in rows],
                         [('1', '1'), ('1', '0'), ('2', '1'), ('2', '1')])

    def test_continue_keeps_invalid_output_as_experimental_outcome(self):
        bundle = {'resourceType': 'Bundle', 'type': 'collection', 'entry': []}
        with self.assertRaises(KeyboardInterrupt):
            self.run_with(Mock(side_effect=[WorkflowResult(bundle, False, 3), KeyboardInterrupt()]),
                          continue_run=True)
        workflow = Mock(return_value=WorkflowResult(bundle, True, 1))
        report = self.run_with(workflow, continue_run=True)
        self.assertEqual([c.args[0] for c in workflow.call_args_list], ['note A', 'note B'])
        self.assertEqual(report['generation_counts']['invalid_output'], 0)
        workflow.reset_mock()
        self.run_with(workflow, continue_run=True)
        self.assertEqual(workflow.call_count, 2)
        self.assertTrue((self.output / 'outputs' / '3_a.json').exists())

    def test_continue_advances_even_without_a_completed_csv_row(self):
        with self.assertRaises(KeyboardInterrupt):
            self.run_with(Mock(side_effect=KeyboardInterrupt()), continue_run=True)
        workflow = Mock(return_value=WorkflowResult(
            {'resourceType': 'Bundle', 'type': 'collection', 'entry': []}, True, 1))
        self.run_with(workflow, continue_run=True)
        with (self.output / 'results.csv').open() as stream:
            self.assertEqual([r['run-id'] for r in csv.DictReader(stream)], ['2', '2'])

    def test_continue_rejects_mismatches_and_legacy_without_overwrite(self):
        bundle = {'resourceType': 'Bundle', 'type': 'collection', 'entry': []}
        workflow = Mock(return_value=WorkflowResult(bundle, True, 1))
        self.run_with(workflow, continue_run=True)
        original = (self.output / 'results.csv').read_bytes()
        self.llm.configuration.return_value = {'model': 'changed'}
        with self.assertRaisesRegex(ValueError, 'differ'):
            self.run_with(workflow, continue_run=True)
        self.assertEqual((self.output / 'results.csv').read_bytes(), original)
        (self.output / 'experiment.json').unlink()
        with self.assertRaisesRegex(ValueError, 'without experiment.json'):
            self.run_with(workflow, continue_run=True)
