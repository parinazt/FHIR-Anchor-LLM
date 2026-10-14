import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from pathlib import Path
from unittest.mock import Mock

from agent import Agent
from conversation_log import CURRENT_LOG, ConversationLog
from fhir_validator import ValidationResult
from llm import LLMResponse


class ConversationLogTests(unittest.TestCase):
    def test_correction_conversation_and_parallel_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'run.jsonl'
            logger = ConversationLog(path)
            context = CURRENT_LOG.set(logger)
            llm = Mock()
            llm.generate.side_effect = [LLMResponse('broken'), LLMResponse('{}')]
            validator = Mock()
            validator.validate_output.return_value = ValidationResult()
            try:
                Agent('single', 'system', llm).run_validated('source', validator)
                with ThreadPoolExecutor(4) as executor:
                    tasks = [executor.submit(copy_context().run, logger.emit,
                                             'parallel', agent=str(i)) for i in range(20)]
                    for task in tasks:
                        task.result()
            finally:
                CURRENT_LOG.reset(context)
            events = [json.loads(line) for line in path.read_text().splitlines()]
            self.assertEqual([e['sequence'] for e in events], list(range(1, len(events) + 1)))
            self.assertEqual([e['event'] for e in events[:6]],
                             ['request', 'response', 'validation'] * 2)
            self.assertEqual(events[0]['messages'][1]['content'], 'source')
            self.assertEqual(events[1]['text'], 'broken')
            self.assertFalse(events[2]['valid'])
            self.assertIn('Invalid JSON', events[3]['messages'][1]['content'])
            self.assertTrue(events[5]['valid'])
            self.assertEqual(len(events), 26)
