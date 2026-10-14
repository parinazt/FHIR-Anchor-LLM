# FHIR single-agent and multiagent experiments

Convert original clinical notes into FHIR R4 (4.0.1) Bundles and compare a
single-call baseline with four resource specialists followed by an LLM Merge
agent. Python 3.9+ and Java 17+ are required.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

A local `.env` file is provided. For a fresh clone, copy `.env.example` to `.env`.
Fill in the credentials for the provider you intend to use:

```dotenv
OPENAI_API_KEY=
OPENROUTER_API_KEY=
NUM_OF_EXECUTION=5
MODE=single-agent
MAX_ATTEMPTS=3
DEVELOPMENT=true
```

- `OPENAI_API_KEY`: OpenAI token.
- `OPENROUTER_API_KEY`: OpenRouter token.
- `NUM_OF_EXECUTION`: positive integer; number of complete passes over the input.
- `DEVELOPMENT`: `true` selects the first 10 `.txt` files in lexicographic filename
  order; `false` processes all notes. The same subset is reused for every execution
  and both workflows. An explicit input file is still processed by itself.
- `MODE`: exactly `single-agent` or `multiagent`.
- `MAX_ATTEMPTS`: maximum generations per agent, including the first (default 3).
  Set it to 1 for first-pass-only experiments; `--max-attempts` overrides it.

The loader finds `.env` relative to the project, regardless of the working
directory. Process environment variables override values in `.env`. Credentials
are excluded from configuration representations and experiment reports. `.env`
is ignored by Git; `.env.example` contains only blank credential placeholders.

## Run an experiment

```bash
python main.py --provider openai --model gpt-4.1-mini
```

With the supplied configuration, this runs the single-agent workflow five times
on the first 10 notes in `dataset/` (50 note executions). The directory currently
contains 500 notes. Set `DEVELOPMENT=false` to process all of them.
Select multiagent mode in `.env`, or override it for an invocation:

```bash
python main.py --provider openrouter --model openai/gpt-4.1-mini --mode multiagent
```

Model identifiers are examples; choose one available through your provider.
Provider and model are command-line options (or process environment variables
`LLM_PROVIDER` and `LLM_MODEL`), not additional required `.env` properties.
The model is required, while the provider defaults to OpenAI.

Additional options:

```bash
python main.py --provider openai --model gpt-4.1-mini \
  --mode multiagent --num-of-execution 5 \
  --input dataset/10211000132109_0373_Perinatal_depression.txt \
  --output-dir results/my-experiment --temperature 0 --json-mode
```

`--input` accepts one note or a directory of `.txt` notes. `--env-file` selects a
custom configuration file. By default results are written to
`results/single/<model>/` or `results/multi/<model>/`. OpenRouter's provider prefix
is removed: `openai/gpt-4.1-mini` becomes `gpt-4.1-mini`, and
`deepseek/deepseek-chat` becomes `deepseek-chat`. Unsupported filename characters
are replaced with underscores. The model folder identifies the selected model.
Running the same mode/model again replaces its previous generated results,
including obsolete executions if the repetition count is reduced. Other models
and modes are preserved. Results from different API providers using the same
model folder also replace each other. Use `--output-dir` to select a different
location when you need to keep them separate. `--base-url` selects a
custom API-compatible endpoint. Temperature and JSON mode are opt-in because
support varies by model; use matching settings when comparing workflows.

Each execution processes the same input snapshot. Each agent validates its output
and receives errors for correction until valid or its `MAX_ATTEMPTS` limit is
reached. The four specialists run concurrently; each retains its own correction loop.
Java validator calls are serialized to protect the shared package cache.
All started specialists finish before an outcome is recorded, including when
one fails, so attempts and usage include their work. Merge runs only after all four specialists
pass; it also validates and corrects the final Bundle. Single-agent uses the same
validation and correction policy.

Without corrections, multiagent makes five LLM calls per note and single-agent
makes one. With `MAX_ATTEMPTS=3`, these bounds are fifteen and three calls per
note respectively. There are no automatic API transport retries. Exhausted
validation attempts produce a failed outcome, and the next note/run continues.
A validator infrastructure failure stops the experiment instead of wasting calls.
The command exits with status 1 if generation or validation fails.

## Offline semantic evaluation

Run the separate, deterministic checker after generation. It requires no LLM,
API credentials, or additional packages and never repairs or edits a Bundle:

```bash
python semantic_validator.py --results-dir results/single/gpt-4.1-mini
```

This evaluates every JSON in `outputs/`, pairing `<run-id>_<note>.json` with
`dataset/<note>.txt`. It does not apply the development limit or sample ten facts.
For one Bundle, use `--bundle path/to/bundle.json --source path/to/note.txt`.
Optional `--dataset-dir` and `--report-dir` override input and report locations.

Two CSV files are overwritten in the selected results directory:

- `semantic_facts.csv`: resource, field path, expected value, outcome, explanation,
  observed value, and source evidence with character offsets.
- `semantic_summary.csv`: counts per note/run and coverage. Failed inputs have a
  failed evaluation status and no semantic scores; the command exits with status 1.

Outcomes are `supported`, `partial_match`, `contradicted`, and `not_verifiable`.
The inventory covers clinical resource properties without a fixed fact limit.
Quantities, ranges, and timing structures are grouped; recoverable panic frequency
and duration subclaims are also checked separately from their narrative.
Consequently, counts describe **field checks, not unique atomic clinical facts**.
Technical identifiers, references, generated narratives, and workflow defaults
are excluded, as are terminology metadata (`system`, `version`, `userSelected`),
nested element IDs, and extension schema URLs. These fields do not enter the
semantic score denominators. Raw terminology codes and coding entries (including their displays) are excluded.
Observation labels provide context for their values/components rather than separate
checks: `Heart rate` with `86 beats/minute` is one measurement check.
Extension values remain in the inventory. Unknown clinical fields remain
visible as `not_verifiable`.

Initial rules cover explicit demographic notation, registered clinical synonyms,
simple local negations, vital signs, weight changes, panic frequency/duration,
and simple medication doses/statuses. Full-phrase matching tolerates quotes and
parentheses and explicitly equates `mod–severe` with `moderate to severe` and
`ego-dystonic` with `egodystonic`, while preserving internal negation. This does
not provide general paraphrase recognition. CSV string values are written as
plain strings; objects remain JSON-encoded. Unit conversion, direction, intervals,
and approximation are considered. For example, `~4–5kg wt loss` contradicts
`Weight change <4.5 kg`; an exact `-4.5 kg` is only a partial match.

These rules are a conservative evidence baseline, not a complete semantic
validator. Lexical support does not establish diagnosis, resource-type choice,
patient/event identity, or temporal equivalence. Codes, dates, complex prose,
titrations, and ambiguous contexts generally remain unverified. The checker does
not detect information omitted from the Bundle. Coverage is the fraction of
checks with an outcome other than `not_verifiable`; supported fraction uses all
checks as its denominator. Neither metric is semantic accuracy or recall.

Use identical frozen rules for both workflows. Before interpreting research
results, audit a sample of findings, especially apparent contradictions and
supported matches. Generation does not use these reports as correction feedback.
Starting a new generation experiment in the same directory removes old semantic
reports to prevent them from describing replaced outputs.

## Project structure

| File | Responsibility |
|---|---|
| `config.py` | Load and validate configuration |
| `main.py` | Select a workflow and repeat it over the input dataset |
| `agent.py` | System prompts and bounded generate/validate/correct loop |
| `fhir_validator.py` | HL7 JAR adapter, resource contexts, and OperationOutcome parser |
| `llm.py` | Shared OpenAI/OpenRouter Chat Completions client |
| `workflow_singleagent.py` | Single-agent baseline with validation and correction |
| `workflow_multiagent.py` | Four specialists followed by Merge |
| `workflow_common.py` | Workflow result contract, JSON writing, and standalone CLI |
| `agents/common.md` | Shared FHIR and source-faithfulness instructions |
| `agents/patient.md` | Patient specialist |
| `agents/condition.md` | Condition specialist |
| `agents/observation.md` | Observation specialist |
| `agents/medication.md` | MedicationStatement and MedicationRequest specialist |
| `agents/merge.md` | Bundle assembly agent |
| `agents/single.md` | Single-agent task instructions |

The single-agent system prompt includes the same common and resource-specific
rules as the specialists. All specialists receive the original note. Merge
receives only their outputs and is instructed to preserve clinical content while
assembling resources and normalizing identifiers/references. Compliance with this
instruction is not yet independently checked.

Standalone one-attempt entry points remain available and also read credentials
from `.env`. Their filenames select the workflow; `MODE` and `NUM_OF_EXECUTION`
control the experiment runner `main.py`, not these standalone commands.
Standalone commands use the same compact result layout as `main.py`, default to
one execution, and replace the previous generated results for that mode/model.

```bash
python workflow_singleagent.py --provider openai --model gpt-4.1-mini
python workflow_multiagent.py --provider openai --model gpt-4.1-mini
```

## Results and current evaluation scope

Each mode/model directory contains only final JSON outputs and one CSV:

```text
results/
  single/
    gpt-4.1-mini/
      results.csv
      outputs/
        1_<note-id>.json
        2_<note-id>.json
  multi/
    deepseek-chat/
      results.csv
      outputs/
        1_<note-id>.json
```

`results.csv` contains one row per source note and execution, with these columns:

| Column | Meaning |
|---|---|
| `timestamp` | UTC start time of this note's execution, in ISO 8601 format |
| `file` | Original source filename, including `.txt` |
| `run-id` | Execution number, starting at 1; shared across notes in the same pass |
| `fhir-valid` | `1` for valid output; `0` for unsuccessful generation/validation; blank for validator infrastructure failures |
| `attempts` | `1 + total correction calls` across executed agents; blank only when no agent call was started |
| `single-attempts` | Generations attempted by the single agent |
| `patient-attempts` | Generations attempted by the Patient agent |
| `condition-attempts` | Generations attempted by the Condition agent |
| `observation-attempts` | Generations attempted by the Observation agent |
| `medication-attempts` | Generations attempted by the shared MedicationStatement/MedicationRequest agent |
| `merge-attempts` | Generations attempted by the Merge agent |

The JSON filename is `<run-id>_<source-stem>.json`, with no timestamp. Each
execution has a distinct file. Only the last complete parsed final response is
saved, including an invalid final Bundle after retries are exhausted. No final
JSON is written if a specialist exhausts its retries before Merge, or the last
response is not a complete JSON object. Intermediate resources, validation reports,
prompts, and raw text are not saved under `results/`.

`attempts` is one initial pipeline pass plus its correction calls. For example,
if Patient needs one correction and Merge needs two, `attempts=4`. Five agents
succeeding immediately still means `attempts=1`, not 5. Each per-agent counter includes its initial call and any correction calls.
Counters are updated before invoking the LLM, so failed calls are counted too.
An agent that was not executed has `0`; partial counts are preserved on technical
failures and when the pipeline stops before Merge. These separate columns make
unequal effort across agents visible. The per-agent limit is separate: with five agents and `MAX_ATTEMPTS=3`, `attempts` can reach 11.

A new invocation replaces `outputs/` and `results.csv` for the selected mode/model.
Legacy execution directories and manifests are removed to prevent stale results.
Other mode/model folders are preserved. Old CSVs generated before validation was
integrated are not retroactively validated; rerun the experiment for real scores.

## Structural validation and correction

The local `validator/validator_cli.jar` is invoked with FHIR R4 `4.0.1`,
`-tx n/a` (no external terminology server), and `-allow-example-urls true`
(the prompts use example.org identifiers). The supplied JAR tested here is
version 7.0.1. Validation also enforces the experiment's resource scope and
patient-reference conventions. Warnings alone do not invalidate an output;
`error` or `fatal` issues do. This is structural conformance under these settings,
not clinical correctness or exhaustive external terminology validation.

Each specialist's resources are wrapped in a temporary collection Bundle. A
minimal Patient is added only to the validation context for non-Patient agents,
so their subject references resolve. It is never added to their actual output.
Patient must supply exactly one Patient; optional specialists may supply an empty
resource list. The complete single-agent and Merge Bundles are validated as-is.

Validation errors, the previous response, and the original input are sent back
to the same agent for correction. Model refusals, truncated responses, malformed
JSON and contract violations use the same bounded correction loop. The Merge
agent's original input remains the validated specialist outputs.

Package downloads are cached under `.cache/fhir-validator/`; the first validation
requires network access for definitions. Reports and input wrappers are temporary
and deleted after validation. Java is launched once per check, so repeated checks
include startup overhead. The subprocess timeout is 180 seconds. Missing Java,
missing JAR, timeout, or an unusable validator report is an infrastructure failure,
not a request for the LLM to change clinical content. Freeze the JAR and cached
package versions when running the comparative study.

## Offline tests

```bash
python -m unittest discover -s tests -v
```

Tests do not require API keys or make paid requests.

## Dataset provenance and API references

The dataset now contains all 500 notes. The original development example was
downloaded unchanged on 2026-10-13 from
[SynGP500](https://raw.githubusercontent.com/pisong314/syngp500/refs/heads/master/notes/10211000132109_0373_Perinatal_depression.txt).
Follow the licensing and citation terms in the
[upstream repository](https://github.com/pisong314/syngp500).

SHA-256: `34354e6d26fef99bc1493d319e1431795317368a4d455d719ddd6d55f4c1716c`.

API references: [OpenAI Chat Completions](https://developers.openai.com/api/reference/python/resources/chat/subresources/completions/methods/create)
and [OpenRouter Quickstart](https://openrouter.ai/docs/quickstart).

---

## Historical anchored/unanchored experiment

The documentation below describes the previous design. Its `pipeline.py`,
`prompts.py`, `evaluate.py`, and `validate_bundle.py` remain as historical code;
the new workflows do not import them. Some modules required by the historical
commands are absent from this checkout. Use the entry points above for current work.

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

## Token usage

`results.csv` includes `input-tokens`, `output-tokens`, and `total-tokens` for each
note/run, plus the same columns prefixed with each agent name (for example,
`patient-total-tokens`). Counts sum the provider's Chat Completions usage across
all attempts, including invalid JSON, refusals, corrections, and Merge calls.
Input counts include prompts and correction context, not just the source note.

A missing usage field or a call failing without a response leaves its aggregate
blank, including the affected workflow total; it is never treated as zero.
Agents with no calls have zero tokens. Counters reset for every note/run.
Provider-reported totals are retained as supplied. These are token counts, not
monetary costs; cached/reasoning token details and pricing are not separate columns.
Existing CSVs are not backfilled because their usage data was not saved.
Semantic evaluation remains optional and separate from generation.

## Conversation logs

Both workflows automatically write `logs/<run-id>_<note>.jsonl` beside `outputs/`.
Each line is a JSON event with a UTC timestamp and sequence number. Agent events
include the agent name and attempt number. Requests contain the full system and
user messages; responses include full returned text, finish reason, refusal,
usage, response ID, model, and elapsed time. Validation failures include the
errors used in the next correction prompt. Merge inputs and outputs are logged
in the same way. Run events record the final outcome and counters.

Writes are synchronized across specialist threads and flushed after every event,
so partial logs survive an interrupted run. Existing logs are replaced with the
other results on a new experiment. No API headers or client credentials are
logged; known environment API keys are redacted. Transport exceptions record
only their type to avoid including request credentials. These logs contain the
source note and generated clinical data. Earlier experiments cannot be reconstructed.
