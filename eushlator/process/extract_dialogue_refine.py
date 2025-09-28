# eushlator/process/extract_dialogue_refine.py
"""
Phase-2: combine consecutive text-boxes with the same speaker.

Input : 3_ExtractedDialogue/SCxxxx.yaml   (one file per scene)
Output: 4_MachineTranslations/dialogue_ready.yaml

YAML format:

SC0000:
  - speaker: マルク
    text: |-
      「おはよう」
      「今日もいい天気だ」
  - speaker: ルイリ
    text: 「ふふっ、そうだね」
SC0001:
  ...
"""

from __future__ import annotations

import re
from pathlib import Path
import yaml

from eushlator.utils.yaml_utils import save_yaml, load_yaml

# Accepts scene files named (optionally) with a "$<1-2 digits>$" prefix,
# followed by one of SC/SN/SP/SG and exactly 4 digits, with ".yaml" suffix.
# Examples: "SC0001.yaml", "$11$SP6932.yaml"
VALID_SCENE = re.compile(r'(?:\$\d{1,2}\$)?(SC|SN|SP|SG)\d{4}\.yaml$', re.IGNORECASE)


def _load_boxes(yaml_path: Path) -> list[dict]:
    """Return a list of boxes ({speaker,text}) ordered by numeric key.

    The input YAML (per scene) is a mapping from numeric-ish keys ("1","2",...)
    to entries like:
      { "text": "...", "speaker": "...", "offset": <int> }

    We sort keys numerically to be robust against string/int key variations.
    """
    tree = load_yaml(yaml_path) or {}
    # Keys are "1", "2", … (or ints); sort numerically just in case.
    ordered = [tree[k] for k in sorted(tree, key=lambda x: int(x))]
    return ordered


def _collapse_boxes(boxes: list[dict]) -> list[dict]:
    """Merge consecutive boxes with the same speaker into one chunk.

    Produces a list of chunks in order:
      - Each chunk is { "speaker": <name>, "text": <joined lines>, "id": <1-based> }
      - The "text" is a newline join of all consecutive box texts for that speaker.
      - The "id" is the 1-based index of the chunk within the scene (not the original box id).
    """
    if not boxes:
        return []

    chunks: list[dict] = []
    cur_speaker = boxes[0]["speaker"]
    cur_lines = [boxes[0]["text"]]

    i = 1  # chunk counter (1-based)
    for box in boxes[1:]:
        if box["speaker"] == cur_speaker:
            # Same speaker as current chunk: accumulate text.
            cur_lines.append(box["text"])
        else:
            # Speaker changed: finalize current chunk and start a new one.
            chunks.append({"speaker": cur_speaker,
                           "text": "\n".join(cur_lines),
                           "id": i})
            cur_speaker = box["speaker"]
            cur_lines = [box["text"]]
            i += 1

    # Finalize the last chunk.
    chunks.append({"speaker": cur_speaker,
                   "text": "\n".join(cur_lines),
                   "id": i})
    return chunks


def run_refine_dialogue(in_dir: Path, out_dir: Path) -> None:
    """Collapse per-scene dialogue YAML files into multi-line chunks per speaker.

    Process:
      1) Iterate in_dir for files matching VALID_SCENE.
      2) Load ordered boxes and collapse consecutive same-speaker entries.
      3) Aggregate into a dict: { <scene_id>: [ {speaker, text, id}, ... ], ... }
      4) Write the combined result to out_dir/$$full_script.yaml

    Notes:
      - If the output file already exists, the function skips processing.
      - Scenes producing no chunks are omitted from the result.
    """
    out_yaml = out_dir / "$$full_script.yaml"
    if out_yaml.exists():
        print("[Info] Skipping refining dialogue, Full script file already exists.")
        return

    result: dict[str, list[dict]] = {}

    for yaml_file in sorted(in_dir.glob("*.yaml")):
        if not VALID_SCENE.match(yaml_file.name):
            continue

        scene_id = yaml_file.stem        # e.g. SC0000
        boxes = _load_boxes(yaml_file)
        chunks = _collapse_boxes(boxes)
        if chunks:
            result[scene_id] = chunks

    if not result:
        print("[Warn] No dialogue files found.")
        return

    out_yaml.parent.mkdir(parents=True, exist_ok=True)
    save_yaml(out_yaml, result)
    print(f"[Info] Dialogue chunks written → {out_yaml}")


# ── tiny demo harness ─────────────────────────────────────────────────────
def _demo() -> None:
    # Minimal example: reads all scene YAMLs in 3_ExtractedDialogue and writes
    # the aggregated "$$full_script.yaml" into 4_MachineTranslations.
    # (Note: the second argument is a directory; the filename is added inside run_refine_dialogue.)
    root = Path.cwd()
    run_refine_dialogue(
        root / "3_ExtractedDialogue",
        root / "4_MachineTranslations" / "dialogue_ready.yaml",
    )


if __name__ == "__main__":
    _demo()
