"""Stable result paths and replacement of previous generated artifacts."""
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def result_directory(mode: str, model: str) -> Path:
    modes = {"single-agent": "single", "singleagent": "single", "multiagent": "multi"}
    if mode not in modes:
        raise ValueError(f"Unknown workflow mode: {mode}")
    # OpenRouter uses provider/model identifiers; the model is one folder.
    name = model.rsplit("/", 1)[-1]
    name = re.sub(r"[^A-Za-z0-9._-]", "_", name).strip(".")
    if not name:
        raise ValueError("Model identifier must contain a usable folder name")
    return ROOT / "results" / modes[mode] / name


def prepare_directory(path: Path, *, experiment: bool) -> None:
    """Replace only known generated artifacts, preserving unrelated files."""
    path = Path(path)
    if path.is_symlink():
        raise ValueError("The output directory must not be a symbolic link")
    path.mkdir(parents=True, exist_ok=True)
    files = ({"experiment.json", "results.csv", "outputs", "logs", "semantic_facts.csv", "semantic_summary.csv"} if experiment else
             {"source.txt", "metadata.json", "bundle.json"})
    agents = {"patient", "condition", "observation", "medication", "merge", "single"}
    for child in path.iterdir():
        generated_dir = (bool(re.fullmatch(r"execution-\d+", child.name)) if experiment
                         else child.name in agents)
        if child.name in files or generated_dir:
            if child.is_symlink() or child.is_file():
                child.unlink()
            elif child.is_dir():
                shutil.rmtree(child)
