"""Token usage must survive invalid responses and failures without fabricated zeros."""
import unittest
from unittest.mock import Mock

from agent import Agent
from llm import LLMResponse
from token_usage import CURRENT_USAGE, TokenUsage


class TokenUsageTests(unittest.TestCase):
    def test_agent_calls_accumulate_and_failures_remain_unknown(self):
        tracker = TokenUsage()
        context = CURRENT_USAGE.set(tracker)
        llm = Mock()
        llm.generate.side_effect = [
            LLMResponse('invalid JSON', usage={'prompt_tokens': 10, 'completion_tokens': 3, 'total_tokens': 13}),
            LLMResponse('{}', usage={'prompt_tokens': 20, 'completion_tokens': 5, 'total_tokens': 25}),
            RuntimeError('transport failure'),
        ]
        try:
            agent = Agent('patient', 'prompt', llm)
            agent.run('note')
            agent.run('correction')
            columns = tracker.columns(('patient', 'merge'))
            self.assertEqual(columns['input-tokens'], 30)
            self.assertEqual(columns['patient-total-tokens'], 38)
            self.assertEqual(columns['merge-total-tokens'], 0)
            with self.assertRaises(RuntimeError):
                Agent('merge', 'prompt', llm).run('fragments')
            columns = tracker.columns(('patient', 'merge'))
            self.assertEqual(columns['total-tokens'], '')
            self.assertEqual(columns['patient-total-tokens'], 38)
            self.assertEqual(columns['merge-total-tokens'], '')
        finally:
            CURRENT_USAGE.reset(context)
        self.assertIsNone(CURRENT_USAGE.get())
        self.assertEqual(TokenUsage().columns(())['total-tokens'], 0)

    def test_missing_usage_is_not_zero(self):
        tracker = TokenUsage()
        tracker.start('single')['usage'] = {'prompt_tokens': 10}
        columns = tracker.columns(('single',))
        self.assertEqual(columns['input-tokens'], 10)
        self.assertEqual(columns['output-tokens'], '')
        self.assertEqual(columns['total-tokens'], '')
