"""Offline tests: transport contract, orchestration, and failure preservation."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from agent import PROMPT_DIR, SPECIALISTS, single_agent, specialist
from llm import LLMClient, LLMResponse
from fhir_validator import ValidationResult, ValidatorError
from workflow_multiagent import run_multiagent
from workflow_singleagent import run_singleagent

BUNDLE = {"resourceType": "Bundle", "type": "collection", "entry": []}


class FakeLLM:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []
        self.histories = []

    def configuration(self):
        return {"model": "fake", "provider": "offline"}

    def generate(self, system_prompt, user_input, *, history=None):
        self.calls.append((system_prompt, user_input))
        self.histories.append(list(history or []))
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        if isinstance(response, LLMResponse):
            return response
        return LLMResponse(text=response, finish_reason="stop")


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.validator = Mock()
        self.validator.validate_output.return_value = ValidationResult()

    def test_every_specialist_and_merge_are_validated(self):
        fragments = [{"resources": []},
                     {"resources": []}, {"resources": []}, {"resources": []}]
        llm = FakeLLM([*(json.dumps(r) for r in fragments), json.dumps(BUNDLE)])
        result = run_multiagent("note", llm, self.validator)
        self.assertEqual(result.bundle, BUNDLE)
        self.assertTrue(result.valid)
        self.assertEqual(result.attempts, 1)
        self.assertEqual(len(llm.calls), 5)
        self.assertEqual([c[1] for c in llm.calls[:4]], ["note"] * 4)
        self.assertEqual(json.loads(llm.calls[4][1]), dict(zip(SPECIALISTS, fragments)))
        self.assertCountEqual([c.args[1] for c in self.validator.validate_output.call_args_list],
                              [*SPECIALISTS, "merge"])

    def test_specialists_overlap_and_usage_reaches_parent(self):
        from threading import Barrier, Lock
        from token_usage import CURRENT_USAGE, TokenUsage
        barrier, lock = Barrier(4), Lock()
        calls = []
        class ConcurrentLLM:
            def generate(self, system_prompt, user_input):
                with lock:
                    calls.append(user_input)
                if user_input == 'note':
                    barrier.wait(timeout=5)
                return LLMResponse(json.dumps(BUNDLE), usage={
                    'prompt_tokens': 10, 'completion_tokens': 2, 'total_tokens': 12})
        tracker = TokenUsage()
        context = CURRENT_USAGE.set(tracker)
        try:
            result = run_multiagent('note', ConcurrentLLM(), self.validator)
        finally:
            CURRENT_USAGE.reset(context)
        self.assertTrue(result.valid)
        self.assertEqual(calls[:4], ['note'] * 4)
        self.assertNotEqual(calls[4], 'note')
        columns = tracker.columns((*SPECIALISTS, 'merge'))
        self.assertEqual(columns['total-tokens'], 60)
        for name in (*SPECIALISTS, 'merge'):
            self.assertEqual(columns[f'{name}-total-tokens'], 12)

    def test_single_shared_rules_and_error_feedback(self):
        self.validator.validate_output.side_effect = [
            ValidationResult(("Observation.status is required",)), ValidationResult()]
        llm = FakeLLM([json.dumps(BUNDLE), json.dumps(BUNDLE)])
        result = run_singleagent("original note", llm, self.validator)
        self.assertTrue(result.valid)
        self.assertEqual(result.attempts, 2)
        self.assertIn("Observation.status is required", llm.calls[1][1])
        self.assertIn("original note", llm.calls[1][1])
        for name in SPECIALISTS:
            text = (PROMPT_DIR / f"{name}.md").read_text()
            rules = text.split("RESOURCE RULES\n", 1)[1].split("\n\nReturn a JSON object", 1)[0].strip()
            self.assertIn(rules, llm.calls[0][0])

    def test_invalid_json_corrected_before_validator(self):
        llm = FakeLLM(['not json', json.dumps(BUNDLE)])
        result = run_singleagent("note", llm, self.validator)
        self.assertEqual(result.attempts, 2)
        self.assertTrue(result.valid)
        self.validator.validate_output.assert_called_once()
        self.assertIn("Invalid JSON", llm.calls[1][1])

    def test_exhausted_final_bundle_is_returned_invalid(self):
        self.validator.validate_output.return_value = ValidationResult(("Missing status",))
        llm = FakeLLM([json.dumps(BUNDLE)] * 3)
        result = run_singleagent("note", llm, self.validator, max_attempts=3)
        self.assertFalse(result.valid)
        self.assertEqual(result.attempts, 3)
        self.assertEqual(result.agent_attempts, {"single": 3})
        self.assertEqual(result.bundle, BUNDLE)
        self.assertEqual(llm.histories[1], [
            {'role': 'user', 'content': 'note'},
            {'role': 'assistant', 'content': json.dumps(BUNDLE)}])
        self.assertEqual(llm.histories[2][:2], llm.histories[1])
        self.assertEqual(llm.histories[2][2]['content'], llm.calls[1][1])
        self.assertEqual(len(llm.histories[2]), 4)

    def test_exhausted_specialist_stops_before_merge(self):
        self.validator.validate_output.return_value = ValidationResult(("Invalid Patient",))
        llm = FakeLLM(['{"resources": []}'] * 8)
        result = run_multiagent("note", llm, self.validator, max_attempts=2)
        self.assertFalse(result.valid)
        self.assertIsNone(result.bundle)
        self.assertEqual(len(llm.calls), 8)
        self.assertEqual(result.attempts, 5)

    def test_counts_corrections_across_specialists_and_merge(self):
        bad, good = ValidationResult(("Error",)), ValidationResult()
        seen = {}
        def validate(payload, scope):
            seen[scope] = seen.get(scope, 0) + 1
            return bad if scope in ('patient', 'merge') and seen[scope] == 1 else good
        self.validator.validate_output.side_effect = validate
        llm = FakeLLM([json.dumps(BUNDLE)] * 7)
        result = run_multiagent("note", llm, self.validator)
        self.assertTrue(result.valid)
        self.assertEqual(result.attempts, 3)
        self.assertEqual(len(llm.calls), 7)
        self.assertEqual(result.agent_attempts, {
            "patient": 2, "condition": 1, "observation": 1, "medication": 1, "merge": 2})

    def test_infrastructure_error_does_not_trigger_llm_correction(self):
        self.validator.validate_output.side_effect = ValidatorError("Unavailable")
        llm = FakeLLM([json.dumps(BUNDLE)])
        with self.assertRaises(ValidatorError):
            run_singleagent("note", llm, self.validator)
        self.assertEqual(len(llm.calls), 1)

    def test_truncated_json_is_not_validated(self):
        llm = FakeLLM([LLMResponse(text=json.dumps(BUNDLE), finish_reason="length")])
        result = run_singleagent("note", llm, self.validator, max_attempts=1)
        self.assertFalse(result.valid)
        self.assertIsNone(result.bundle)
        self.validator.validate_output.assert_not_called()


class TransportTests(unittest.TestCase):
    def test_chat_contract_for_both_providers(self):
        for provider in ("openai", "openrouter"):
            with self.subTest(provider=provider):
                sdk = Mock()
                sdk.chat.completions.create.return_value = SimpleNamespace(
                    id="response-1", model="actual-model", usage=None,
                    choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(
                        content='{"resources": []}', refusal=None))])
                llm = LLMClient("chosen-model", provider, client=sdk,
                                temperature=0, json_mode=True)
                response = llm.generate("system", "note")
                sdk.chat.completions.create.assert_called_once_with(
                    model="chosen-model", temperature=0,
                    response_format={"type": "json_object"},
                    messages=[{"role": "system", "content": "system"},
                              {"role": "user", "content": "note"}])
                self.assertEqual(response.model, "actual-model")
                self.assertNotIn("api_key", llm.configuration())

    def test_provider_credentials_and_endpoints_are_isolated(self):
        sdk_module = SimpleNamespace(OpenAI=Mock())
        with patch.dict(os.environ, {"OPENAI_API_KEY": "fake-openai",
                                     "OPENROUTER_API_KEY": "fake-router"}, clear=True):
            with patch.dict("sys.modules", {"openai": sdk_module}):
                for provider, key in (("openai", "fake-openai"),
                                      ("openrouter", "fake-router")):
                    LLMClient("test", provider)
                    sdk_module.OpenAI.assert_called_with(
                        api_key=key, base_url=LLMClient.ENDPOINTS[provider],
                        max_retries=0, timeout=120.0)

    def test_missing_router_key_does_not_fall_back_to_openai(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "fake"}, clear=True):
            with self.assertRaisesRegex(ValueError, "OPENROUTER_API_KEY"):
                LLMClient("test", "openrouter")

    def test_unsupported_options_omitted_by_default(self):
        sdk = Mock()
        sdk.chat.completions.create.return_value = SimpleNamespace(
            id="r", model="m", usage=None,
            choices=[SimpleNamespace(finish_reason="stop",
                                     message=SimpleNamespace(content="{}", refusal=None))])
        LLMClient("test", client=sdk).generate("system", "note")
        parameters = sdk.chat.completions.create.call_args.kwargs
        self.assertNotIn("temperature", parameters)
        self.assertNotIn("response_format", parameters)


if __name__ == "__main__":
    unittest.main()
