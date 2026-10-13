"""Conservative, non-LLM checks of FHIR clinical facts against their source note.

Every nontechnical field is inventoried. Unsupported fields stay not_verifiable;
this is a partial evidence checker, not a complete clinical correctness oracle.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

VERSION = "1.3"
STATUSES = ("supported", "partial_match", "contradicted", "not_verifiable")
ROOT = Path(__file__).resolve().parent

# Explicit equivalence rules; expand only on development notes and freeze before evaluation.
ALIASES = {
    "weight_change": ["weight change", "weight loss", "weight gain", "wt loss", "wt gain"],
    "heart_rate": ["heart rate", "pulse rate", "hr"],
    "oxygen_saturation": ["oxygen saturation", "spo2", "o2 saturation"],
    "temperature": ["temperature", "temp"],
    "respiratory_rate": ["respiratory rate", "rr"],
    "systolic": ["blood pressure systolic", "systolic blood pressure", "systolic bp"],
    "diastolic": ["blood pressure diastolic", "diastolic blood pressure", "diastolic bp"],
    "blood_pressure": ["blood pressure", "bp"],
    "postnatal_depression": ["postnatal depression", "postpartum depression", "pnd"],
    "generalized_anxiety_disorder": ["generalized anxiety disorder", "generalised anxiety disorder", "gad"],
    "passive_suicidal_ideation": ["passive suicidal ideation", "passive si", "si passive"],
    "psychosis": ["psychosis", "psychotic symptoms", "psychotic sx"],
    "panic_attacks": ["panic attacks", "panic attack"],
    "panic_frequency": ["panic attack frequency", "panic attacks frequency"],
    "panic_duration": ["panic attack duration", "panic attacks duration"],
}
MEASUREMENTS = {
    "heart_rate": r"(?:heart rate|pulse rate|hr)\s*[:=]?\s*(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>bpm|beats/min(?:ute)?|/min)?",
    "oxygen_saturation": r"(?:oxygen saturation|spo2|o2 saturation)\s*[:=]?\s*(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>%)?",
    "respiratory_rate": r"(?:respiratory rate|rr)\s*[:=]?\s*(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>/min|breaths/min(?:ute)?)?",
    "temperature": r"(?:temperature|temp)\s*[:=]?\s*(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>°?c|°?f|celsius|fahrenheit)\b",
}
IMPLIED_UNITS = {"heart_rate": "beats/min", "oxygen_saturation": "%", "respiratory_rate": "breaths/min"}
TECHNICAL = {"resourceType", "id", "meta", "implicitRules", "language", "text", "fullUrl"}
UNITS = {
    "1/wk": ("events_per_week", 1),
    "kg": ("mass", 1000), "g": ("mass", 1), "mg": ("mass", .001),
    "mcg": ("mass", .000001), "ug": ("mass", .000001),
    "%": ("percent", 1), "mmhg": ("pressure", 1),
    "beats/min": ("heart_rate", 1), "beats/minute": ("heart_rate", 1),
    "bpm": ("heart_rate", 1), "breaths/min": ("respiratory_rate", 1),
    "/min": ("per_minute", 1), "1/min": ("per_minute", 1),
    "min": ("duration", 60), "minute": ("duration", 60), "minutes": ("duration", 60),
    "h": ("duration", 3600), "hours": ("duration", 3600),
    "d": ("duration", 86400), "days": ("duration", 86400),
    "wk": ("duration", 604800), "weeks": ("duration", 604800),
    "c": ("temperature_c", 1), "°c": ("temperature_c", 1),
    "cel": ("temperature_c", 1), "degrees celsius": ("temperature_c", 1),
    "celsius": ("temperature_c", 1), "f": ("temperature_f", 1),
    "°f": ("temperature_f", 1), "fahrenheit": ("temperature_f", 1),
}


def normalize(text: str) -> str:
    text = str(text).lower().replace("–", "-").replace("−", "-").replace("µ", "u")
    return " ".join(re.findall(r"[\w]+|[<>~%+-]", text))


def phrase_pattern(phrase: str) -> str:
    """Explicit lexical equivalences; keep numbers, relations, and negation intact."""
    phrase = phrase.strip().strip('"\'“”‘’').lower()
    phrase = re.sub(r"\b(?:moderate\s+to\s+severe|mod(?:erate)?\s*[-–—]\s*severe)\b",
                    "severityplaceholder", phrase)
    phrase = re.sub(r"\bego[-–—\s]*dystonic\b", "egoplaceholder", phrase)
    tokens = re.findall(r"\w+|[^\w\s()\"'“”‘’]", phrase)
    patterns = []
    for token in tokens:
        if token == 'severityplaceholder':
            patterns.append(r"(?:moderate\s+to\s+severe|mod(?:erate)?\s*[-–—]\s*severe)")
        elif token == 'egoplaceholder':
            patterns.append(r"ego[-–—\s]*dystonic")
        else:
            patterns.append(re.escape(token))
    return r"(?<!\w)" + r"[\s()\"'“”‘’]*".join(patterns) + r"(?!\w)"


def concept_key(label: str) -> str:
    label = normalize(label)
    for key, aliases in ALIASES.items():
        if label in [normalize(a) for a in aliases]:
            return key
    return label


def display(concept: Any) -> str:
    if not isinstance(concept, dict):
        return ""
    if concept.get("text"):
        return str(concept["text"])
    return next((str(c["display"]) for c in concept.get("coding", [])
                 if isinstance(c, dict) and c.get("display")), "")


@dataclass
class Fact:
    resource_type: str
    resource_id: str
    path: str
    kind: str
    concept: str
    expected: Any


@dataclass
class Finding:
    fact: Fact
    status: str
    reason: str
    observed: Any = None
    evidence: str = ""
    evidence_start: int | None = None
    evidence_end: int | None = None


@dataclass
class Report:
    findings: list[Finding]
    excluded_fields: int

    def summary(self) -> dict:
        counts = {status: sum(f.status == status for f in self.findings) for status in STATUSES}
        total = len(self.findings)
        assessed = total - counts["not_verifiable"]
        return {"checker_version": VERSION, "total_checks": total, **counts,
                "excluded_technical_fields": self.excluded_fields,
                "assessment_coverage": assessed / total if total else None,
                "supported_fraction_of_all_checks": counts["supported"] / total if total else None}


class SemanticValidator:
    """Inventory all clinical fields; apply explicit rules when available."""

    def extract_facts(self, bundle: dict) -> tuple[list[Fact], int]:
        if not isinstance(bundle, dict) or bundle.get("resourceType") != "Bundle":
            raise ValueError("Expected a FHIR Bundle")
        entries = bundle.get("entry")
        if not isinstance(entries, list):
            raise ValueError("Bundle.entry must be a list")
        facts, excluded = [], 0
        for i, entry in enumerate(entries):
            resource = entry.get("resource") if isinstance(entry, dict) else None
            if not isinstance(resource, dict):
                raise ValueError(f"Missing resource at Bundle.entry[{i}]")
            resource_type, resource_id = resource.get("resourceType", "unknown"), resource.get("id", "")
            root = f"entry[{i}].resource"
            label = display(resource.get("code")) or display(resource.get("medicationCodeableConcept"))

            def visit(value, path, key, concept):
                nonlocal excluded
                if (path == f"{root}.{key}" and key in TECHNICAL) or key in (
                        "reference", "id", "system", "version", "userSelected"):
                    excluded += 1
                    return
                # Terminology identifiers and their labels are not separate source facts.
                # A quantity's code remains inside the grouped value for unit interpretation.
                if key == "coding" or (key == "code" and not isinstance(value, (dict, list))):
                    excluded += 1
                    return
                # Extension URLs identify the schema, not a claim in the note.
                if key == "url" and re.search(r"\.(?:extension|modifierExtension)\[\d+\]\.url$", path):
                    excluded += 1
                    return
                # These are deterministic workflow defaults, not source claims.
                if path == f"{root}.status" and resource_type == "Observation":
                    excluded += 1
                    return
                if resource_type == "MedicationRequest" and path in (f"{root}.intent", f"{root}.status"):
                    excluded += 1
                    return
                if isinstance(value, dict):
                    if key.endswith("Quantity") or key == "quantity":
                        facts.append(Fact(resource_type, resource_id, path, "quantity", concept, value))
                        # Retain unexpected nested/extra fields as separate unsupported checks.
                        for subkey, child in value.items():
                            if subkey not in ("value", "unit", "system", "code", "comparator"):
                                visit(child, f"{path}.{subkey}", subkey, concept)
                        return
                    if key == "valueRange":
                        facts.append(Fact(resource_type, resource_id, path, "range", concept, value))
                        return
                    if key == "repeat":
                        facts.append(Fact(resource_type, resource_id, path, "timing", concept, value))
                        return
                    if key == "code" and path.startswith(f"{root}.component["):
                        concept = display(value)
                    child_concept = display(value.get("code")) or concept
                    for subkey, child in value.items():
                        if resource_type == "Observation" and subkey == "code":
                            # The label belongs to the sibling value/component checks.
                            # Never score label presence as evidence for a measurement.
                            continue
                        visit(child, f"{path}.{subkey}", subkey, child_concept)
                elif isinstance(value, list):
                    for j, child in enumerate(value):
                        visit(child, f"{path}[{j}]", key, concept)
                else:
                    kind = "unsupported"
                    if key in ("text", "display", "valueString"):
                        kind = "text"
                    if resource_type == "Patient" and key == "gender":
                        kind = "gender"
                    if resource_type == "Patient" and key == "birthDate":
                        kind = "birth_date"
                    if resource_type == "Observation" and key == "valueBoolean":
                        kind = "boolean"
                    if resource_type == "MedicationStatement" and path == f"{root}.status":
                        kind = "medication_status"
                    facts.append(Fact(resource_type, resource_id, path, kind, concept, value))
                    # Keep the entire assertion as a check, and additionally test
                    # recoverable numeric subclaims without claiming the prose is verified.
                    if key == "valueString" and isinstance(value, str) and re.search(
                            r"panic attacks?", concept + " " + value, re.I):
                        for numeric_kind, pattern, numeric_unit in (
                            ("Panic attack frequency", r"(\d+)(?:\s*[-–]\s*(\d+))?\s*(?:panic attacks?\s*)?(?:per\s+week|/wk)", "1/wk"),
                            ("Panic attack duration", r"(\d+)(?:\s*[-–]\s*(\d+))?\s*(?:minutes?|min)\b", "min"),
                        ):
                            for match in re.finditer(pattern, value, re.I):
                                interval = {"low": {"value": int(match[1]), "unit": numeric_unit},
                                            "high": {"value": int(match[2] or match[1]), "unit": numeric_unit}}
                                facts.append(Fact(resource_type, resource_id, path, "range", numeric_kind, interval))
            visit(resource, root, "resource", label)
        return facts, excluded

    def evaluate(self, bundle: dict, source: str) -> Report:
        if not isinstance(source, str) or not source.strip():
            raise ValueError("Source note must not be empty")
        facts, excluded = self.extract_facts(bundle)
        return Report([self.check(fact, source) for fact in facts], excluded)

    @staticmethod
    def finding(fact, status, reason, source, match=None, observed=None):
        if match is None:
            return Finding(fact, status, reason, observed)
        start, end = match.span()
        return Finding(fact, status, reason, observed, source[start:end], start, end)

    @staticmethod
    def context(source, match) -> tuple[str, bool, bool]:
        start = max(source.rfind("\n", 0, match.start()), source.rfind(";", 0, match.start()),
                    source.rfind(",", 0, match.start())) + 1
        prefix = source[start:match.start()].lower()
        negated = bool(re.search(r"\b(?:no|not|denies|without|denied)\s+(?:\w+\s+){0,2}$", prefix))
        uncertain = bool(re.search(r"\b(?:if|possible|suspected|consider|rule out|family history|mother|father)\b", prefix))
        # Historic or conditional contexts cannot establish a current assertion.
        uncertain = uncertain or bool(re.search(r"\b(?:history of|previous|previously|prior|prev|old)\b", prefix))
        return prefix, negated, uncertain

    def check(self, fact: Fact, source: str) -> Finding:
        if fact.kind in ("quantity", "range"):
            return self.check_quantity(fact, source)
        if fact.kind == "gender":
            pattern = r"(?im)^\s*(?:\d{1,3}\s*(?P<short>[FM])\b|(?:sex|gender)\s*[:=]\s*(?P<long>female|male|other|unknown)\b)"
            matches = list(re.finditer(pattern, source))
            values = [(m, {'f':'female','m':'male'}.get((m['short'] or '').lower(), (m['long'] or '').lower())) for m in matches]
            if len(set(v for _, v in values)) == 1:
                match, value = values[0]
                return self.finding(fact, "supported" if value == fact.expected else "contradicted",
                                    "Compared explicit demographic notation.", source, match, value)
        if fact.kind == "medication_status":
            return self.check_medication_status(fact, source)
        if fact.kind in ("text", "boolean"):
            return self.check_text(fact, source)
        return Finding(fact, "not_verifiable", "No reliable rule for this field or no unambiguous evidence.")

    def check_text(self, fact, source):
        target = (str(fact.expected) if fact.kind == "text" else fact.concept).strip().strip('"\'“”‘’')
        key = concept_key(target)
        phrases = ALIASES.get(key, [target])
        # Do not let coding labels such as 'Active' match unrelated source content.
        if ".coding[" in fact.path:
            return Finding(fact, "not_verifiable", "Terminology display requires code/context verification.")
        if not target.strip() or (len(target) < 4 and key not in ALIASES):
            return Finding(fact, "not_verifiable", "Label too short or ambiguous for lexical verification.")
        matches = []
        for phrase in phrases:
            pattern = phrase_pattern(phrase)
            matches.extend(re.finditer(pattern, source, flags=re.I))
        verdicts = []
        for match in matches:
            _, negated, uncertain = self.context(source, match)
            expected_negative = fact.kind == "boolean" and fact.expected is False
            # Full-phrase matching preserves internal negation verbatim.
            # Uncertain/historical assertions still require event interpretation.
            if re.search(r"\b(?:possible|history)\b", target, re.I):
                uncertain = True
            if uncertain:
                verdict = "not_verifiable"
            elif negated == expected_negative:
                verdict = "supported"
            else:
                verdict = "contradicted"
            verdicts.append((verdict, match))
        statuses = {v for v, _ in verdicts}
        if len(statuses) == 1 and "not_verifiable" not in statuses:
            verdict, match = verdicts[0]
            prefix, negated, _ = self.context(source, match)
            finding = self.finding(fact, verdict, "Lexical concept match with local assertion context; temporal/event equivalence is not established.", source, match,
                                  {"negated": negated})
            # Include the modifier in evidence, not just the concept name.
            if negated:
                finding.evidence_start = max(0, match.start() - len(prefix))
                finding.evidence = source[finding.evidence_start:match.end()]
            return finding
        return Finding(fact, "not_verifiable", "No exact/registered synonym match, or multiple/uncertain contexts.")

    def check_medication_status(self, fact, source):
        if re.search(r"[;,]|\band\b", fact.concept, re.I):
            return Finding(fact, "not_verifiable", "Compound medication label requires event disambiguation.")
        name = re.match(r"[A-Za-z][A-Za-z-]+", fact.concept)
        if not name:
            return Finding(fact, "not_verifiable", "No medication label.")
        candidates = []
        for match in re.finditer(r"(?im)^.*\b" + re.escape(name[0]) + r"\b.*$", source):
            line = match[0].lower()
            if re.search(r"[;,]|\b(?:and|not|never|start|prescribe|renew|if|consider)\b", line):
                continue
            if re.search(r"\b(?:stopped|ceased|discontinued|self-ceased)\b", line):
                status = "stopped"
            elif re.search(r"\bcompleted\b", line):
                status = "completed"
            elif re.search(r"\b(?:currently takes|currently taking|continues taking)\b", line):
                status = "active"
            else:
                continue
            candidates.append((match, status))
        if len(candidates) == 1:
            match, status = candidates[0]
            return self.finding(fact, "supported" if status == fact.expected else "contradicted",
                                "Explicit medication-use status in one source line.", source, match, status)
        return Finding(fact, "not_verifiable", "Medication status is not explicit or multiple events are possible.")

    def quantity_candidates(self, fact, source):
        key = concept_key(fact.concept)
        if key in MEASUREMENTS:
            for match in re.finditer(r"\b" + MEASUREMENTS[key], source, re.I):
                value = float(match['value'])
                yield match, value, value, match['unit'] or IMPLIED_UNITS.get(key, ''), False, '='
        elif key in ("systolic", "diastolic"):
            for match in re.finditer(r"\b(?:bp|blood pressure)\s*[:=]?\s*(\d+)\s*/\s*(\d+)\s*(mmHg)?", source, re.I):
                value = float(match[1 if key == 'systolic' else 2])
                yield match, value, value, 'mmHg', False, '='
        elif key == "weight_change":
            pattern = r"(?P<approx>~|approximately\s+|about\s+)?(?P<low>\d+(?:\.\d+)?)(?:\s*[-–]\s*(?P<high>\d+(?:\.\d+)?))?\s*(?P<unit>kg|g)\s*(?:wt|weight)\s*(?P<direction>loss|gain)\b"
            for match in re.finditer(pattern, source, re.I):
                low, high = float(match['low']), float(match['high'] or match['low'])
                if match['direction'].lower() == 'loss':
                    low, high = -high, -low
                yield match, low, high, match['unit'], bool(match['approx']), '='
            pattern = r"\b(?P<direction>lost|gained)\s*(?P<approx>~|about\s+|approximately\s+)?(?P<low>\d+(?:\.\d+)?)(?:\s*[-–]\s*(?P<high>\d+(?:\.\d+)?))?\s*(?P<unit>kg|g)\b"
            for match in re.finditer(pattern, source, re.I):
                low, high = float(match['low']), float(match['high'] or match['low'])
                if match['direction'].lower() == 'lost':
                    low, high = -high, -low
                yield match, low, high, match['unit'], bool(match['approx']), '='
        elif key in ("panic_frequency", "panic_duration"):
            for line in re.finditer(r"(?im)^.*\bpanic attacks?\b.*$", source):
                # Restrict matching to this event's clause, not the full note.
                tail = source[line.start():line.end()]
                pattern = (r"(\d+)(?:\s*[-–]\s*(\d+))?\s*(?:panic attacks?\s*)?(?:/wk|per\s+week)"
                           if key == "panic_frequency" else
                           r"(\d+)(?:\s*[-–]\s*(\d+))?\s*(?:minutes?|min)\b")
                for found in re.finditer(pattern, tail, re.I):
                    # Re-match at its absolute source offset to retain evidence offsets.
                    match = re.compile(pattern, re.I).match(source, line.start() + found.start())
                    low, high = float(match[1]), float(match[2] or match[1])
                    yield match, low, high, '1/wk' if key == 'panic_frequency' else 'min', False, '='
        elif fact.resource_type in ("MedicationStatement", "MedicationRequest") and "dose" in fact.path.lower():
            drug = re.match(r"[A-Za-z][A-Za-z-]+", fact.concept)
            if drug:
                pattern = (r"\b" + re.escape(drug[0]) +
                           r"\s+(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>mg|mcg|g)\b")
                for match in re.finditer(pattern, source, re.I):
                    value = float(match['value'])
                    # A later dose in the same clause signals titration or another
                    # event; do not accept the first dose for every dosage step.
                    end = source.find('\n', match.end())
                    suffix = source[match.end():end if end >= 0 else len(source)]
                    if re.search(r"\d+(?:\.\d+)?\s*(?:mg|mcg|g)\b", suffix, re.I):
                        yield match, value, value, '', False, '='
                    else:
                        yield match, value, value, match['unit'], False, '='

    def check_quantity(self, fact, source):
        expected = fact.expected
        if fact.kind == "range":
            if set(expected) - {'low', 'high'}:
                return Finding(fact, 'not_verifiable', 'Range contains unsupported qualifiers.')
            low, high = expected.get('low', {}), expected.get('high', {})
            if low.get('unit') != high.get('unit'):
                return Finding(fact, "not_verifiable", "Range units differ or are not supplied.")
            if low.get('comparator') or high.get('comparator'):
                return Finding(fact, 'not_verifiable', 'Range-bound comparators are unsupported.')
            values = [low.get('value'), high.get('value')]
            unit = low.get('unit') or low.get('code', '')
            comparator = '='
        else:
            values = [expected.get('value')] * 2
            unit = expected.get('unit') or expected.get('code', '')
            comparator = expected.get('comparator', '=')
        if any(not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v) for v in values):
            return Finding(fact, "not_verifiable", "Missing or nonnumeric quantity.")
        # A contradictory code/unit pair must not be silently accepted.
        if fact.kind == 'quantity' and expected.get('unit') and expected.get('code'):
            if UNITS.get(str(expected['unit']).lower()) != UNITS.get(str(expected['code']).lower()):
                return Finding(fact, "not_verifiable", "Unit label/code require terminology reconciliation.")
        expected_unit = UNITS.get(str(unit).lower())
        if expected_unit is None:
            return Finding(fact, "not_verifiable", "Unsupported unit.")
        candidates = list(self.quantity_candidates(fact, source))
        if len(candidates) != 1:
            return Finding(fact, "not_verifiable", "No unique concept-linked quantity; multiple events are not disambiguated.")
        match, low, high, observed_unit, approximate, relation = candidates[0]
        _, negated, uncertain = self.context(source, match)
        if negated or uncertain:
            return self.finding(fact, "not_verifiable", "Quantity appears in a negated, historical, or uncertain context.", source, match)
        parsed_unit = UNITS.get(observed_unit.lower())
        if parsed_unit is None or parsed_unit[0] != expected_unit[0]:
            return self.finding(fact, "not_verifiable", "Units cannot be compared by a registered conversion.", source, match)
        a, b = [v * expected_unit[1] for v in values]
        lo, hi = low * parsed_unit[1], high * parsed_unit[1]
        # 'Weight loss' encodes direction in the label; 'Weight change' requires a sign.
        if normalize(fact.concept) in ('weight loss', 'wt loss'):
            a, b = -b, -a
        observed = {'minimum': low, 'maximum': high, 'unit': observed_unit,
                    'approximate': approximate, 'comparator': relation}
        if values[0] > values[1] or low > high:
            return Finding(fact, 'not_verifiable', 'Reversed range bounds.')
        if comparator != relation:
            status, reason = 'contradicted', 'Comparator does not preserve the source relation.'
        elif abs(a-lo) < 1e-8 and abs(b-hi) < 1e-8:
            status, reason = ('partial_match', 'Source approximation is not retained.') if approximate else ('supported', 'Concept-linked quantity and unit match.')
        elif lo <= a <= b <= hi:
            status, reason = 'partial_match', 'Value is inside the source interval but adds unsupported precision.'
        elif b >= lo and a <= hi:
            status, reason = 'partial_match', 'Intervals overlap but are not equivalent.'
        else:
            status, reason = 'contradicted', 'Value or direction differs from the source quantity.'
        return self.finding(fact, status, reason, source, match, observed)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument('--results-dir', type=Path, help='Mode/model directory containing outputs/')
    inputs.add_argument('--bundle', type=Path, help='One Bundle JSON')
    parser.add_argument('--source', type=Path, help='Required with --bundle')
    parser.add_argument('--dataset-dir', type=Path, default=ROOT / 'dataset')
    parser.add_argument('--report-dir', type=Path, help='Defaults to results directory or bundle parent')
    args = parser.parse_args()
    if args.bundle and not args.source:
        parser.error('--source is required with --bundle')
    tasks = []
    if args.bundle:
        tasks = [(args.bundle, args.source, '')]
    else:
        for bundle in sorted((args.results_dir / 'outputs').glob('*.json')):
            match = re.fullmatch(r'(\d+)_(.+)\.json', bundle.name)
            if not match:
                parser.error(f'Unexpected output filename: {bundle.name}')
            tasks.append((bundle, args.dataset_dir / f'{match[2]}.txt', match[1]))
    if not tasks:
        parser.error('No Bundle files found')
    report_dir = args.report_dir or args.results_dir or args.bundle.parent
    report_dir.mkdir(parents=True, exist_ok=True)
    checker = SemanticValidator()
    fact_fields = ['file', 'run-id', 'resource_type', 'resource_id', 'path', 'kind', 'concept',
                   'expected', 'status', 'reason', 'observed', 'evidence', 'evidence_start', 'evidence_end']
    summary_fields = ['file', 'run-id', 'evaluation_status', 'checker_version', 'total_checks', *STATUSES,
                      'excluded_technical_fields', 'assessment_coverage', 'supported_fraction_of_all_checks']
    failed = False
    with (report_dir / 'semantic_facts.csv').open('w', encoding='utf-8', newline='') as fact_file, \
         (report_dir / 'semantic_summary.csv').open('w', encoding='utf-8', newline='') as summary_file:
        facts_writer = csv.DictWriter(fact_file, fieldnames=fact_fields)
        summary_writer = csv.DictWriter(summary_file, fieldnames=summary_fields)
        facts_writer.writeheader()
        summary_writer.writeheader()
        for bundle_path, source_path, run_id in tasks:
            try:
                source = source_path.read_text(encoding='utf-8')
                report = checker.evaluate(json.loads(bundle_path.read_text(encoding='utf-8')), source)
                for finding in report.findings:
                    row = asdict(finding)
                    row.update(row.pop('fact'))
                    for key in ('expected', 'observed'):
                        row[key] = row[key] if isinstance(row[key], str) else json.dumps(row[key], ensure_ascii=False)
                    facts_writer.writerow({'file': source_path.name, 'run-id': run_id, **row})
                summary_writer.writerow({'file': source_path.name, 'run-id': run_id,
                                         'evaluation_status': 'completed', **report.summary()})
                print(f'{bundle_path.name}: {report.summary()}')
            except (OSError, ValueError, TypeError, AttributeError) as exc:
                failed = True
                summary_writer.writerow({'file': source_path.name, 'run-id': run_id,
                                         'evaluation_status': f'failed:{type(exc).__name__}', 'checker_version': VERSION})
                print(f'Could not evaluate {bundle_path.name}: {type(exc).__name__}')
    print(f'Semantic reports saved in {report_dir}; no Bundle files were modified.')
    if failed:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
