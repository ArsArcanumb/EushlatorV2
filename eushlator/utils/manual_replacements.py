from pathlib import Path
from typing import Dict

from eushlator.utils.yaml_utils import load_yaml


# Global, process-wide replacement dictionary.
# Populated once via `load_replacement_file()` and then reused by `replace_str()`.
rep_dict: Dict = {}


def load_replacement_file(utils_path: Path):
    """
    Load the manual replacements dictionary from:
        <utils_path>/manual_replacements_dict.yaml
    and cache it in the module-level `rep_dict`.

    Behavior:
      - If `rep_dict` is already populated, return it as-is (no re-read).
      - If the YAML file exists, load it and store it globally, then return it.
      - If the file does not exist, return an empty dict.

    Notes:
      - The YAML is expected to be a simple mapping of: old -> new
      - Ordering of replacements follows the dict order as loaded by `load_yaml`.
        If keys overlap (e.g., "Undine" and "Infected Undine"), order may affect results.
    """
    global rep_dict
    if rep_dict:
        return rep_dict
    rep_file = utils_path / "manual_replacements_dict.yaml"
    if rep_file.exists():
        new_rep_dict = load_yaml(rep_file)
        rep_dict = new_rep_dict
        return rep_dict
    return {}


def replace_str(txt: str) -> str:
    """
    Apply all configured manual replacements to `txt`.

    For each (old, new) pair in `rep_dict`, perform a simple, case-sensitive
    `str.replace(old, new)` across the entire string. This is not regex-based
    and does not consider word boundaries.

    Caveats:
      - Replacement order follows the dict iteration order.
      - Overlapping patterns may cascade (a replacement may enable a later one).
    """
    global rep_dict
    for old, new in rep_dict.items():
        txt = txt.replace(old, new)
    return txt
