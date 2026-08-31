"""Generate paired FHIR R4 Bundles from manual fact annotations.

No-cost prompt inspection:
    python pipeline.py --annotations-dir annotations --limit 1 --dry-run

Real paired generation:
    python pipeline.py --annotations-dir annotations --limit 1
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fhir_checks import check_bundle
from prompts import FHIR_VERSION, PROMPT_VERSION, build_prompt, build_summary


def load_annotations(annotations_dir: Path, limit: int) -> list[tuple[Path, dict]]:
    """Load ready annotation JSON files in a stable order."""

    paths = sorted(annotations_dir.glob("*.json"))
    if not paths:
        raise FileNotFoundError(f"No annotation JSON files found in {annotations_dir}")
    selected = paths[:limit] if limit > 0 else paths
    return [
        (path, json.loads(path.read_text(encoding="utf-8"))) for path in selected
    ]


def call_llm(client: Any, model: str, prompt: str) -> dict[str, Any]:
    """Make one API call and preserve both raw and parsed output.

    There is deliberately no repair call. Invalid FHIR is an experimental outcome,
    so silently asking the model to fix it would bias the comparison.
    """

    response = client.responses.create(
        model=model,
        input=[{"role": "user", "content": prompt}],
        temperature=0,
        store=False,
        text={"format": {"type": "json_object"}},
    )
    raw_text = response.output_text or ""

    try:
        parsed = json.loads(raw_text)
        parse_error = None
    except json.JSONDecodeError as exc:
        parsed = None
        parse_error = str(exc)

    return {
        "response_id": response.id,
        "raw_text": raw_text,
        "bundle": parsed,
        "json_parse_error": parse_error,
        "local_errors": check_bundle(parsed),
    }


def condition_order(note_id: str) -> list[str]:
    """Counterbalance which experiment arm is called first, reproducibly."""

    first_byte = hashlib.sha256(note_id.encode("utf-8")).digest()[0]
    if first_byte % 2 == 0:
        return ["unanchored", "anchored"]
    return ["anchored", "unanchored"]


def save_condition(output_dir: Path, condition: str, result: dict[str, Any]) -> None:
    """Save unchanged raw output and, when parseable, pretty Bundle JSON."""

    (output_dir / f"{condition}.raw.txt").write_text(
        result["raw_text"] + "\n", encoding="utf-8"
    )
    bundle_path = output_dir / f"{condition}.bundle.json"
    if result["bundle"] is not None:
        bundle_path.write_text(
            json.dumps(result["bundle"], ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    else:
        # With --overwrite, do not leave an older valid-looking Bundle beside a
        # newly generated response that failed JSON parsing.
        bundle_path.unlink(missing_ok=True)


def process_annotation(
    client: Any,
    model: str,
    annotation_path: Path,
    annotation: dict,
    output_root: Path,
) -> Path:
    """Run both arms for one annotation and write a reproducibility record."""

    # Building both summaries validates the annotation before making paid calls.
    summaries = {
        "unanchored": build_summary(annotation, anchored=False),
        "anchored": build_summary(annotation, anchored=True),
    }
    prompts = {
        "unanchored": build_prompt(annotation, anchored=False),
        "anchored": build_prompt(annotation, anchored=True),
    }
    order = condition_order(annotation["note_id"])
    results: dict[str, dict[str, Any]] = {}

    for condition in order:
        print(f"    API call: {condition}")
        results[condition] = call_llm(client, model, prompts[condition])

    note_output = output_root / annotation["note_id"]
    note_output.mkdir(parents=True, exist_ok=True)
    for condition, result in results.items():
        save_condition(note_output, condition, result)

    annotation_bytes = annotation_path.read_bytes()
    metadata = {
        "schema_version": 2,
        "note_id": annotation["note_id"],
        "source_file": annotation["source_file"],
        "annotation_file": annotation_path.name,
        "annotation_sha256": hashlib.sha256(annotation_bytes).hexdigest(),
        "model": model,
        "fhir_version": FHIR_VERSION,
        "prompt_version": PROMPT_VERSION,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "call_order": order,
        "input_sha256": {
            name: hashlib.sha256(text.encode("utf-8")).hexdigest()
            for name, text in summaries.items()
        },
        "results": {
            condition: {
                "response_id": result["response_id"],
                "json_parsed": result["bundle"] is not None,
                "json_parse_error": result["json_parse_error"],
                "local_guard_passed": not result["local_errors"],
                "local_errors": result["local_errors"],
            }
            for condition, result in results.items()
        },
    }
    (note_output / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return note_output


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--annotations-dir", type=Path, default=Path("annotations"))
    parser.add_argument("--output-dir", type=Path, default=Path("outputs/bundles"))
    parser.add_argument("--limit", type=int, default=0, help="0 means all ready files")
    parser.add_argument(
        "--model",
        default=os.environ.get("OPENAI_MODEL", "gpt-4.1-mini"),
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    if args.limit < 0:
        parser.error("--limit must be zero or positive")
    return args


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()
    annotations = load_annotations(args.annotations_dir, args.limit)

    # Validate and print the first pair without importing OpenAI or making calls.
    if args.dry_run:
        _, annotation = annotations[0]
        print("===== UNANCHORED INPUT =====\n")
        print(build_prompt(annotation, anchored=False))
        print("\n===== ANCHORED INPUT =====\n")
        print(build_prompt(annotation, anchored=True))
        print("\nDry run complete: no API calls were made.")
        return

    if not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY is not set. See README.md Step 5.")
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise SystemExit(
            "Could not import the OpenAI SDK. Activate .venv and run "
            f"pip install -r requirements.txt. Original error: {exc}"
        ) from exc

    client = OpenAI()
    generated = 0
    for position, (annotation_path, annotation) in enumerate(annotations, start=1):
        note_output = args.output_dir / annotation["note_id"]
        if (note_output / "metadata.json").exists() and not args.overwrite:
            print(f"[{position}/{len(annotations)}] SKIP {annotation['note_id']}")
            continue
        print(f"[{position}/{len(annotations)}] RUN  {annotation['note_id']}")
        saved = process_annotation(
            client, args.model, annotation_path, annotation, args.output_dir
        )
        print(f"    Saved: {saved}")
        generated += 1
    print(f"Finished. Generated {generated} paired Bundle result(s).")


if __name__ == "__main__":
    main()
