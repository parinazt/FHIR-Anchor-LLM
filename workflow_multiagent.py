"""Validate each specialist before passing its output to the Merge agent."""
import json
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from threading import Lock

from agent import SPECIALISTS, merge_agent, specialist
from fhir_validator import FHIRValidator
from llm import LLMClient
from workflow_common import WorkflowResult, run_cli


def run_multiagent(note: str, llm: LLMClient, validator: FHIRValidator,
                   max_attempts: int = 3, attempt_counts=None) -> WorkflowResult:
    counts = attempt_counts if attempt_counts is not None else {}
    # Serialize Java validation against its shared package cache; LLM calls overlap.
    validation_lock = Lock()

    class SharedValidator:
        def validate_output(self, payload, scope):
            with validation_lock:
                return validator.validate_output(payload, scope)

    shared_validator = SharedValidator()
    with ThreadPoolExecutor(max_workers=len(SPECIALISTS)) as executor:
        futures = {
            name: executor.submit(copy_context().run,
                                  specialist(name, llm).run_validated,
                                  note, shared_validator, max_attempts, counts)
            for name in SPECIALISTS
        }
        # The executor waits for running workers even when one raises, preserving
        # all attempt/token counts before the caller writes the experiment row.
        results = {name: future.result() for name, future in futures.items()}
    corrections = sum(result.attempts - 1 for result in results.values())
    if any(not result.valid for result in results.values()):
        return WorkflowResult(None, False, 1 + corrections, dict(counts))
    outputs = {name: result.payload for name, result in results.items()}
    result = merge_agent(llm).run_validated(
        json.dumps(outputs, ensure_ascii=False), validator, max_attempts, counts)
    corrections += result.attempts - 1
    return WorkflowResult(result.payload, result.valid, 1 + corrections, dict(counts))


if __name__ == "__main__":
    run_cli("multiagent")
