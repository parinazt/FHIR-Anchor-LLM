"""Run repeated single-agent or multiagent experiments on clinical notes."""
from __future__ import annotations

import argparse
import csv
import os
from datetime import datetime, timezone
from pathlib import Path

from config import MODES, ROOT, Settings, load_settings
from fhir_validator import FHIRValidator, ValidatorError
from conversation_log import CURRENT_LOG, ConversationLog
from token_usage import CURRENT_USAGE, TokenUsage, usage_fields
from llm import LLMClient
from results_paths import prepare_directory, result_directory
from workflow_common import write_json
from workflow_multiagent import run_multiagent
from workflow_singleagent import run_singleagent

AGENT_NAMES = ("single", "patient", "condition", "observation", "medication", "merge")

WORKFLOWS = {"single-agent": run_singleagent, "multiagent": run_multiagent}


def run_experiment(settings: Settings, llm: LLMClient,
                   input_path: Path, output_dir: Path, validator=None) -> dict:
    """Repeat the same fixed dataset; keep every attempt, including failures.

    Each agent validates and repairs its output. Technical validator failures
    stop the experiment and leave validity blank rather than reporting invalid FHIR.
    """
    input_path, output_dir = Path(input_path), Path(output_dir)
    paths = sorted(input_path.glob("*.txt")) if input_path.is_dir() else [input_path]
    if settings.development:
        paths = paths[:10]
    print(f"Selected {len(paths)} note(s); development={settings.development}")
    if not paths:
        raise ValueError("No .txt notes found in the input directory")
    notes = [(path, path.read_text(encoding="utf-8")) for path in paths]
    if any(not note.strip() for _, note in notes):
        raise ValueError("Input notes must not be empty")
    # Read the dataset once so all executions use identical source text.
    for source, _ in notes:
        if output_dir.resolve() in source.resolve().parents:
            raise ValueError("Input notes must be outside the output directory")
    validator = validator if validator is not None else FHIRValidator()
    validator.preflight()
    prepare_directory(output_dir, experiment=True)
    outputs = output_dir / "outputs"
    outputs.mkdir()
    logs = output_dir / "logs"
    logs.mkdir()
    report = {"generation_counts": {"completed": 0, "invalid_output": 0, "failed": 0}}
    workflow = WORKFLOWS[settings.mode]
    with (output_dir / "results.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=[
            "timestamp", "file", "run-id", "fhir-valid", "attempts",
            *(f"{name}-attempts" for name in AGENT_NAMES),
            *usage_fields(AGENT_NAMES),
        ])
        writer.writeheader()
        stream.flush()
        for execution in range(1, settings.num_of_execution + 1):
            for source, note in notes:
                timestamp = datetime.now(timezone.utc).isoformat()
                status, valid, attempts = "failed", 0, ""
                technical_error = None
                agent_attempts = {}
                token_usage = TokenUsage()
                usage_context = CURRENT_USAGE.set(token_usage)
                logger = ConversationLog(logs / f"{execution}_{source.stem}.jsonl")
                log_context = CURRENT_LOG.set(logger)
                logger.emit('run_started', file=source.name, run_id=execution,
                            mode=settings.mode, max_attempts=settings.max_attempts)
                try:
                    result = workflow(note, llm, validator, settings.max_attempts,
                                      attempt_counts=agent_attempts)
                    agent_attempts.update(result.agent_attempts)
                    if result.bundle is not None:
                        write_json(outputs / f"{execution}_{source.stem}.json", result.bundle)
                    valid, attempts = int(result.valid), result.attempts
                    status = "completed" if result.valid else "invalid_output"
                except ValidatorError as exc:
                    valid = ""
                    technical_error = exc
                    print(f"Validator infrastructure error: {exc}")
                except Exception as exc:
                    print(f"Generation failed: {source.name}, run {execution} ({type(exc).__name__})")
                finally:
                    CURRENT_USAGE.reset(usage_context)
                    CURRENT_LOG.reset(log_context)
                    logger.emit('run_finished', status=status, fhir_valid=valid,
                                agent_attempts=dict(agent_attempts),
                                token_usage=token_usage.columns(AGENT_NAMES))
                if agent_attempts:
                    attempts = 1 + sum(max(count - 1, 0) for count in agent_attempts.values())
                writer.writerow({"timestamp": timestamp, "file": source.name,
                                 "run-id": execution, "fhir-valid": valid, "attempts": attempts,
                                 **{f"{name}-attempts": agent_attempts.get(name, 0)
                                    for name in AGENT_NAMES},
                                 **token_usage.columns(AGENT_NAMES)})
                stream.flush()
                report["generation_counts"][status] += 1
                print(f"[{execution}/{settings.num_of_execution}] {source.name}: {status}")
                if technical_error is not None:
                    raise technical_error
    return report


def main(arguments=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, help="Defaults to the project .env")
    parser.add_argument("--input", type=Path, default=ROOT / "dataset",
                        help="One note or a directory of .txt notes")
    parser.add_argument("--output-dir", type=Path, help="Experiment directory; previous generated results are replaced")
    parser.add_argument("--provider", choices=tuple(LLMClient.ENDPOINTS),
                        default=os.environ.get("LLM_PROVIDER", "openai"))
    parser.add_argument("--model", default=os.environ.get("LLM_MODEL"))
    parser.add_argument("--mode", choices=MODES, help="Overrides MODE")
    parser.add_argument("--num-of-execution", type=int, help="Overrides NUM_OF_EXECUTION")
    parser.add_argument("--max-attempts", type=int, help="Maximum generations per agent (overrides MAX_ATTEMPTS)")
    parser.add_argument("--temperature", type=float)
    parser.add_argument("--json-mode", action="store_true")
    parser.add_argument("--base-url")
    args = parser.parse_args(arguments)
    if not args.model:
        parser.error("Provide --model or set LLM_MODEL in the process environment")
    try:
        loaded = load_settings(args.env_file)
        settings = Settings(
            openai_token=loaded.openai_token, openrouter_token=loaded.openrouter_token,
            development=loaded.development,
            mode=args.mode if args.mode is not None else loaded.mode,
            max_attempts=args.max_attempts if args.max_attempts is not None else loaded.max_attempts,
            num_of_execution=(args.num_of_execution if args.num_of_execution is not None
                              else loaded.num_of_execution),
        )
        llm = LLMClient(args.model, args.provider, api_key=settings.api_key(args.provider),
                        base_url=args.base_url, temperature=args.temperature,
                        json_mode=args.json_mode)
    except (ValueError, OSError, ImportError) as exc:
        parser.error(str(exc))
    output = args.output_dir or result_directory(settings.mode, args.model)
    try:
        report = run_experiment(settings, llm, args.input, output)
    except (OSError, ValueError, ValidatorError) as exc:
        parser.error(str(exc))
    print(f"Experiment saved in {output}. results.csv contains structural FHIR validation results (external terminology disabled).")
    if report["generation_counts"]["failed"] or report["generation_counts"]["invalid_output"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
