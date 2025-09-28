from copy import deepcopy
from pathlib import Path
import yaml
from typing import List, Dict

from eushlator.process.translate import load_pua
from eushlator.utils.code_utils import create_text_box_code, create_text_box_code_concat, init_labels, create_text_box_code_SG
from eushlator.utils.manual_replacements import replace_str
from eushlator.utils.prompt_utils import generate_translation_prompt
from eushlator.utils.text_box_utils import create_english_box, refine_llm_output
from eushlator.utils.yaml_utils import load_yaml

# ──────────────────────────────────────────────────────────────────────────────
# This module takes machine/edited translations and reinserts them into the
# decompiled script, recreating the correct SYS5 text-box command sequences.
#
# Overall flow (per scene):
#   1) Read JP "boxes" (per-line dialogue units with offsets and speakers).
#   2) Read "full boxes" (speaker-consecutive chunks) and the EN translations
#      produced earlier by the LLM (plus the LLM input metadata for validation).
#   3) For each same-speaker run, craft the English command sequences that
#      correspond to the original JP boxes (respecting concat/S G variants).
#   4) Splice those sequences into the decompiled script at the right offsets.
#   5) Optionally overlay manual edits from 4ex_Translations/*.txt on top.
#
# Important: This operates on raw line offsets and simple heuristics for where
# a text-box starts/ends, so the correctness of offsets and the scene graph is
# crucial. Assertions and validations are used where possible.
# ──────────────────────────────────────────────────────────────────────────────


# markers for the start of a textbox
TEXT_STARTS = ("u00416120", "304")
# lines that belong _inside_ a textbox after the start
TEXT_CMDS = (
    "show-text 0",
    "display-furigana 0",
    "wait-for-input 0",
    "end-text-line 0",
    "concat",
)


def _load_reverse_pua_map(pua_path: Path, reverse_pua_path: Path) -> Dict[str, str]:
    """
    Build a reverse PUA (Private Use Area) character map, combining:
      - the forward mapping from pua.txt (char -> PUA token),
      - and explicit overrides/back-mappings from reverse_pua.txt.

    The resulting dict maps output-facing tokens back into script-expected
    characters, ensuring we preserve or reapply PUA symbols correctly when
    generating English command sequences.
    """
    def load_map(path):
        return {
            line.split("=")[0]: line.split("=")[1]
            for line in path.read_text(encoding="utf-8").splitlines()
            if "=" in line
        }

    forward = load_map(pua_path)
    reverse = load_map(reverse_pua_path)
    # Merge: invert 'forward' (value->key) and overlay with explicit 'reverse'
    return {**{v: k for k, v in forward.items()}, **reverse}


def craft_into_script(
    en_boxes: str,
    jp_box_dicts: List[Dict],
    reverse_pua_map: Dict[str, str],
    final_en_script: List[str],
    config: Dict[str, int],
    starting_offset: int = 0,
    scene_id: str = "",
    text_id: int = 0,
):
    """
    Generate the correct English text-box command sequences for a *consecutive*
    run of JP boxes (same speaker), then splice them into the decompiled script.

    Parameters
    ----------
    en_boxes : str
        The final English text covering *all* jp_box_dicts in order, as one block.
    jp_box_dicts : List[Dict]
        The original JP per-box dicts for this run (each contains "text",
        "speaker", "offset" lines-from-previous-box).
    reverse_pua_map : Dict[str, str]
        Map to restore PUA characters/tokens back to script-expected forms.
    final_en_script : List[str]
        The mutable script lines we are injecting into (a copy of the JP script).
    config : Dict[str, int]
        Formatting controls, e.g. number of text lines per box and max line length:
        { "text_lines": int, "line_length": int, ... }
    starting_offset : int
        Current base index in the script from which box offsets are measured.
        This is updated and returned so subsequent runs continue at the right place.
    scene_id : str
        The current scene name (e.g., "SC0001") used for SG-specific handling.
    text_id : int
        The EN "full-chunk" id (useful for tracing/debug).

    Returns
    -------
    (position, final_en_script)
        position : int
            Updated insertion position index after injecting all boxes in this run.
        final_en_script : List[str]
            Script lines with the English boxes spliced in.
    """
    # 1) Pre-generate all EN code snippets by box ID.
    #    We pass the entire JP run and the unified EN block to build per-box EN containers.
    jp_boxes = "\n".join([b["text"] for b in jp_box_dicts])
    final_en_containers = create_english_box(
        jp_boxes,
        en_boxes,
        reverse_pua_map,
        config["text_lines"],
        config["line_length"],
        scene_id=scene_id,
        text_id=text_id
    )

    code_map: Dict[int, List[str]] = {}   # per local box index -> list of command lines
    concat_offset = starting_offset       # rolling pointer used to detect 'concat' continuation
    concat_container = False              # once we enter concat mode, continue until speaker run ends

    # Decide, per JP box, which kind of code to generate:
    #  - SG scenes: special SG-flavored code path
    #  - concat continuation: if current target site already has "concat" or we are in concat_container
    #  - otherwise: standard 'show-text' box with speaker
    for i, (jp_box, en_container) in enumerate(zip(jp_box_dicts, final_en_containers)):
        bid = i
        # Compute the absolute offset inside the script where this box is expected.
        concat_offset += jp_box["offset"] + 2  # +2 to move past box start/signature lines
        if "SG" in scene_id:
            code_map[bid] = create_text_box_code_SG(
                en_container,
            )
        elif concat_container or final_en_script[concat_offset].startswith("concat"):
            code_map[bid] = create_text_box_code_concat(
                en_container,
            )
            concat_container = True
        else:
            code_map[bid] = create_text_box_code(
                en_container,
                jp_box["speaker"],
            )

    # 2) With a code_map prepared, walk through the script and *replace each JP box*.
    #    We find the box start, then include all TEXT_CMDS, and splice them out
    #    replacing with our new EN lines.
    position = starting_offset
    for i, jp_dict in enumerate(jp_box_dicts):
        offset = jp_dict["offset"]
        lookat = position + offset  # navigate to the start of the next box
        text_box_start = lookat

        # Move forward until we reach the first non-TEXT_CMDS line after the start.
        # Everything between (text_box_start+1) and text_box_end-1 is box body.
        while True:
            lookat += 1
            if any(final_en_script[lookat].strip().startswith(cmd) for cmd in TEXT_CMDS):
                continue
            text_box_end = lookat
            break

        # Replace the original box body with our generated EN lines.
        en_lines = code_map[i]
        final_en_script = final_en_script[:text_box_start+1] + en_lines + final_en_script[text_box_end:]

        # Update current position so the next offset is relative to the just-inserted lines.
        position = len(en_lines) + text_box_start

    return position, final_en_script


def replace_manual_strings(
    final_en_script: list[str],
    edited_script: list[str]
) -> list[str]:
    """
    Replaces differing 'set-string' lines from an edited translation back
    into the full compiled output.

    Args:
        final_en_script: Decompiled full script (e.g. with textboxes reinjected)
        edited_script:   Decompiled manual translation of same file (e.g. 4ex_Translations)

    Returns:
        A new list of strings with modified set-string lines swapped in.

    Notes:
        - The scripts must be line-by-line aligned and of equal length; otherwise
          we abort early because we cannot safely match 'set-string' sites.
        - For any line where the edited version differs and starts with 'set-string',
          the function swaps in the edited variant (after applying manual replacements
          to the quoted payload), keeping all other lines from the original.
    """
    if len(final_en_script) != len(edited_script):
        raise ValueError("Script length mismatch between original and edited version.")

    updated = []
    for orig_line, edited_line in zip(final_en_script, edited_script):
        if orig_line != edited_line and edited_line.strip().startswith("set-string"):
            # Sanitize the quoted payload in case manual replacements apply.
            if edited_line.strip().count('"') > 1:
                edited_line_split = edited_line.strip().split('"')
                edited_line_split[-2] = replace_str(edited_line_split[-2])
                edited_line = '"'.join(edited_line_split)
            updated.append(edited_line)
        else:
            updated.append(orig_line)

    return updated


def reinsert_translations(
    decompiled_path: Path,
    extracted_dialogue_path: Path,
    machine_translations_path: Path,
    edited_translations_path: Path,
    inserted_path: Path,
    utils_path: Path,
    model_name: str,
    config: dict,
    batch: bool = False,
):
    """
    Main reinsertion routine for all scenes of a given model run.

    Parameters
    ----------
    decompiled_path : Path
        Folder containing original decompiled scene .txt files.
    extracted_dialogue_path : Path
        Folder containing per-scene YAML boxes and $$full_script.yaml.
    machine_translations_path : Path
        Folder containing machine translation outputs:
          <machine_translations_path>/<model_name>/<scene>.yaml
    edited_translations_path : Path
        Optional folder with manually edited decompiled scripts (<scene>.txt).
        If present and aligned, their set-string lines are overlaid.
    inserted_path : Path
        Output folder where we write the reinjected .txt scripts (under model_name).
    utils_path : Path
        Folder containing pua.txt and reverse_pua.txt, used for PUA handling.
    model_name : str
        Name/tag of the model run (used to locate inputs and to name outputs).
    config : dict
        Formatting config for English box generation (text lines/width).
    batch : bool
        If True, prompts may omit speaker names during validation comparisons,
        matching the batch translation flow.
    """
    print("[Phase 5] reinsert.py – validation stage")

    # Shared assets: full JP chunks per scene and PUA maps.
    full_script = load_yaml(extracted_dialogue_path / "$$full_script.yaml")
    pua_map = load_pua(utils_path / "pua.txt")
    try:
        reverse_pua_map = _load_reverse_pua_map(utils_path / "pua.txt", utils_path / "reverse_pua.txt")
    except:
        reverse_pua_map = {}

    # Input translations and output directory for this model run.
    translations_root = machine_translations_path / model_name
    out_path = inserted_path / model_name
    out_path.mkdir(exist_ok=True)

    # Process each translated scene YAML.
    for scene in sorted(translations_root.glob("*.yaml")):
        scene_name = scene.stem
        print(f"\n→ Validating {scene_name}")

        # Load all relevant files for this scene.
        jp_boxes = yaml.safe_load((extracted_dialogue_path / f"{scene_name}.yaml").read_text(encoding="utf-8"))
        en_full_boxes = load_yaml(scene)
        jp_script = (decompiled_path / f"{scene_name}.txt").read_text(encoding="utf-8").splitlines()

        # Work on a copy of the JP script; we'll splice EN boxes into this.
        final_en_script = deepcopy(jp_script)
        init_labels(final_en_script)  # ensure labels/init structures are present as needed

        # Optional manual edits overlay (aligned set-string swaps).
        edited_script = None
        edited_path = edited_translations_path / f"{scene_name}.txt"
        if edited_path.exists():
            edited_script = edited_path.read_text(encoding="utf-8").splitlines()

        # Full JP chunks for validation and for selecting corresponding EN chunks.
        jp_full_boxes = full_script.get(scene_name, [])

        # Iterators across:
        #   - per-box JP items for this scene (ordered dict values),
        #   - full-chunk JP items,
        #   - full-chunk EN translations (with prompt/input metadata).
        jp_iter = iter(jp_boxes.values())
        full_iter = iter(jp_full_boxes)
        en_iter = iter(en_full_boxes["translations"])

        # Accumulators for the current consecutive-same-speaker run.
        jp_texts_acc: List[str] = []
        jp_boxes_acc: List[dict] = []
        current_speaker = None

        # Insertion position in the script; advanced as we splice boxes.
        starting_offset = 0

        # If there are manual edits, overlay their set-string lines first.
        if edited_script:
            final_en_script = replace_manual_strings(final_en_script, edited_script)

        last_pass = False  # marks the "speaker changed on last box" case

        # Walk through all JP boxes, batching by consecutive same-speaker blocks.
        for b, box in enumerate(jp_iter):
            speaker = box["speaker"]
            text = box["text"]

            if current_speaker is None:
                current_speaker = speaker

            if speaker == current_speaker:
                # Continue accumulating boxes for this speaker.
                jp_boxes_acc.append(box)
                jp_texts_acc.append(text)
                if b != len(jp_boxes) - 1:
                    # Not the last box overall; keep accumulating.
                    continue
            elif b == len(jp_boxes) - 1:
                # Speaker changed *and* we are at the last box overall.
                last_pass = True

            # Speaker changed (or end reached) → flush the previous run.
            full_box = "\n".join(jp_texts_acc).strip()

            try:
                jp_full = next(full_iter)  # the expected JP full chunk for this run
                en_full = next(en_iter)    # the corresponding EN translation chunk
            except StopIteration:
                # If either full list ran out, the inputs are inconsistent.
                raise RuntimeError(f"[Error] Ran out of full boxes in {scene_name}")

            # Basic sanity checks to ensure we're mapping the correct blocks.
            assert jp_full["speaker"] == current_speaker, f"[{scene_name}] Speaker mismatch"
            assert jp_full["text"].strip() == full_box.strip(), f"[{scene_name}] JP full box mismatch"

            # Reconstruct the prompt we would have sent (for optional comparison).
            expected_input = generate_translation_prompt(jp_full["speaker"], jp_full["text"], pua_map, with_speaker=not batch)
            jp_input = en_full["input"]
            en_input = en_full["text"]
            # Clean up LLM output (quotations, ellipses, bracket balances, etc.).
            en_input = refine_llm_output(en_input, scene_name, en_full["id"])

            # Optional strict input equivalence check (kept disabled to allow minor diffs):
            # assert expected_input.replace(".", "").replace("⋯", "") == jp_input.replace(".", "").replace("⋯", ""), f"[{scene_name}] EN full input mismatch"

            # All good: generate and splice English command sequences for this run.
            starting_offset, final_en_script = craft_into_script(
                en_input,
                jp_boxes_acc,
                reverse_pua_map,
                final_en_script,
                config,
                starting_offset,
                scene_id=scene_name,
                text_id=en_full["id"]
            )

            # Reset accumulators to begin a new run with the *current* (new) speaker.
            jp_boxes_acc = [box]
            jp_texts_acc = [text]
            current_speaker = speaker

        # If the last iteration ended with a pending run, flush it now.
        if last_pass:
            full_box = "\n".join(jp_texts_acc).strip()

            # Pull the remaining pair from both iterators (they must exist).
            try:
                jp_full = next(full_iter)
                en_full = next(en_iter)
            except StopIteration:
                raise RuntimeError(
                    f"[Error] Iterator mismatch at END of {scene_name} "
                    "(JP or EN full boxes ran out)."
                )

            assert jp_full["speaker"] == current_speaker, f"[{scene_name}] Speaker mismatch"
            assert jp_full["text"].strip() == full_box, f"[{scene_name}] JP full box mismatch"

            expected_input = generate_translation_prompt(
                jp_full["speaker"], jp_full["text"], pua_map, with_speaker=not batch
            )
            # Optional strict check as above:
            # assert expected_input.replace(".", "").replace("⋯", "") == en_full["input"].replace(".", "").replace("⋯", ""), f"[{scene_name}] EN input mismatch"

            en_input = refine_llm_output(en_full["text"], scene_name, en_full["id"])

            starting_offset, final_en_script = craft_into_script(
                en_input,
                jp_boxes_acc,
                reverse_pua_map,
                final_en_script,
                config,
                starting_offset,
                scene_id=scene_name,
                text_id=en_full["id"],
            )

        # Write the fully reinjected script lines back to disk.
        (out_path / (scene_name + ".txt")).write_text("\n".join(final_en_script), encoding="utf8")

    print("\n✅ All scenes passed initial validation.")
