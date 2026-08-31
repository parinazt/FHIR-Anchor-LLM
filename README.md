
# FHIR-anchor experiment: two LLM Bundles from the same clinical facts

This repository implements the small, controlled study shown below:

```text
SynGP500 raw note
        |
        v
one clinician-created fact annotation
        |
        +--> headings removed --> LLM --> unanchored FHIR R4 Bundle
        |
        +--> FHIR headings kept -> LLM --> anchored FHIR R4 Bundle
                                      |
                                      v
                         official HL7 FHIR validator
```

The five permitted clinical resource types are `Patient`, `Condition`,
`Observation`, `MedicationStatement`, and `MedicationRequest`. `Bundle` is the
container. Both arms use the same model, clinical facts, fact order, FHIR version,
conversion instructions, and generation settings. The only intentional difference
is whether the facts arrive under FHIR resource headings.

The diagram's “colour coding” is implemented as explicit text labels such as
`[Observation]`. Colour itself is only visual formatting and is not a dependable
machine-readable prompt signal.

This design starts with FHIR from the beginning. It does **not** generate prose now
and hope to convert it later. Both LLM outputs are FHIR R4 JSON Bundles, and both go
through the official HL7 validator without repair. Invalid output is retained as an
experimental result.

## What “valid” means

`official_valid = true` means the HL7 validator found no `fatal` or `error` issue.
Warnings are reported but do not make an instance invalid. This only establishes
FHIR conformance. It does not establish that a diagnosis, dose, negation, or
resource choice is clinically correct. That requires blinded human review.

## Files you will use

| File | Purpose |
|---|---|
| `prepare_annotations.py` | Reproducibly samples notes and creates editable JSON templates |
| `docs/ANNOTATION_GUIDE.md` | Exactly what the clinician puts in each of five lists |
| `prompts.py` | Creates the two controlled inputs and the shared FHIR instructions |
| `pipeline.py` | Makes two independent LLM calls and saves both unchanged outputs |
| `setup_validator.py` | Downloads pinned official validator version 6.10.2 and checks it |
| `validate_bundles.py` | Validates every pair and creates CSV/JSON validity reports |
| `evaluate.py` | Creates masked A/B files for clinical review and scores the sheet |
| `docs/STUDY_ROADMAP.md` | Conference timetable, outcomes, and manual-hours estimate |
| `docs/CODE_WALKTHROUGH.md` | Beginner explanation of every code block and important line |

## Step 1 — install Python packages

Open PowerShell in this folder and run:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

The virtual environment keeps this study's package separate from other Python
projects. If activation is blocked, run
`Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` in that terminal and
activate again.

Java 11 or newer is also required by the official validator. Check it with:

```powershell
java -version
```

## Step 2 — download the 500 synthetic notes

```powershell
python download_syngp500.py
```

The script accepts the archive only when it contains exactly 500 text files. The
notes are synthetic, but you must still follow the upstream SynGP500 licence and
cite the dataset. Do not claim that results on these notes establish safety on real
patient data.

## Step 3 — make annotation templates

Start with five development notes:

```powershell
python prepare_annotations.py --limit 5 --seed 2026
```

This creates files under `annotations`. Each contains the raw note and five empty
lists. A clinically trained annotator reads the note and writes short atomic facts
into the appropriate list. Then a clinician checks the file and changes
`"review_status": "draft"` to `"ready"`.

Do not write two summaries manually. Write one annotation only. The software derives
both inputs from that one source, so no fact can accidentally be added to one arm.
Follow [the annotation guide](docs/ANNOTATION_GUIDE.md) before editing a template.

## Step 4 — inspect the experiment without an API call

```powershell
python pipeline.py --annotations-dir annotations --limit 1 --dry-run
```

The first input shows the facts as plain bullets. The second contains the exact same
bullets under headings such as `[Condition]`. Confirm that no fact was added,
deleted, or reworded. The common prompt mentions all five allowed resource types in
both arms because both arms must solve the same limited FHIR task.

## Step 5 — configure the model

Set the API key only in the current terminal:

```powershell
$env:OPENAI_API_KEY = "your-key"
$env:OPENAI_MODEL = "gpt-4.1-mini"
```

Do not place a key in source code, an annotation file, or Git. Freeze one model for
the main run. Changing the model or prompt creates a different experiment.

## Step 6 — generate one real pair

```powershell
python pipeline.py --annotations-dir annotations --output-dir outputs\bundles --limit 1
```

For each note, the folder contains:

- `anchored.raw.txt` and `unanchored.raw.txt`: unchanged API output;
- `anchored.bundle.json` and `unanchored.bundle.json`: pretty JSON when parsing worked;
- `metadata.json`: model, prompt version, hashes, call order, response IDs, and local checks.

There is no automatic repair or retry. Repairing one invalid answer would hide the
outcome you want to measure. `--overwrite` intentionally regenerates a pair; during
the main experiment, generate once and preserve the files.

## Step 7 — install and run the official validator

```powershell
python setup_validator.py
python validate_bundles.py --bundles-dir outputs\bundles
```

The setup pins validator 6.10.2 and caches the official R4 definitions. The first
run may take several minutes. The validator writes:

- `outputs\validation\results.csv`: one row per note and arm;
- `outputs\validation\summary.json`: both valid, anchored-only valid,
  unanchored-only valid, and neither valid;
- detailed `OperationOutcome` JSON and logs below `outputs\validation\details`.

The supplied live demonstration produced two parseable Bundles, and both passed the
official validator. It is only a technical smoke test, not evidence of an anchor
effect.

## Step 8 — prepare blinded clinical review

```powershell
python evaluate.py prepare --bundles-dir outputs\bundles --annotations-dir annotations
```

Give `outputs\review\annotations.csv` and the `blinded` folder to the reviewer. Keep
`outputs\review\key.csv` hidden until ratings are locked. The reviewer counts
reference-fact coverage, unsupported claims, and wrong resource types, then scores
faithfulness and mapping quality. Run:

```powershell
python evaluate.py score
```

## Step 9 — run the offline tests

```powershell
python -m unittest discover -s tests -v
```

The tests make no paid calls. They verify the controlled input difference, two-call
pipeline, output saving, local guards, validator report parsing, A/B masking, and
scoring. They cannot replace the one-pair live API and validator smoke tests.

## Recommended conference-sized study

Use 5–10 notes for development, then freeze prompt version 2.0 and the annotation
guide. A 20–30-note unseen pilot is realistic when time is short. Treat it as a
feasibility/pilot study rather than a powered claim of superiority. The primary
automatic outcome can be paired official-validity status; clinical correctness must
be a co-primary or important secondary human-reviewed outcome.

Read [the full roadmap](docs/STUDY_ROADMAP.md) before creating the final sample.

## Primary sources

- [SynGP500 repository](https://github.com/pisong314/syngp500)
- [HL7 FHIR R4 Bundle](https://hl7.org/fhir/R4/bundle.html)
- [HL7 FHIR R4 resources](https://hl7.org/fhir/R4/resourcelist.html)
- [Official HL7 validator repository](https://github.com/hapifhir/org.hl7.fhir.core)
- [OpenAI API quickstart](https://developers.openai.com/api/docs/quickstart)
- [OpenAI Structured Outputs guide](https://developers.openai.com/api/docs/guides/structured-outputs/)
