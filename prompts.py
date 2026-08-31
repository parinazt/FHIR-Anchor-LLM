"""Build the two controlled inputs for FHIR Bundle generation.

The experiment has one shared conversion prompt. Only the presentation of the
manually selected facts changes:

* unanchored: the facts are plain bullet points;
* anchored: the same facts appear under FHIR resource headings.

Never edit one arm independently. ``build_summary`` derives both arms from the same
annotation JSON so their clinical content stays identical.
"""

from __future__ import annotations

from typing import Any


PROMPT_VERSION = "2.0"
FHIR_VERSION = "4.0.1"

# These are the only resource types allowed inside Bundle.entry.resource.
# Bundle is the container and is therefore not included in this tuple.
ANCHORS = (
    "Patient",
    "Condition",
    "Observation",
    "MedicationStatement",
    "MedicationRequest",
)


# This prompt is intentionally identical for both experiment arms. It mentions the
# five permitted resource types in both arms, because both outputs must solve the
# same limited FHIR task. The intervention is whether individual facts are already
# attached to those resource labels in the input summary.
CONVERSION_INSTRUCTIONS = """Convert the supplied synthetic clinical summary into one HL7 FHIR R4 (version 4.0.1) JSON Bundle.

OUTPUT RULES
1. Return only the Bundle JSON object. Do not use Markdown or explanatory prose.
2. The root must have resourceType "Bundle" and type "collection".
3. Bundle entries may contain only Patient, Condition, Observation, MedicationStatement, and MedicationRequest resources.
4. Include exactly one Patient resource with id "patient-1".
5. Give every resource a unique, readable id. Give every entry a fullUrl in the form "https://example.org/fhir/<ResourceType>/<id>".
6. Every non-Patient resource must reference "Patient/patient-1" in its subject.reference.
7. Use only clinical facts explicitly present in the supplied summary. Do not infer missing diagnoses, dates, demographics, doses, identifiers, or terminology codes. For mandatory FHIR workflow fields, apply only the deterministic mappings below.
8. Do not invent SNOMED CT, LOINC, RxNorm, or other codes. When no code was supplied, use CodeableConcept.text only.
9. Omit a resource when the summary contains no supported fact for it.
10. Preserve negation, uncertainty, medication timing, and whether a medicine is current versus newly requested.

MINIMUM RESOURCE RULES
- Condition: include subject and code.text. Use clinicalStatus only when supported.
- Observation: include status "final", subject, code.text, and an appropriate value[x], component, or dataAbsentReason. "final" means the supplied finding is already recorded. Keep stated units and values unchanged.
- MedicationStatement: include subject and medicationCodeableConcept.text. Map explicitly current or occasional use to status "active"; explicitly stopped use to "stopped"; a completed course to "completed"; otherwise use "unknown".
- MedicationRequest: include intent "order", subject, and medicationCodeableConcept.text. Map an explicitly ordered, started, or prescribed medicine to status "active"; otherwise use "unknown".

Do not add resources merely to fill every permitted category. Structural FHIR validity does not excuse unsupported clinical content."""


def validate_annotation(annotation: dict[str, Any]) -> None:
    """Fail clearly when a manual annotation is incomplete or malformed."""

    required_top_level = {"note_id", "source_file", "review_status", "sections"}
    missing = required_top_level - annotation.keys()
    if missing:
        raise ValueError(f"Annotation is missing fields: {sorted(missing)}")

    if annotation["review_status"] != "ready":
        raise ValueError(
            f"Annotation {annotation['note_id']} is not ready. "
            'Set "review_status" to "ready" after clinician review.'
        )

    sections = annotation["sections"]
    if not isinstance(sections, dict):
        raise ValueError("'sections' must be a JSON object.")

    unexpected = set(sections) - set(ANCHORS)
    if unexpected:
        raise ValueError(f"Unsupported annotation sections: {sorted(unexpected)}")

    for anchor in ANCHORS:
        facts = sections.get(anchor)
        if not isinstance(facts, list):
            raise ValueError(f"Section '{anchor}' must be a JSON list.")
        if any(not isinstance(fact, str) or not fact.strip() for fact in facts):
            raise ValueError(f"Every fact in '{anchor}' must be a non-empty string.")

    if not any(sections[anchor] for anchor in ANCHORS):
        raise ValueError("The annotation contains no facts.")


def build_summary(annotation: dict[str, Any], anchored: bool) -> str:
    """Create anchored or unanchored text from exactly the same fact strings.

    Facts keep the same order in both arms. The anchored arm adds a heading before
    each non-empty section; the unanchored arm omits those headings. No fact is added,
    deleted, rephrased, or duplicated.
    """

    validate_annotation(annotation)
    lines: list[str] = []

    for anchor in ANCHORS:
        facts = annotation["sections"][anchor]
        if not facts:
            continue
        if anchored:
            lines.append(f"[{anchor}]")
        lines.extend(f"- {fact.strip()}" for fact in facts)
        lines.append("")

    return "\n".join(lines).strip()


def build_prompt(annotation: dict[str, Any], anchored: bool) -> str:
    """Combine the shared instructions with one derived summary presentation."""

    summary = build_summary(annotation, anchored)
    return (
        f"{CONVERSION_INSTRUCTIONS}\n\n"
        "BEGIN CLINICAL SUMMARY\n"
        f"{summary}\n"
        "END CLINICAL SUMMARY"
    )
