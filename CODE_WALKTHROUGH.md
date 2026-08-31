# Code walkthrough for a beginner

This guide follows the order in which you use the project. It explains every code
block and the important individual lines. Python comments start with `#`. Text inside
triple quotes is a docstring: executable documentation attached to a file or
function. Type hints such as `path: Path` help readers and tools but do not change
the clinical logic.

## 1. `download_syngp500.py`: obtain the source notes

The constants at the top name the upstream ZIP and expected note count. Pinning an
upstream commit would be even stronger for the final paper; record the downloaded
dataset state in the protocol.

`download_notes(destination)` performs four simple operations:

1. `destination.mkdir(...)` creates the local notes folder.
2. `urllib.request.urlopen(...)` downloads the public ZIP.
3. `ZipFile(...)` reads the archive without running any file inside it.
4. The loop accepts only `.txt` files from the expected notes directory and writes
   only their filename, preventing an archive path from escaping the destination.

The final count check deliberately fails unless exactly 500 notes were found. A
silent partial download would invalidate sampling.

## 2. `prepare_annotations.py`: create human work files

### Imports

`argparse` reads command-line options. `json` writes editable annotation objects.
`random` provides seeded shuffling. `Path` handles Windows paths safely.
`from prompts import ANCHORS` imports the single authoritative five-resource list.

### `select_notes`

```python
note_paths = sorted(notes_dir.glob("*.txt"))
random.Random(seed).shuffle(note_paths)
return note_paths[:limit] if limit > 0 else note_paths
```

The first line finds and sorts all text files. Sorting creates a stable starting
order. The second creates a private pseudo-random generator with the declared seed
and shuffles the list reproducibly. The third returns the first requested number;
zero means all notes.

### `make_template`

The returned dictionary records annotation version, note ID, source filename,
status, instructions, complete raw note, and five empty fact lists. `note_path.stem`
means the filename without `.txt`. `read_text(encoding="utf-8", errors="strict")`
refuses invalid text rather than silently replacing characters.

### `create_templates`

`annotations_dir.mkdir(...)` creates the destination. For every sampled note, the
function calculates a `.json` path. If that path exists, it prints `SKIP` and never
overwrites manual work. `json.dumps(..., ensure_ascii=False, indent=2)` preserves
normal Unicode and makes the file readable.

### `parse_args` and `main`

The defaults create five templates using seed 2026. A negative limit is rejected.
`main` passes the options to `create_templates` and reports how many new files were
created.

## 3. `prompts.py`: define the controlled experiment

This is the most important scientific file.

### Versions and `ANCHORS`

`PROMPT_VERSION = "2.0"` is written into every result. Increment it whenever prompt
wording changes. `FHIR_VERSION = "4.0.1"` fixes the experiment to FHIR R4.
`ANCHORS` is an ordered tuple of the only clinical resource types permitted in a
Bundle.

### `CONVERSION_INSTRUCTIONS`

This long string is identical in both arms. Its blocks have distinct purposes:

- output rules require JSON, a `collection` Bundle, one `patient-1`, unique IDs,
  `fullUrl`, and patient references;
- clinical safety rules prohibit unstated facts and invented terminology codes;
- omission rules prevent the model from filling empty categories;
- minimum-resource rules supply required fields and deterministic workflow-status
  mappings that cannot be left to model improvisation.

Both arms must see the allowed resource list. Otherwise one arm would be solving a
different task. The experimental intervention is fact-level headings, not whether
the model has ever heard the word FHIR.

### `validate_annotation`

`required_top_level - annotation.keys()` calculates missing fields. A non-`ready`
file is rejected before paid API calls. The function then checks that `sections` is
an object, contains no unsupported headings, has a list for every anchor, contains
only non-empty strings, and includes at least one fact. These checks catch formatting
mistakes; they cannot judge whether a fact is clinically true.

### `build_summary`

```python
for anchor in ANCHORS:
    facts = annotation["sections"][anchor]
    if not facts:
        continue
    if anchored:
        lines.append(f"[{anchor}]")
    lines.extend(f"- {fact.strip()}" for fact in facts)
```

The loop visits categories in one fixed order. Empty categories disappear. Only the
anchored arm receives the bracketed heading. `lines.extend(...)` adds the exact same
fact strings to both arms. There is no model or separate human summary at this step.

### `build_prompt`

This joins the shared instructions, a clear start marker, the derived summary, and
an end marker. The markers help prevent the model from confusing clinical content
with instructions.

## 4. `fhir_checks.py`: fast local feedback

This file is deliberately small and is **not** called an official validator. It
checks common study-specific requirements before Java is started:

- root resource is Bundle and type is collection;
- `entry` is a list;
- all resource types are in scope;
- IDs and `fullUrl` values exist and are unique;
- exactly one `Patient/patient-1` exists;
- every other resource refers to that patient;
- the minimum fields requested for each resource are present.

It returns a list of error strings. An empty list means the local guard passed. The
official validator remains the conformance outcome because local checks cover only a
small subset of FHIR R4.

## 5. `pipeline.py`: generate both Bundles

### `load_annotations`

`glob("*.json")` finds annotation files, `sorted` makes processing stable, and the
limit takes the first N. The JSON becomes a Python dictionary. Full validation occurs
when the prompt is built, before any paid call.

### `call_llm`

This is the only API function:

```python
response = client.responses.create(
    model=model,
    input=[{"role": "user", "content": prompt}],
    temperature=0,
    store=False,
    text={"format": {"type": "json_object"}},
)
```

- `model=model` uses the frozen command-line/environment choice.
- `input` sends one self-contained prompt; the two arms share no conversation.
- `temperature=0` reduces avoidable sampling variation.
- `store=False` asks the API not to retain the response as application state.
- JSON-object mode makes parseable JSON more likely, but it does not guarantee valid
  FHIR; that is why official validation remains necessary.

`response.output_text` preserves the model text. `json.loads` attempts parsing. A
parse error is saved rather than repaired. `check_bundle(parsed)` records quick local
errors. The response ID is retained for traceability.

### Why no automatic repair

An invalid answer is data. Asking the model to fix only invalid outputs changes the
number of calls and supplies error feedback. It could make one arm look better and
would answer a different question: “Can an iterative repair workflow produce valid
FHIR?” This simple experiment measures the first attempt.

### `condition_order`

`hashlib.sha256(note_id...)` converts each note ID into stable bytes. Whether the
first byte is even determines which arm runs first. Approximately half the notes
start anchored and half unanchored, without relying on a changing random state.

### `save_condition`

The raw string is always saved. If JSON parsing worked, the parsed object is also
saved with indentation. If parsing failed during `--overwrite`, an old Bundle file
is removed so stale valid-looking JSON cannot be mistaken for the new response.

### `process_annotation`

The function first builds both summaries and prompts. This validates the manual file
before spending money. It calls both arms in counterbalanced order, creates one note
folder, and saves both responses.

`metadata.json` records:

- schema, note, annotation file, and SHA-256 annotation hash;
- model, FHIR version, and prompt version;
- UTC time and API call order;
- hashes of both derived inputs;
- response IDs, JSON parse status, and local-guard results.

Hashes show whether an input or annotation changed; they do not reveal its content.

### `main`

Dry-run mode prints both complete prompts, returns immediately, and never imports the
OpenAI SDK. A real run requires `OPENAI_API_KEY`. Existing note metadata causes a
skip unless `--overwrite` is explicit. This protects collected results.

## 6. `validator_runtime.py`: isolate validator folders

The official Java validator uses package, terminology, and temporary caches. This
helper creates all of them under `tools/fhir-home`, writes a small FHIR-settings JSON,
sets Java properties, and returns an environment for the subprocess. Keeping this
logic in one file prevents setup and study validation from silently using different
paths.

On some Windows installations the validator prefers an existing `C:\temp` for an
initial terminology cache. That behavior belongs to the validator itself; a normal
PowerShell process can write there. A heavily restricted sandbox may require the
validator command to be allowed outside that sandbox.

## 7. `setup_validator.py`: pin and prove the official validator

`VALIDATOR_VERSION` fixes release 6.10.2. `VALIDATOR_URL` points directly to that
official HL7-maintained GitHub release asset.

### `download_validator`

The JAR is streamed in one-megabyte chunks so it is not held entirely in memory.
Each chunk updates a SHA-256 checksum. A JAR is a ZIP archive, so the first bytes
must be `PK`. The final JAR and a metadata JSON containing URL, version, checksum,
and download time are saved under `tools` and ignored by Git.

### `bootstrap_validator`

The function validates `examples/valid_bundle.json` as FHIR 4.0.1 with terminology
server set to `n/a`. The first run downloads/caches core R4 packages. It reads the
resulting `OperationOutcome` and fails if any issue has severity `fatal` or `error`.
This proves Java, the JAR, R4 definitions, command syntax, and output parsing work
together before study data is generated.

## 8. `validate_bundles.py`: measure official validity

### `parse_operation_outcome`

The validator returns an `OperationOutcome`. The function verifies the resource
type, counts every severity, extracts readable messages, and defines:

```python
official_valid = fatal_count == 0 and error_count == 0
```

Warnings remain visible. They do not change official validity.

### `run_validator`

One Java process validates one Bundle with `-version 4.0.1`, `-tx n/a`, controlled
cache paths, and a named JSON output. Standard output, standard error, and the exact
command are written to a log. A timeout prevents a stuck validator from blocking the
entire experiment indefinitely.

If no OperationOutcome is produced, the row is marked invalid and incomplete. The
script does not reinterpret Java's return code as clinical or FHIR validity; it uses
the OperationOutcome severities.

### `validate_directory`

The function discovers completed note metadata, then looks for both conditions. A
missing Bundle—usually caused by invalid JSON—is recorded as invalid without asking
Java to validate a nonexistent file. Existing Bundles are validated independently.

### `summarize_pairs`

Rows are grouped by note ID into the four paired outcomes. The two discordant cells
are the direct anchor comparison. The JSON report includes condition totals and the
paired table; the CSV retains error messages for analysis.

## 9. `evaluate.py`: blinded clinical review

### `output_source`

The function prefers parsed Bundle JSON. If parsing failed, it copies raw text so
reviewers can still see exactly what the model returned.

### `prepare_review`

For each note, the function creates:

- `reference.json` containing the raw note and clinician-selected reference facts;
- randomly assigned `system_A` and `system_B` files;
- one blank ratings row;
- a separate secret key mapping letters to conditions.

`random.Random(seed)` makes A/B assignment reproducible. `shutil.copyfile` copies the
unchanged generated result. `utf-8-sig` helps Excel recognize Unicode CSV files.

### `score_review`

The scorer refuses blanks, negative counts, coverage above the reference fact count,
or 1–5 scores outside range. It reveals the key, maps A/B ratings back to anchored or
unanchored, and reports means plus `anchored - unanchored` differences. Positive is
better for coverage/faithfulness/mapping; negative is better for unsupported claims
and wrong resource types.

The code gives descriptive pilot summaries. A conference analysis should add paired
confidence intervals, the pre-specified test, missing-data handling, and inter-rater
agreement when there are two reviewers.

## 10. `tests`: what is verified without spending money

The tests use fake API clients and temporary folders. They verify that:

- anchored and unanchored summaries contain identical fact strings;
- only the anchored summary has headings;
- draft annotations are rejected;
- local Bundle guard accepts a known-good limited Bundle and rejects bad types;
- one annotation causes exactly two API calls;
- call order is reproducible;
- output and metadata are saved correctly;
- validator OperationOutcome severities and paired tables are parsed correctly;
- masked A/B files and clinical scoring work.

Offline tests cannot prove credentials, network access, model behavior, FHIR
conformance, or clinical correctness. That is why the workflow also includes one
live API pair, a known-good validator bootstrap, official validation of every study
output, and human review.

## The safest order for changing code later

1. Change annotation rules first and document the scientific reason.
2. If prompt wording changes, increment `PROMPT_VERSION`.
3. Add or adjust an offline test that expresses the intended behavior.
4. Run all tests.
5. Dry-run one annotation and compare the complete prompts.
6. Generate one new development pair in a new output directory.
7. Run the official validator and inspect all issues.
8. Only then freeze the revision and start unseen data.

Never mix outputs made with different prompt versions or models in one primary
comparison unless that mixture was explicitly part of the protocol.
