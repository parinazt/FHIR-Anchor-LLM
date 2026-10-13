"""Structural FHIR R4 validation through the local HL7 CLI (no external terminology)."""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SCOPES = {
    "patient": {"Patient"}, "condition": {"Condition"},
    "observation": {"Observation"},
    "medication": {"MedicationStatement", "MedicationRequest"},
}
ALLOWED = set().union(*SCOPES.values())


class ValidatorError(RuntimeError):
    """Infrastructure failure: never interpret this as a FHIR finding."""


@dataclass(frozen=True)
class ValidationResult:
    errors: tuple = ()
    warnings: tuple = ()

    @property
    def valid(self) -> bool:
        return not self.errors


def parse_outcome(outcome: dict) -> ValidationResult:
    if not isinstance(outcome, dict) or outcome.get("resourceType") != "OperationOutcome":
        raise ValidatorError("Validator did not return an OperationOutcome")
    issues = outcome.get("issue")
    if not isinstance(issues, list) or not issues:
        raise ValidatorError("Missing or empty OperationOutcome.issue")
    errors, warnings = [], []
    for issue in issues:
        if not isinstance(issue, dict) or issue.get("severity") not in (
                "fatal", "error", "warning", "information", "success"):
            raise ValidatorError("Malformed OperationOutcome issue")
        if issue.get("code") in ("exception", "timeout", "incomplete", "throttled"):
            raise ValidatorError("Validator reported an incomplete or failed validation")
        locations = issue.get("expression") or issue.get("location") or []
        message = (issue.get("diagnostics") or issue.get("details", {}).get("text")
                   or issue.get("code", "Validation issue"))
        formatted = f"{', '.join(locations)}: {message}"
        if issue["severity"] in ("fatal", "error"):
            errors.append(formatted)
        elif issue["severity"] == "warning":
            warnings.append(formatted)
    return ValidationResult(tuple(errors), tuple(warnings))


class FHIRValidator:
    def __init__(self, jar_path: Path = ROOT / "validator" / "validator_cli.jar",
                 timeout: float = 180, cache_dir: Path = ROOT / ".cache" / "fhir-validator"):
        self.jar_path = Path(jar_path).resolve()
        self.timeout = timeout
        self.cache_dir = Path(cache_dir).resolve()

    def preflight(self) -> None:
        if not self.jar_path.is_file():
            raise ValidatorError(f"Validator JAR not found: {self.jar_path}")
        if not shutil.which("java"):
            raise ValidatorError("Java is not installed or not on PATH")

    def validate(self, resource: dict) -> ValidationResult:
        self.preflight()
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="fhir-validation-") as temporary:
            folder = Path(temporary)
            source, output = folder / "resource.json", folder / "outcome.json"
            source.write_text(json.dumps(resource, ensure_ascii=False), encoding="utf-8")
            command = ["java", f"-Duser.home={self.cache_dir}", "-jar", str(self.jar_path),
                       str(source), "-version", "4.0.1", "-tx", "n/a",
                       "-allow-example-urls", "true", "-output", str(output)]
            try:
                process = subprocess.run(command, cwd=folder, capture_output=True,
                                         text=True, timeout=self.timeout)
            except subprocess.TimeoutExpired as exc:
                raise ValidatorError(f"Validator exceeded {self.timeout} seconds") from exc
            except OSError as exc:
                raise ValidatorError("Could not launch the validator") from exc
            if not output.is_file():
                raise ValidatorError(f"Validator produced no report (exit {process.returncode})")
            try:
                result = parse_outcome(json.loads(output.read_text(encoding="utf-8")))
            except (ValueError, TypeError, KeyError) as exc:
                raise ValidatorError("Could not parse the validator report") from exc
            # CLI versions may use exit 1 for an invalid resource. A clean report
            # combined with a nonzero exit is not a trustworthy successful run.
            if process.returncode not in (0, 1) or (process.returncode != 0 and result.valid):
                raise ValidatorError(f"Unexpected validator exit: {process.returncode}")
            return result

    def validate_output(self, payload: dict, scope: str) -> ValidationResult:
        """Check the agent contract, then validate resources in a Bundle context.

        Specialists other than Patient get a temporary reference target only;
        their original output is never mutated or augmented.
        """
        errors = []
        if scope in SCOPES:
            resources = payload.get("resources")
            allowed = SCOPES[scope]
        else:
            if payload.get("resourceType") != "Bundle" or payload.get("type") != "collection":
                return ValidationResult(("Return a Bundle with type collection.",))
            entries = payload.get("entry")
            if not isinstance(entries, list) or any(not isinstance(e, dict) for e in entries):
                return ValidationResult(("Bundle.entry must be a list of entries.",))
            resources = [entry.get("resource") for entry in entries]
            allowed = ALLOWED
        if not isinstance(resources, list) or any(not isinstance(r, dict) for r in resources):
            return ValidationResult(("Resources must be a list of JSON resource objects.",))
        if any(r.get("resourceType") not in allowed for r in resources):
            errors.append(f"Only these resource types are allowed: {', '.join(sorted(allowed))}.")
        patients = [r for r in resources if r.get("resourceType") == "Patient"]
        if scope == "patient" or scope not in SCOPES:
            if len(patients) != 1 or patients[0].get("id") != "patient-1":
                errors.append('Include exactly one Patient with id "patient-1".')
        for resource in resources:
            if resource.get("resourceType") != "Patient":
                subject = resource.get("subject")
                if not isinstance(subject, dict) or subject.get("reference") != "Patient/patient-1":
                    errors.append('Every non-Patient resource must reference Patient/patient-1 in subject.')
        if errors:
            return ValidationResult(tuple(errors))
        if scope not in SCOPES:
            return self.validate(payload)
        context = list(resources)
        if scope != "patient":
            context.insert(0, {"resourceType": "Patient", "id": "patient-1"})
        entries = []
        for resource in context:
            entry = {"resource": resource}
            if resource.get("id"):
                entry["fullUrl"] = f"https://example.org/fhir/{resource['resourceType']}/{resource['id']}"
            entries.append(entry)
        return self.validate({"resourceType": "Bundle", "type": "collection", "entry": entries})
