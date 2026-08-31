# Manual annotation guide

## Your job in one sentence

Read one raw SynGP500 note and create one short, faithful list of atomic facts that
can be represented by the five permitted FHIR resource types. Do not create the two
experimental inputs yourself; the code creates both from this one annotation.

## Why one annotation is essential

If a human wrote an anchored summary and then separately wrote an ordinary summary,
the wording and content would inevitably differ. That would confound the experiment.
Here every fact string occurs once in JSON. The anchored arm adds resource headings;
the unanchored arm removes only those headings. Fact wording and order stay fixed.

This still has one limitation: facts remain in resource-group order after headings
are removed. Report that residual ordering cue in the conference limitations.

## Before you begin

Use a clinically trained annotator and have a clinician review every annotation.
For a very small pilot, the same clinician may do both, but record that there was no
independent input review. Calibrate on 3–5 development notes before freezing rules.

Never infer a fact because it is medically likely. The synthetic note is the only
source of truth. Preserve uncertainty, negation, timing, dose, route, and frequency.
Do not invent SNOMED CT, LOINC, RxNorm, dates, or patient identifiers.

## The template

An annotation file has this shape:

```json
{
  "note_id": "example-note",
  "source_file": "example-note.txt",
  "review_status": "draft",
  "source_note": "The complete source note is here...",
  "sections": {
    "Patient": [],
    "Condition": [],
    "Observation": [],
    "MedicationStatement": [],
    "MedicationRequest": []
  }
}
```

Edit only the five lists and, after review, `review_status`. Keep `source_note`
unchanged so the annotation remains auditable.

## What counts as one atomic fact

An atomic fact can be checked independently. Split this sentence:

> Diabetes is stable and metformin 500 mg twice daily was continued.

into:

```json
"Condition": ["Diabetes described as stable"],
"MedicationStatement": ["Currently takes metformin 500 mg twice daily"]
```

Do not split a measurement from its value or unit. `"Blood pressure 120/80 mmHg"`
is one usable Observation fact. Do not duplicate one fact in multiple sections.

## Exact meaning of each section

### Patient

Use for facts that are truly Patient demographics or identifiers, for example:

- `"Administrative gender female"`
- `"Date of birth 1984-03-12"` only when the exact date is stated

Do not convert an approximate age into an invented birth date. Do not add a name or
identifier merely because the Bundle requires a Patient; the prompt creates the
technical `patient-1` identity.

### Condition

Use for a diagnosis, problem, or clinically assessed state belonging to the patient:

- `"Type 2 diabetes mellitus described as active"`
- `"Possible viral upper respiratory infection"`
- `"History of postnatal depression"`

Keep qualifiers such as possible, suspected, history of, resolved, active, and
denied. Avoid using Condition for a single laboratory result or vital sign.

### Observation

Use for measurements, examination findings, symptoms recorded as findings, and
test results:

- `"Blood pressure 112/70 mmHg"`
- `"Heart rate 86 beats/min"`
- `"Oxygen saturation 99% on room air"`
- `"Reports nausea for three days"`

Keep the stated value, unit, body site, method, and date/time when present. Do not
convert `afebrile` into a numerical temperature. Do not add normal ranges.

### MedicationStatement

Use for medication use reported as current, occasional, past, stopped, or completed:

- `"Currently takes metformin 500 mg twice daily"`
- `"Occasionally takes paracetamol"`
- `"Stopped sertraline during pregnancy"`
- `"Completed seven days of amoxicillin"`

This category describes what the patient takes or took. Preserve dose, route,
frequency, adherence, and timing only when stated.

### MedicationRequest

Use for a medication newly prescribed, ordered, started, renewed, or explicitly
requested as the plan in this encounter:

- `"Start sertraline 25 mg each morning for 7 days, then 50 mg each morning"`
- `"Prescription renewed for salbutamol inhaler"`

This differs from MedicationStatement: it represents the prescriber's order, not
merely a history of use. One medicine may legitimately have both facts when the note
states both prior use and a new order, but the two strings must express those
different events.

## What to exclude in this limited experiment

Exclude facts that cannot be represented cleanly with the five permitted types:

- allergies and intolerances, because `AllergyIntolerance` is out of scope;
- procedures, referrals, appointments, care plans, and family history;
- administrative encounter details;
- vague prose that cannot be converted without guessing;
- a negated diagnosis when you cannot represent the negation without creating a
  misleading Condition;
- age when no exact birth date is available.

Write this restricted-content rule in the conference methods. You are evaluating a
limited FHIR extraction task, not complete note representation.

## Annotation procedure for every note

1. Read the entire note once without editing.
2. On the second reading, identify only facts that fit the five-resource scope.
3. Write short atomic fact strings in the correct lists.
4. Compare every string back to the exact source passage.
5. Check negation, certainty, temporality, dose, route, frequency, value, and unit.
6. Remove duplicates and facts that need inference.
7. Ask the clinician reviewer to check both inclusion and resource category.
8. Resolve disagreements and retain a short decision log for new edge cases.
9. Change `"review_status": "draft"` to `"ready"` only after review.
10. Run the pipeline dry-run and visually confirm that the two inputs contain the
    same facts.

## Quality-control checklist

Before marking `ready`, answer yes to every question:

- Is every fact explicitly supported by the raw note?
- Does each fact belong to exactly one intended resource event?
- Are negation and uncertainty unchanged?
- Are medication status and timing clear?
- Are all numbers and units copied exactly?
- Are allergies/procedures/referrals and other out-of-scope facts excluded?
- Were no terminology codes or dates invented?
- Is there at least one fact?

## Manual review of generated Bundles

Official validation does not answer these questions. In the blinded A/B package,
review each generated resource against `reference.json` and the raw note:

- `covered_reference_fact_count`: how many manual reference facts are represented;
- `unsupported_claim_count`: atomic claims absent from the reference/source;
- `wrong_resource_type_count`: facts mapped to the wrong one of the five types;
- `faithfulness_1_to_5`: correctness of meaning, values, timing, and negation;
- `mapping_quality_1_to_5`: appropriateness and completeness of FHIR representation.

Use 5 for essentially complete/correct, 4 for a minor issue, 3 for one material or
several minor issues, 2 for multiple material issues, and 1 for largely unreliable.
Choose A, B, or Tie only after scoring both independently. Keep the condition key
hidden. Ideally use two independent reviewers and preserve original scores before
adjudication.
