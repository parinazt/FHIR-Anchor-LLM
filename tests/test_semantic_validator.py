"""Semantic rules: assert conservative handling, including deliberately wrong facts."""
import copy
import unittest

from semantic_validator import Fact, SemanticValidator


def bundle(resource):
    return {'resourceType': 'Bundle', 'entry': [{'resource': resource}]}


def quantity(concept, value, unit, **extra):
    return Fact('Observation', 'o', 'entry[0].resource.valueQuantity', 'quantity', concept,
                {'value': value, 'unit': unit, **extra})


class SemanticTests(unittest.TestCase):
    def setUp(self):
        self.checker = SemanticValidator()

    def test_weight_direction_comparator_and_precision(self):
        source = 'Appetite ↓ ~4–5kg wt loss (pt report)'
        cases = [(4.5, {'comparator': '<'}, 'contradicted'),
                 (-4.5, {}, 'partial_match'), (4.5, {}, 'contradicted'),
                 (-8, {}, 'contradicted')]
        for value, extra, expected in cases:
            result = self.checker.check(quantity('Weight change', value, 'kg', **extra), source)
            self.assertEqual(result.status, expected)
            self.assertEqual(result.observed['minimum'], -5)
            self.assertEqual(result.observed['maximum'], -4)
            self.assertTrue(result.observed['approximate'])
            self.assertEqual(source[result.evidence_start:result.evidence_end], result.evidence)

    def test_weight_loss_label_is_distinct_from_signed_change(self):
        self.assertEqual(self.checker.check(quantity('Weight loss', 4.5, 'kg'),
                                           'Patient lost 4.5 kg').status, 'supported')
        self.assertEqual(self.checker.check(quantity('Weight change', -4.5, 'kg'),
                                           'Patient lost 4.5 kg').status, 'supported')

    def test_vitals_are_linked_to_the_correct_concept(self):
        source = 'Obs WNL BP 112/70 HR 86 afeb SpO2 99% RA'
        for concept, value, unit in [('Blood pressure systolic', 112, 'mmHg'),
                                      ('Blood pressure diastolic', 70, 'mmHg'),
                                      ('Heart rate', 86, 'beats/minute'),
                                      ('Oxygen saturation', 99, '%')]:
            self.assertEqual(self.checker.check(quantity(concept, value, unit), source).status, 'supported')
        self.assertEqual(self.checker.check(quantity('Heart rate', 99, 'beats/minute'), source).status,
                         'contradicted')
        self.assertEqual(self.checker.check(quantity('Temperature', 37, 'degrees Celsius'), source).status,
                         'not_verifiable')

    def test_numeric_conversion_and_repeated_values(self):
        self.assertEqual(self.checker.check(quantity('Weight loss', 4500, 'g'),
                                           'Lost 4.5kg').status, 'supported')
        result = self.checker.check(quantity('Heart rate', 80, 'beats/min'), 'HR 80\nHR 110')
        self.assertEqual(result.status, 'not_verifiable')

    def test_negation_and_conditional_context(self):
        fact = Fact('Observation', 'o', 'valueBoolean', 'boolean', 'Psychosis', True)
        result = self.checker.check(fact, 'No psychotic Sx')
        self.assertEqual(result.status, 'contradicted')
        self.assertIn('No', result.evidence)
        fact.expected = False
        self.assertEqual(self.checker.check(fact, 'No psychotic Sx').status, 'supported')
        fact.expected = True
        self.assertEqual(self.checker.check(fact, 'Review if psychosis').status, 'not_verifiable')

    def test_no_plan_does_not_negate_ideation(self):
        fact = Fact('Observation', 'o', 'code.text', 'text', '', 'Passive suicidal ideation')
        self.assertEqual(self.checker.check(fact, 'Passive SI no plan/intent').status, 'supported')

    def test_medication_dose_does_not_match_other_drug(self):
        fact = Fact('MedicationRequest', 'm', 'dosageInstruction[0].doseQuantity',
                    'quantity', 'Sertraline', {'value': 2, 'unit': 'mg'})
        self.assertEqual(self.checker.check(fact, 'Sertraline stopped, diazepam 2mg').status, 'not_verifiable')
        fact.expected['value'] = 25
        self.assertEqual(self.checker.check(fact, 'Start sertraline 25mg daily').status, 'supported')
        self.assertEqual(self.checker.check(fact, 'Start sertraline 25mg then 50mg').status, 'not_verifiable')

    def test_status_does_not_infer_completed_from_no_current_use(self):
        fact = Fact('MedicationStatement', 'm', 'status', 'medication_status', 'Diazepam', 'completed')
        self.assertEqual(self.checker.check(fact, 'Old diazepam 2mg PRN, none now').status, 'not_verifiable')
        fact.concept, fact.expected = 'Sertraline', 'stopped'
        self.assertEqual(self.checker.check(fact, 'Prev sertraline 50mg self-ceased when pregnant').status, 'supported')

    def test_inventory_includes_unknown_fields_and_coded_terms(self):
        data = bundle({'resourceType':'Observation', 'id':'o', 'status':'final',
                       'code':{'text':'Heart rate', 'coding':[{'system':'http://loinc.org', 'code':'8867-4'}]},
                       'subject':{'reference':'Patient/patient-1'},
                       'valueQuantity':{'value':80, 'unit':'beats/min'},
                       'effectiveDateTime':'2025-01-01', 'interpretation':[{'text':'Normal'}]})
        original = copy.deepcopy(data)
        report = self.checker.evaluate(data, 'HR 80')
        self.assertEqual(data, original)
        paths = [f.fact.path for f in report.findings]
        self.assertTrue(any(p.endswith('effectiveDateTime') for p in paths))
        self.assertFalse(any('.coding[' in p for p in paths))
        self.assertFalse(any(p.endswith('.status') for p in paths))
        self.assertGreater(report.summary()['not_verifiable'], 0)
        self.assertLess(report.summary()['assessment_coverage'], 1)

    def test_component_context_and_numeric_subclaims(self):
        data = bundle({'resourceType':'Observation', 'code':{'text':'Vital signs'},
                       'component':[{'code':{'text':'Heart rate'}, 'valueQuantity':{'value':86,'unit':'bpm'}}]})
        facts, _ = self.checker.extract_facts(data)
        numeric = [f for f in facts if f.kind == 'quantity'][0]
        self.assertEqual(numeric.concept, 'Heart rate')
        data = bundle({'resourceType':'Observation', 'code':{'text':'Panic attacks frequency and features'},
                       'valueString':'2-3 panic attacks per week lasting 10-15 minutes, with tremor'})
        report = self.checker.evaluate(data, 'Panic attacks 2–3/wk (palps SOB) 10–15min self-resolve')
        numeric = [f for f in report.findings if f.fact.kind == 'range']
        self.assertEqual(len(numeric), 2)
        self.assertTrue(all(f.status == 'supported' for f in numeric))
        self.assertTrue(any(f.status == 'not_verifiable' for f in report.findings))

    def test_demographics_not_inferred_from_pregnancy(self):
        fact = Fact('Patient', 'p', 'gender', 'gender', '', 'female')
        self.assertEqual(self.checker.check(fact, '29F new pt').status, 'supported')
        self.assertEqual(self.checker.check(fact, 'Currently pregnant').status, 'not_verifiable')

    def test_empty_bundle_has_no_perfect_score(self):
        report = self.checker.evaluate({'resourceType':'Bundle', 'entry':[]}, 'HR 80')
        self.assertIsNone(report.summary()['assessment_coverage'])
        self.assertIsNone(report.summary()['supported_fraction_of_all_checks'])

    def test_empty_source_and_nonfinite_values(self):
        with self.assertRaises(ValueError):
            self.checker.evaluate(bundle({'resourceType': 'Patient'}), '  ')
        self.assertEqual(self.checker.check(quantity('Heart rate', float('nan'), 'bpm'),
                                           'HR 80').status, 'not_verifiable')

    def test_medication_status_ambiguous_or_negated(self):
        fact = Fact('MedicationStatement', 'm', 'status', 'medication_status', 'Sertraline', 'stopped')
        for source in ('Sertraline not stopped', 'Sertraline continues; diazepam stopped'):
            self.assertEqual(self.checker.check(fact, source).status, 'not_verifiable')

    def test_technical_coding_metadata_is_excluded_but_clinical_values_remain(self):
        data = bundle({'resourceType': 'Condition', 'id': 'c',
                       'clinicalStatus': {'coding': [{'id': 'coding-id',
                           'system': 'http://terminology.hl7.org/CodeSystem/condition-clinical',
                           'version': '4.0.1', 'userSelected': True,
                           'code': 'active', 'display': 'Active'}]},
                       'extension': [{'url': 'https://example.org/clinical-detail',
                                      'valueString': 'Fatigue'}]})
        report = self.checker.evaluate(data, 'Fatigue')
        paths = [f.fact.path for f in report.findings]
        self.assertEqual(len(paths), 1)
        self.assertFalse(any(p.endswith('.code') for p in paths))
        self.assertFalse(any(p.endswith('.display') for p in paths))
        self.assertTrue(any(p.endswith('.valueString') for p in paths))
        self.assertFalse(any(p.endswith(('.system', '.version', '.userSelected', '.url', '.id'))
                             for p in paths))
        self.assertEqual(report.summary()['total_checks'], 1)

    def test_expanded_clinical_phrase_preserves_negation_and_evidence(self):
        source = '1. Postnatal depression mod–severe with intrusive harm thoughts (ego-dystonic) no psychosis'
        target = 'Postnatal depression moderate to severe with intrusive harm thoughts (egodystonic) no psychosis'
        fact = Fact('Condition', 'c', 'code.text', 'text', '', target)
        result = self.checker.check(fact, source)
        self.assertEqual(result.status, 'supported')
        self.assertEqual(source[result.evidence_start:result.evidence_end], result.evidence)
        self.assertIn('mod–severe', result.evidence)
        self.assertEqual(self.checker.check(fact, source.replace('no psychosis', 'psychosis')).status,
                         'not_verifiable')
        self.assertEqual(self.checker.check(fact, 'Possible ' + source).status, 'not_verifiable')

    def test_quotes_do_not_prevent_lexical_matching(self):
        for target in ('Temperature', '"Temperature"', '“Temperature”'):
            fact = Fact('Observation', 'o', 'code.text', 'text', '', target)
            self.assertEqual(self.checker.check(fact, 'Temperature 37 C').status, 'supported')
            self.assertEqual(self.checker.check(fact, 'afeb').status, 'not_verifiable')

    def test_measurement_is_one_fact_not_a_label_and_a_value(self):
        observation = {'resourceType': 'Observation', 'code': {'text': 'Heart rate'},
                       'valueQuantity': {'value': 86, 'unit': 'beats/minute'}}
        for resource in (observation, {'resourceType': 'Observation',
                                      'code': {'text': 'Vital signs'},
                                      'component': [{k: v for k, v in observation.items() if k != 'resourceType'}]}):
            report = self.checker.evaluate(bundle(resource), 'HR 99')
            self.assertEqual(len(report.findings), 1)
            finding = report.findings[0]
            self.assertEqual(finding.fact.concept, 'Heart rate')
            self.assertEqual(finding.fact.expected['value'], 86)
            self.assertEqual(finding.status, 'contradicted')
            report = self.checker.evaluate(bundle(resource), 'HR 86')
            self.assertEqual(report.findings[0].status, 'supported')
            report = self.checker.evaluate(bundle(resource), 'Heart rate recorded')
            self.assertEqual(report.findings[0].status, 'not_verifiable')
