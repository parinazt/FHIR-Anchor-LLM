"""Generate, validate, and if necessary correct a complete FHIR Bundle."""
from agent import single_agent
from fhir_validator import FHIRValidator
from llm import LLMClient
from workflow_common import WorkflowResult, run_cli


def run_singleagent(note: str, llm: LLMClient, validator: FHIRValidator,
                    max_attempts: int = 3, attempt_counts=None) -> WorkflowResult:
    counts = attempt_counts if attempt_counts is not None else {}
    result = single_agent(llm).run_validated(note, validator, max_attempts, counts)
    return WorkflowResult(result.payload, result.valid, result.attempts, dict(counts))


if __name__ == "__main__":
    run_cli("single-agent")
