import tempfile
import unittest
from pathlib import Path

from results_paths import prepare_directory, result_directory


class ResultPathTests(unittest.TestCase):
    def test_modes_and_openrouter_names(self):
        self.assertEqual(result_directory('single-agent', 'gpt-4.1-mini').parts[-3:],
                         ('results', 'single', 'gpt-4.1-mini'))
        self.assertEqual(result_directory('multiagent', 'deepseek/deepseek-chat').parts[-3:],
                         ('results', 'multi', 'deepseek-chat'))
        self.assertEqual(result_directory('singleagent', 'openai/gpt-4.1-mini'),
                         result_directory('single-agent', 'gpt-4.1-mini'))

    def test_model_name_cannot_escape_results(self):
        with self.assertRaises(ValueError):
            result_directory('multiagent', '../..')
        self.assertEqual(result_directory('multiagent', 'model:free').name, 'model_free')

    def test_cleanup_does_not_follow_symlinks(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = root / 'target'
            target.mkdir()
            (target / 'keep.txt').write_text('keep')
            output = root / 'output'
            output.mkdir()
            (output / 'execution-001').symlink_to(target, target_is_directory=True)
            prepare_directory(output, experiment=True)
            self.assertTrue((target / 'keep.txt').exists())
            self.assertFalse((output / 'execution-001').exists())
