"""
utils/compare_mt.py  –  Compare machine-translation outputs across models.

main(root, scenes, models, dialogue_dir, out_dir)
──────────────────────────────────────────────────
* root          : Path to 4_MachineTranslations
* scenes        : list[str]  (e.g. ["SC0000"])
* models        : list[str]  (folder names under 4_MachineTranslations)
* dialogue_dir  : path to 3_ExtractedDialogue  (contains $$full_script.yaml)
* out_dir       : where comparison yaml files are written

Each output YAML row:
  - id
  - speaker
  - jp
  - models: { model_name: en_text, ... }
"""

from __future__ import annotations
from pathlib import Path
from typing import List, Dict
import yaml
from eushlator.utils.yaml_utils import LiteralDumper, load_yaml


# ───────────────────────── helpers ──────────────────────────

def collect_models(root: Path, model_names: List[str]) -> List[str]:
    """
    Validate that each requested model folder exists under `root`.

    Args:
        root: Path to 4_MachineTranslations
        model_names: list of folder names to check

    Returns:
        The same list of model_names if all exist.

    Raises:
        FileNotFoundError: if any model directory is missing.
    """
    for m in model_names:
        if not (root / m).exists():
            raise FileNotFoundError(f"Model folder missing: {root/m}")
    return model_names


def gather_jp_boxes(full_script: dict, scene: str) -> List[dict]:
    """Return list of JP box dicts for a scene (order preserved).

    `full_script[scene]` is expected to be a list like:
      [{id: int, speaker: str, text: str}, ...]
    """
    # full_script[scene] is already a list of box dicts
    return full_script.get(scene, [])


def compare_scene(
    scene: str,
    models: List[str],
    mt_root: Path,
    full_script: dict,
    out_dir: Path,
):
    """
    Build a per-scene comparison table for all given `models`.

    For each JP box (id/speaker/text) in $$full_script.yaml:
      - create a row dict
      - fill "models" sub-dict with each model's EN text (if available)
      - write out a YAML file <scene>_compare.yaml to `out_dir`
    """
    jp_boxes = gather_jp_boxes(full_script, scene)
    if not jp_boxes:
        print(f"[WARN] {scene}: No JP boxes found in $$full_script.yaml")
        return

    # Build comparison rows, one row per JP box id
    rows: List[Dict] = [
        {
            "id": b["id"],
            "speaker": b["speaker"],
            "jp": b["text"],
            "models": {},  # populated below
        }
        for b in jp_boxes
    ]
    # Fast lookup by id to fill per-model translations
    rows_by_id = {r["id"]: r for r in rows}

    # Load each model's scene YAML and copy translated text into the row
    for model in models:
        mt_file = mt_root / model / f"{scene}.yaml"
        if not mt_file.exists():
            print(f"[WARN] {scene}: {model} has no translation file")
            continue
        mt_yaml = load_yaml(mt_file)
        for entry in mt_yaml.get("translations", []):
            rid = entry["id"]
            if rid in rows_by_id:
                # Normalize double blank lines to single for compactness
                rows_by_id[rid]["models"][model] = entry["text"].replace("\n\n", "\n")

    # Write comparison yaml for the scene
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{scene}_compare.yaml"
    out_path.write_text(
        yaml.dump(rows, allow_unicode=True, sort_keys=False, width=10000, Dumper=LiteralDumper),
        encoding="utf-8",
    )
    print("✓ wrote", out_path)


# ───────────────────────── public API ────────────────────────
def main(
    root: Path,
    scenes: List[str],
    models: List[str],
    dialogue_dir: Path,
    out_dir: Path,
):
    """
    Orchestrate comparison generation for multiple scenes and models.

    Args:
        root: 4_MachineTranslations path
        scenes: list of scene ids, e.g., ["SC0000"]
        models: list of model folder names under `root`
        dialogue_dir: path to 3_ExtractedDialogue (must contain $$full_script.yaml)
        out_dir: output directory for <scene>_compare.yaml files
    """
    models = collect_models(root, models)
    full_script = load_yaml(dialogue_dir / "$$full_script.yaml")

    for scene in scenes:
        compare_scene(scene, models, root, full_script, out_dir)


# ───────────────────────── CLI / CONFIG ──────────────────────
if __name__ == "__main__":
    from pathlib import Path

    # Simple local convenience launch:
    # Reads game config to locate the Eushlator workspace and runs a sample comparison.
    with open("config.yaml", "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    game_cfg = cfg.get(22, {})
    install_path = Path(game_cfg["install_path"])
    root = install_path / "Eushlator"

    mtr = root / "4_MachineTranslations"
    dialogue_dir = root / "3_ExtractedDialogue"
    out_dir = root / "Utils"

    scenes = ["SC0000"]
    models = [
        "ClaudeBatchLLM_claude-sonnet-4-20250514",
        "Custom",
        "OpenAIBatchLLM_gpt-4o-mini",
        "OpenAIBatchLLM_gpt-4.1-mini",
        "ClaudeBatchLLM_claude-opus-4-20250514",
    ]

    main(mtr, scenes, models, dialogue_dir, out_dir)
