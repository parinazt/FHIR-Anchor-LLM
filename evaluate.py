"""Prepare blinded Bundle review files and summarize completed human annotations.

Prepare review package:
    python evaluate.py prepare

Score a completed annotation CSV:
    python evaluate.py score
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import shutil
import statistics
from pathlib import Path


CONDITIONS = ("unanchored", "anchored")


def output_source(note_dir: Path, condition: str) -> Path:
    """Choose parsed Bundle JSON when present; otherwise preserve raw model text."""

    bundle_path = note_dir / f"{condition}.bundle.json"
    if bundle_path.exists():
        return bundle_path
    raw_path = note_dir / f"{condition}.raw.txt"
    if raw_path.exists():
        return raw_path
    raise FileNotFoundError(f"No output found for {note_dir.name}/{condition}")


def prepare_review(
    bundles_dir: Path,
    annotations_dir: Path,
    review_dir: Path,
    annotation_csv: Path,
    key_csv: Path,
    seed: int,
) -> int:
    """Copy paired outputs to randomized A/B names and create the rating sheet."""

    metadata_paths = sorted(bundles_dir.glob("*/metadata.json"))
    if not metadata_paths:
        raise FileNotFoundError(f"No completed Bundle pairs found below {bundles_dir}")
    randomizer = random.Random(seed)
    review_dir.mkdir(parents=True, exist_ok=True)
    annotation_csv.parent.mkdir(parents=True, exist_ok=True)
    key_csv.parent.mkdir(parents=True, exist_ok=True)

    fields = [
        "note_id",
        "reference_file",
        "system_a_file",
        "system_b_file",
        "reference_fact_count",
        "a_covered_reference_fact_count",
        "a_unsupported_claim_count",
        "a_wrong_resource_type_count",
        "a_faithfulness_1_to_5",
        "a_mapping_quality_1_to_5",
        "b_covered_reference_fact_count",
        "b_unsupported_claim_count",
        "b_wrong_resource_type_count",
        "b_faithfulness_1_to_5",
        "b_mapping_quality_1_to_5",
        "preference_A_B_or_Tie",
        "reviewer_comments",
    ]

    with (
        annotation_csv.open("w", encoding="utf-8-sig", newline="") as ann_file,
        key_csv.open("w", encoding="utf-8-sig", newline="") as key_file,
    ):
        ann_writer = csv.DictWriter(ann_file, fieldnames=fields)
        key_writer = csv.DictWriter(
            key_file, fieldnames=["note_id", "system_a_condition", "system_b_condition"]
        )
        ann_writer.writeheader()
        key_writer.writeheader()

        for metadata_path in metadata_paths:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            note_id = metadata["note_id"]
            source_annotation = annotations_dir / metadata["annotation_file"]
            annotation = json.loads(source_annotation.read_text(encoding="utf-8"))
            note_review_dir = review_dir / note_id
            note_review_dir.mkdir(parents=True, exist_ok=True)

            # The reference contains human-selected facts and the raw note, but no
            # information about which generated output received FHIR headings.
            reference = {
                "note_id": note_id,
                "source_file": annotation["source_file"],
                "source_note": annotation.get("source_note", ""),
                "reference_sections": annotation["sections"],
            }
            reference_path = note_review_dir / "reference.json"
            reference_path.write_text(
                json.dumps(reference, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            fact_count = sum(len(facts) for facts in annotation["sections"].values())

            if randomizer.choice((True, False)):
                a_condition, b_condition = CONDITIONS
            else:
                b_condition, a_condition = CONDITIONS

            copied_paths = {}
            for letter, condition in (("A", a_condition), ("B", b_condition)):
                source = output_source(metadata_path.parent, condition)
                suffix = ".json" if source.suffix == ".json" else ".txt"
                destination = note_review_dir / f"system_{letter}{suffix}"
                shutil.copyfile(source, destination)
                copied_paths[letter] = destination

            ann_writer.writerow(
                {
                    "note_id": note_id,
                    "reference_file": str(reference_path),
                    "system_a_file": str(copied_paths["A"]),
                    "system_b_file": str(copied_paths["B"]),
                    "reference_fact_count": fact_count,
                }
            )
            key_writer.writerow(
                {
                    "note_id": note_id,
                    "system_a_condition": a_condition,
                    "system_b_condition": b_condition,
                }
            )
    return len(metadata_paths)


def required_number(row: dict[str, str], field: str, minimum: float = 0) -> float:
    """Parse one required numeric rating with a note-specific error message."""

    try:
        value = float(row.get(field, "").strip())
    except ValueError as exc:
        raise ValueError(f"Invalid or blank {field} for {row['note_id']}") from exc
    if value < minimum:
        raise ValueError(f"{field} must be at least {minimum} for {row['note_id']}")
    return value


def score_review(annotation_csv: Path, key_csv: Path) -> str:
    """Unblind completed ratings and report anchored-minus-unanchored differences."""

    with key_csv.open(encoding="utf-8-sig", newline="") as file:
        key = {row["note_id"]: row for row in csv.DictReader(file)}
    with annotation_csv.open(encoding="utf-8-sig", newline="") as file:
        rows = list(csv.DictReader(file))
    if not rows:
        raise ValueError("Annotation CSV contains no rows.")

    metric_names = (
        "coverage_proportion",
        "unsupported_claim_count",
        "wrong_resource_type_count",
        "faithfulness",
        "mapping_quality",
    )
    values = {
        condition: {metric: [] for metric in metric_names} for condition in CONDITIONS
    }

    for row in rows:
        assignment = key.get(row["note_id"])
        if assignment is None:
            raise ValueError(f"Missing key row for {row['note_id']}")
        reference_count = required_number(row, "reference_fact_count", minimum=1)
        for letter in ("a", "b"):
            condition = assignment[f"system_{letter}_condition"]
            covered = required_number(row, f"{letter}_covered_reference_fact_count")
            if covered > reference_count:
                raise ValueError(f"Covered facts exceed reference facts for {row['note_id']}")
            faithfulness = required_number(row, f"{letter}_faithfulness_1_to_5", 1)
            mapping = required_number(row, f"{letter}_mapping_quality_1_to_5", 1)
            if faithfulness > 5 or mapping > 5:
                raise ValueError(f"1-to-5 score exceeds 5 for {row['note_id']}")

            values[condition]["coverage_proportion"].append(covered / reference_count)
            values[condition]["unsupported_claim_count"].append(
                required_number(row, f"{letter}_unsupported_claim_count")
            )
            values[condition]["wrong_resource_type_count"].append(
                required_number(row, f"{letter}_wrong_resource_type_count")
            )
            values[condition]["faithfulness"].append(faithfulness)
            values[condition]["mapping_quality"].append(mapping)

    lines = [f"Completed paired clinical reviews: {len(rows)}", ""]
    for metric in metric_names:
        unanchored = statistics.mean(values["unanchored"][metric])
        anchored = statistics.mean(values["anchored"][metric])
        lines.append(
            f"{metric:28} unanchored={unanchored:.3f}  "
            f"anchored={anchored:.3f}  difference={anchored - unanchored:+.3f}"
        )
    lines.extend(
        [
            "",
            "Positive differences favor anchors for coverage, faithfulness, and mapping.",
            "Negative differences favor anchors for unsupported claims and wrong types.",
        ]
    )
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--bundles-dir", type=Path, default=Path("outputs/bundles"))
    prepare.add_argument("--annotations-dir", type=Path, default=Path("annotations"))
    prepare.add_argument(
        "--review-dir", type=Path, default=Path("outputs/review/blinded")
    )
    prepare.add_argument(
        "--annotations", type=Path, default=Path("outputs/review/annotations.csv")
    )
    prepare.add_argument("--key", type=Path, default=Path("outputs/review/key.csv"))
    prepare.add_argument("--seed", type=int, default=2026)

    score = subparsers.add_parser("score")
    score.add_argument(
        "--annotations", type=Path, default=Path("outputs/review/annotations.csv")
    )
    score.add_argument("--key", type=Path, default=Path("outputs/review/key.csv"))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.command == "prepare":
        count = prepare_review(
            args.bundles_dir,
            args.annotations_dir,
            args.review_dir,
            args.annotations,
            args.key,
            args.seed,
        )
        print(f"Prepared {count} blinded Bundle pair(s): {args.annotations}")
        print(f"Keep this condition key hidden: {args.key}")
    else:
        print(score_review(args.annotations, args.key))


if __name__ == "__main__":
    main()

