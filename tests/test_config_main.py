"""Offline coverage for configuration precedence and repeated experiments."""
import json
import csv
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from config import Settings, load_settings
from main import run_experiment
from workflow_common import WorkflowResult
from fhir_validator import ValidatorError


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env = Path(self.temp.name) / '.env'
        self.env.write_text('OPENAI_API_KEY=file-key\nOPENROUTER_API_KEY=router-key\n'
                            'NUM_OF_EXECUTION=5\nMODE=multiagent\n')

    def test_file_credentials_mode_and_repetitions(self):
        with patch.dict(os.environ, {}, clear=True):
            settings = load_settings(self.env)
        self.assertEqual(settings.api_key('openai'), 'file-key')
        self.assertEqual(settings.api_key('openrouter'), 'router-key')
        self.assertEqual(settings.mode, 'multiagent')
        self.assertEqual(settings.num_of_execution, 5)
        self.assertNotIn('file-key', repr(settings))
        self.assertNotIn('router-key', repr(settings))

    def test_process_environment_overrides_file_without_mutation(self):
        with patch.dict(os.environ, {'NUM_OF_EXECUTION': '2', 'MODE': 'single-agent',
                                     'OPENAI_API_KEY': 'process-key'}, clear=True):
            before = dict(os.environ)
            settings = load_settings(self.env)
            self.assertEqual(dict(os.environ), before)
        self.assertEqual(settings.num_of_execution, 2)
        self.assertEqual(settings.mode, 'single-agent')
        self.assertEqual(settings.api_key('openai'), 'process-key')

    def test_invalid_configuration_fails_before_calls(self):
        for entry in ['NUM_OF_EXECUTION=0', 'NUM_OF_EXECUTION=-1',
                      'NUM_OF_EXECUTION=1.5', 'NUM_OF_EXECUTION=abc', 'MODE=both',
                      'MAX_ATTEMPTS=0', 'MAX_ATTEMPTS=invalid', 'DEVELOPMENT=yes']:
            with self.subTest(entry=entry):
                self.env.write_text(entry)
                with patch.dict(os.environ, {}, clear=True):
                    with self.assertRaises(ValueError):
                        load_settings(self.env)

    def test_development_flag_and_environment_precedence(self):
        self.env.write_text('DEVELOPMENT=true\n')
        with patch.dict(os.environ, {}, clear=True):
            self.assertTrue(load_settings(self.env).development)
        with patch.dict(os.environ, {'DEVELOPMENT': 'false'}, clear=True):
            self.assertFalse(load_settings(self.env).development)

    def test_missing_provider_key_never_falls_back(self):
        with self.assertRaisesRegex(ValueError, 'OPENROUTER_API_KEY'):
            Settings(openai_token='key').api_key('openrouter')

    def test_explicit_missing_env_is_reported(self):
        with self.assertRaises(ValueError):
            load_settings(Path(self.temp.name) / 'missing')


class ExperimentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.dataset = self.root / 'dataset'
        self.dataset.mkdir()
        (self.dataset / 'a.txt').write_text('note A')
        (self.dataset / 'b.txt').write_text('note B')
        self.output = self.root / 'output'
        self.validator = Mock()
        self.llm = Mock()
        self.llm.configuration.return_value = {'model': 'fake'}

    def test_repeats_entire_dataset_and_preserves_failed_attempts(self):
        workflow = Mock(side_effect=[WorkflowResult({'resourceType': 'Bundle'}, True, 1), RuntimeError('secret'),
                                     WorkflowResult({'resourceType': 'Patient'}, False, 3), WorkflowResult({'resourceType': 'Bundle'}, True, 1)])
        settings = Settings(num_of_execution=2, mode='multiagent')
        with patch.dict('main.WORKFLOWS', {'multiagent': workflow}):
            report = run_experiment(settings, self.llm, self.dataset, self.output, self.validator)
        self.assertEqual(workflow.call_count, 4)
        self.assertEqual([c.args[0] for c in workflow.call_args_list],
                         ['note A', 'note B', 'note A', 'note B'])
        self.assertEqual(report['generation_counts'],
                         {'completed': 2, 'invalid_output': 1, 'failed': 1})
        with (self.output / 'results.csv').open(newline='') as stream:
            reader = csv.DictReader(stream)
            rows = list(reader)
            self.assertEqual(reader.fieldnames,
                             ['timestamp', 'file', 'run-id', 'fhir-valid', 'attempts',
                              'single-attempts', 'patient-attempts', 'condition-attempts',
                              'observation-attempts', 'medication-attempts', 'merge-attempts']
                             + __import__('token_usage').usage_fields(
                                 ('single', 'patient', 'condition', 'observation', 'medication', 'merge')))
        self.assertEqual([r['run-id'] for r in rows], ['1', '1', '2', '2'])
        self.assertEqual([r['file'] for r in rows], ['a.txt', 'b.txt', 'a.txt', 'b.txt'])
        self.assertEqual([r['fhir-valid'] for r in rows], ['1', '0', '0', '1'])
        self.assertEqual([r['attempts'] for r in rows], ['1', '', '3', '1'])
        self.assertEqual({p.name for p in self.output.iterdir()}, {'outputs', 'results.csv', 'logs', 'experiment.json'})
        self.assertEqual({p.name for p in (self.output / 'outputs').iterdir()},
                         {'1_a.json', '2_a.json', '2_b.json'})

    def test_single_mode_dispatches_five_runs_by_default(self):
        single, multi = Mock(return_value=WorkflowResult({'resourceType': 'Bundle'}, True, 1)), Mock()
        with patch.dict('main.WORKFLOWS', {'single-agent': single, 'multiagent': multi}):
            run_experiment(Settings(), self.llm, self.dataset / 'a.txt', self.output, self.validator)
        self.assertEqual(single.call_count, 5)
        multi.assert_not_called()

    def test_existing_experiment_replaced_without_stale_executions(self):
        stale = self.output / 'execution-005' / 'a'
        stale.mkdir(parents=True)
        (stale / 'bundle.json').write_text('{}')
        (self.output / 'notes.md').write_text('Keep this file')
        workflow = Mock(return_value=WorkflowResult({'resourceType': 'Bundle'}, True, 1))
        with patch.dict('main.WORKFLOWS', {'single-agent': workflow}):
            run_experiment(Settings(num_of_execution=1), self.llm, self.dataset, self.output, self.validator)
        self.assertEqual(workflow.call_count, 2)
        self.assertFalse(stale.exists())
        self.assertTrue((self.output / 'notes.md').exists())

    def test_failed_rerun_removes_stale_json(self):
        with patch.dict('main.WORKFLOWS', {
                'single-agent': Mock(return_value=WorkflowResult({'resourceType': 'Bundle'}, True, 1))}):
            run_experiment(Settings(num_of_execution=1), self.llm, self.dataset, self.output, self.validator)
        with patch.dict('main.WORKFLOWS', {'single-agent': Mock(side_effect=RuntimeError)}):
            run_experiment(Settings(num_of_execution=1), self.llm, self.dataset, self.output, self.validator)
        self.assertEqual(list((self.output / 'outputs').iterdir()), [])
        with (self.output / 'results.csv').open() as stream:
            self.assertEqual(len(list(csv.DictReader(stream))), 2)

    def test_validator_failure_writes_unknown_and_aborts(self):
        workflow = Mock(side_effect=ValidatorError("Unavailable"))
        with patch.dict('main.WORKFLOWS', {'single-agent': workflow}):
            with self.assertRaises(ValidatorError):
                run_experiment(Settings(), self.llm, self.dataset, self.output, self.validator)
        self.assertEqual(workflow.call_count, 1)
        with (self.output / 'results.csv').open() as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['fhir-valid'], '')

    def test_csv_preserves_different_agent_attempt_counts(self):
        counts = {'patient': 3, 'condition': 1, 'observation': 2, 'medication': 1, 'merge': 2}
        workflow = Mock(return_value=WorkflowResult({'resourceType': 'Bundle'}, True, 5, counts))
        with patch.dict('main.WORKFLOWS', {'multiagent': workflow}):
            run_experiment(Settings(mode='multiagent', num_of_execution=1), self.llm,
                           self.dataset / 'a.txt', self.output, self.validator)
        with (self.output / 'results.csv').open() as stream:
            row = next(csv.DictReader(stream))
        self.assertEqual(row['attempts'], '5')
        self.assertEqual(row['single-attempts'], '0')
        for name, count in counts.items():
            self.assertEqual(row[f'{name}-attempts'], str(count))

    def test_partial_counts_survive_infrastructure_error(self):
        def fail(*args, attempt_counts):
            attempt_counts.update(patient=2, condition=1)
            raise ValidatorError('Timeout')
        with patch.dict('main.WORKFLOWS', {'multiagent': fail}):
            with self.assertRaises(ValidatorError):
                run_experiment(Settings(mode='multiagent'), self.llm,
                               self.dataset, self.output, self.validator)
        with (self.output / 'results.csv').open() as stream:
            row = next(csv.DictReader(stream))
        self.assertEqual(row['fhir-valid'], '')
        self.assertEqual(row['patient-attempts'], '2')
        self.assertEqual(row['condition-attempts'], '1')
        self.assertEqual(row['merge-attempts'], '0')
        self.assertEqual(row['attempts'], '2')

    def test_development_repeats_only_first_ten_sorted_notes(self):
        for index in reversed(range(12)):
            (self.dataset / f'{index:02d}.txt').write_text(f'note {index}')
        workflow = Mock(return_value=WorkflowResult({'resourceType': 'Bundle'}, True, 1))
        with patch.dict('main.WORKFLOWS', {'single-agent': workflow}):
            run_experiment(Settings(development=True, num_of_execution=2), self.llm,
                           self.dataset, self.output, self.validator)
        self.assertEqual(workflow.call_count, 20)
        self.assertEqual([call.args[0] for call in workflow.call_args_list],
                         [f'note {i}' for i in range(10)] * 2)

    def test_development_false_processes_all_notes(self):
        for index in range(12):
            (self.dataset / f'{index:02d}.txt').write_text(f'note {index}')
        workflow = Mock(return_value=WorkflowResult({'resourceType': 'Bundle'}, True, 1))
        with patch.dict('main.WORKFLOWS', {'single-agent': workflow}):
            run_experiment(Settings(development=False, num_of_execution=1), self.llm,
                           self.dataset, self.output, self.validator)
        self.assertEqual(workflow.call_count, 14)

    def test_empty_note_rejected_before_experiment(self):
        (self.dataset / 'a.txt').write_text(' ')
        with self.assertRaises(ValueError):
            run_experiment(Settings(), self.llm, self.dataset, self.output, self.validator)
        self.assertFalse(self.output.exists())


if __name__ == '__main__':
    unittest.main()
