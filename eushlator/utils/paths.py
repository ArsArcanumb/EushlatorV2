from pathlib import Path

# Standard project subfolders created under a given base 'Eushlator' directory.
# Order is not functionally important here; it's just the conventional layout:
#   1_Extracted           - raw assets extracted from the game install
#   2_Decompiled          - decompiled script/resource .txt files
#   3_ExtractedDialogue   - per-scene YAML with parsed dialogue boxes
#   4_MachineTranslations - machine translation outputs per model
#   4ex_Translations      - manually edited translation .txt files (optional)
#   5_Inserted            - decompiled scripts with EN text reinserted
#   6_Recompiled          - assembled .BIN files ready to ship
#   Utils                 - helper files (names.yaml, dictionary.yaml, pua maps, etc.)
FOLDERS = ["1_Extracted", "2_Decompiled", "3_ExtractedDialogue", "4_MachineTranslations", "4ex_Translations", "5_Inserted", "6_Recompiled", "Utils"]


def ensure_folders_exist(base_path: Path):
    """
    Ensure the standard project directory structure exists under `base_path`.

    Behavior:
      - Creates the base directory if missing (non-recursive).
      - Creates each known subfolder (non-recursive) within that base.
    Notes:
      - This intentionally does not pass `parents=True`; callers should ensure
        that the parent of `base_path` exists if needed.
    """
    base_path.mkdir(exist_ok=True)
    for folder in FOLDERS:
        (base_path / folder).mkdir(exist_ok=True)
