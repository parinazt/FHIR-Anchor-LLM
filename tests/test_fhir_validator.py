"""Parser and subprocess tests; the regular suite does not launch Java."""
import copy
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fhir_validator import FHIRValidator, ValidationResult, ValidatorError, parse_outcome


def outcome(severity='information', code='informational'):
    return {'resourceType': 'OperationOutcome', 'issue': [
        {'severity': severity, 'code': code, 'details': {'text': 'message'},
         'expression': ['Bundle.entry[1]']}]}


class ValidatorTests(unittest.TestCase):
    def test_only_errors_and_fatal_invalidate(self):
        for severity in ('information', 'warning', 'error', 'fatal'):
            result = parse_outcome(outcome(severity))
            self.assertEqual(result.valid, severity in ('information', 'warning'))

    def test_malformed_and_infrastructure_outcomes_are_not_validity_results(self):
        for data in ({}, {'resourceType': 'OperationOutcome', 'issue': []},
                     outcome('error', 'timeout'), outcome('fatal', 'exception')):
            with self.assertRaises(ValidatorError):
                parse_outcome(data)

    def test_specialist_wrapper_does_not_mutate_output(self):
        payload = {'resources': [{'resourceType': 'Observation', 'id': 'obs-1',
                                 'subject': {'reference': 'Patient/patient-1'}}]}
        original = copy.deepcopy(payload)
        validator = FHIRValidator()
        with patch.object(validator, 'validate', return_value=ValidationResult()) as run:
            self.assertTrue(validator.validate_output(payload, 'observation').valid)
        bundle = run.call_args.args[0]
        self.assertEqual(bundle['entry'][0]['resource'],
                         {'resourceType': 'Patient', 'id': 'patient-1'})
        self.assertEqual(bundle['entry'][1]['resource'], payload['resources'][0])
        self.assertEqual(payload, original)

    def test_wrong_scope_and_empty_patient_rejected_before_java(self):
        validator = FHIRValidator()
        with patch.object(validator, 'validate') as run:
            self.assertFalse(validator.validate_output({'resources': []}, 'patient').valid)
            self.assertFalse(validator.validate_output(
                {'resources': [{'resourceType': 'Patient'}]}, 'condition').valid)
            self.assertFalse(validator.validate_output({'resources': 'wrong'}, 'observation').valid)
        run.assert_not_called()

    def test_empty_optional_scope_validates_reference_context(self):
        validator = FHIRValidator()
        with patch.object(validator, 'validate', return_value=ValidationResult()) as run:
            self.assertTrue(validator.validate_output({'resources': []}, 'medication').valid)
        self.assertEqual(len(run.call_args.args[0]['entry']), 1)

    def test_bundle_is_validated_without_rewriting(self):
        bundle = {'resourceType': 'Bundle', 'type': 'collection', 'entry': [
            {'resource': {'resourceType': 'Patient', 'id': 'patient-1'}}]}
        validator = FHIRValidator()
        with patch.object(validator, 'validate', return_value=ValidationResult()) as run:
            validator.validate_output(bundle, 'merge')
        self.assertIs(run.call_args.args[0], bundle)

    def test_cli_report_and_flags(self):
        with tempfile.TemporaryDirectory() as temp:
            jar = Path(temp) / 'validator.jar'
            jar.touch()
            validator = FHIRValidator(jar, cache_dir=Path(temp) / 'cache')
            def invoke(command, **kwargs):
                self.assertEqual(command[command.index('-version') + 1], '4.0.1')
                self.assertEqual(command[command.index('-tx') + 1], 'n/a')
                self.assertNotIn('shell', kwargs)
                Path(command[command.index('-output') + 1]).write_text(json.dumps(outcome('error')))
                return SimpleNamespace(returncode=1)
            with patch('fhir_validator.shutil.which', return_value='/java'):
                with patch('fhir_validator.subprocess.run', side_effect=invoke):
                    self.assertFalse(validator.validate({'resourceType': 'Patient'}).valid)
                with patch('fhir_validator.subprocess.run',
                           side_effect=subprocess.TimeoutExpired('java', 1)):
                    with self.assertRaises(ValidatorError):
                        validator.validate({})
                with patch('fhir_validator.subprocess.run', return_value=SimpleNamespace(returncode=1)):
                    with self.assertRaises(ValidatorError):
                        validator.validate({})
