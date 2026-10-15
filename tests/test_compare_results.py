import json
import statistics
import tempfile
import unittest
from pathlib import Path

from compare_results import aggregate, load_results


def entry(value):
    return {'valid': True, 'counts': {kind: {'resources': value, 'properties': value * 2}
                                    for kind in ('Patient', 'Condition', 'Observation',
                                                 'MedicationStatement', 'MedicationRequest', 'ALL')}}


class ComparisonTests(unittest.TestCase):
    def test_sd_is_across_runs_and_total_is_sum_before_sd(self):
        single = {(file, run): entry(run * factor) for file, factor in [('a.txt', 1), ('b.txt', 2)]
                  for run in range(1, 6)}
        multi = {key: entry(item['counts']['ALL']['resources'] * 2) for key, item in single.items()}
        raw, summary, meta = aggregate(single, multi)
        total = next(r for r in summary if r['case'] == 'TOTAL' and r['method'] == 'single'
                     and r['metric'] == 'resources' and r['resource_type'] == 'ALL')
        self.assertEqual(total['mean'], 9)
        self.assertAlmostEqual(total['std'], statistics.stdev([3, 6, 9, 12, 15]))
        self.assertEqual(total['n_runs'], 5)
        self.assertEqual(meta['included_runs'], [1, 2, 3, 4, 5])

    def test_incomplete_runs_are_not_zero_or_false_sd(self):
        data = {('a.txt', 1): entry(2)}
        with self.assertRaisesRegex(ValueError, 'Expected 5'):
            aggregate(data, data)
        _, summary, meta = aggregate(data, data, allow_incomplete=True)
        self.assertTrue(all(r['std'] is None for r in summary))
        self.assertEqual(meta['excluded_runs'], [2, 3, 4, 5])
        with self.assertRaisesRegex(ValueError, 'No complete paired'):
            aggregate(data, {('b.txt', 1): entry(2)}, allow_incomplete=True)

    def test_resume_last_row_and_top_level_properties(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / 'outputs').mkdir()
            (directory / 'results.csv').write_text('file,run-id,fhir-valid\na.txt,1,0\na.txt,1,1\nb.txt,1,0\n')
            bundle = {'resourceType': 'Bundle', 'type': 'collection', 'entry': [
                {'fullUrl': 'x', 'resource': {'resourceType': 'Patient', 'id': 'patient-1',
                 'gender': 'female', 'name': [{'family': 'Example', 'given': ['A']}]}}]}
            (directory / 'outputs' / '1_a.json').write_text(json.dumps(bundle))
            results = load_results(directory)
            self.assertEqual(results[('a.txt', 1)]['counts']['ALL'], {'resources': 1, 'properties': 2})
            self.assertEqual(results[('b.txt', 1)]['counts']['ALL']['resources'], 0)
            (directory / 'outputs' / '1_a.json').unlink()
            with self.assertRaisesRegex(ValueError, 'missing'):
                load_results(directory)
