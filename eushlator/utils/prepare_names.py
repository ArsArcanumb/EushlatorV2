from pathlib import Path
import re

from eushlator.utils.character_summary import get_description
from eushlator.utils.yaml_utils import save_yaml

# ─── Purpose ────────────────────────────────────────────────────────────────
# Parse decompiled CNINIT*.txt files to build a speaker name map (ID → JP/EN),
# optionally enrich with short character summaries, and write Utils/names.yaml.
#
# Flow:
#   1) Scan decompiled CNINIT variants to collect canonical JP names.
#   2) Optionally read same IDs from translated CNINIT variants to obtain EN.
#   3) Validate EN strings (must look English-ish), else set "placeholder".
#   4) Generate JP/EN summaries via get_description().
#   5) Write an ordered YAML mapping: { id: {jp, en, jp_summary, en_summary} }.
#
# No files are overwritten if Utils/names.yaml already exists (keeps user edits).

# ─── constants ─────────────────────────────────────────────────────────────
# Matches lines like:
#   set-string (global-string a5b1) "ミカ"
# Captures the hex label (e.g., a5b1) and the quoted name (e.g., ミカ)
NAME_RE = re.compile(
    r'set-string\s+\(global-string\s+([0-9a-fA-F]+)\)\s+"([^"]+)"'
)

# The base label address used to derive a 0-based speaker ID:
#   speaker_id = int(label_hex, 16) - BASE_ADDR
BASE_LABEL = "a5a3"
BASE_ADDR = int(BASE_LABEL, 16)

# CNINIT variants we recognise → CNINIT.txt , $1$CNINIT.txt , $12$CNINIT.txt …
# (Optional "$<1-2 digits>$" prefix, case-insensitive)
CNINIT_PAT = re.compile(r'(?:\$\d{1,2}\$)?CNINIT\.txt$', re.IGNORECASE)


# ─── helpers ───────────────────────────────────────────────────────────────
def is_english(text: str) -> bool:
    """
    Heuristic: treat a string as English if it contains at least one Latin
    letter and no Japanese/CJK characters.
    """
    has_latin = any("A" <= c <= "Z" or "a" <= c <= "z" for c in text)
    has_cjk   = any(
        "\u3040" <= c <= "\u30FF" or  # Hiragana / Katakana
        "\u4E00" <= c <= "\u9FFF"     # CJK Unified Ideographs
        for c in text
    )
    return has_latin and not has_cjk


def gather_names_from_folder(folder: Path) -> dict[int, str]:
    """
    Scan *all* CNINIT variants in `folder` and return {speaker_id: jp_name}.

    Logic:
      • Iterate *.txt files, filter by CNINIT_PAT.
      • For each matching line, extract (hex label, name).
      • speaker_id = int(label_hex, 16) - BASE_ADDR ; ignore non-positive.
      • If an ID appears multiple times with different names, warn and keep
        the first occurrence (later conflicts are ignored).
    """
    names: dict[int, str] = {}

    for txt_file in folder.glob("*.txt"):
        if not CNINIT_PAT.match(txt_file.name):
            continue

        for line in txt_file.read_text(encoding="utf-8").splitlines():
            m = NAME_RE.search(line)
            if not m:
                continue

            label_hex, jp_name = m.groups()
            speaker_id = int(label_hex, 16) - BASE_ADDR
            if speaker_id <= 0:
                # Skip invalid/legacy/offset entries that would yield non-positive IDs.
                continue

            if speaker_id in names:
                if names[speaker_id] != jp_name:
                    # Conflict: same ID mapped to different strings in different files.
                    # Keep the original and emit a warning.
                    print(
                        f"[Warn] ID {speaker_id} already mapped to "
                        f"'{names[speaker_id]}', "
                        f"ignoring conflicting '{jp_name}' "
                        f"in {txt_file.name}"
                    )
                continue

            names[speaker_id] = jp_name

    return names


# ─── main entry point ──────────────────────────────────────────────────────
def run_prepare_names(decompiled_path: Path,
                      translations_path: Path,
                      utils_path: Path) -> None:
    """
    Build Utils/names.yaml from decompiled and (optionally) translated CNINIT files.

    Inputs:
      • decompiled_path:     folder with decompiled CNINIT*.txt (JP ground truth)
      • translations_path:   folder with translated CNINIT*.txt (optional EN)
      • utils_path:          target folder for names.yaml

    Behavior:
      • If names.yaml already exists, do nothing (preserve user edits).
      • Otherwise, construct a mapping for all discovered speaker IDs.
      • EN value is taken from translations if `is_english()` passes; else "placeholder".
      • Add brief JP/EN summaries via get_description() for downstream prompts.
      • Write an ordered YAML (by speaker_id).
    """
    out_file = utils_path / "names.yaml"
    if out_file.exists():
        print(f"[Skip] {out_file} already exists – keeping your edits.")
        return

    # Canonical JP names from the decompiled CNINIT files.
    jp_names = gather_names_from_folder(decompiled_path)
    if not jp_names:
        print("[Warn] No CNINIT files found in decompiled folder – aborting.")
        return

    # Optional EN names sourced from translated CNINIT files.
    en_names_raw = gather_names_from_folder(translations_path)

    merged: dict[int, dict[str, str]] = {}
    for sid, jp in jp_names.items():
        en_raw = en_names_raw.get(sid, "")
        # Keep only plausible English; otherwise mark as "placeholder".
        en_val = en_raw if is_english(en_raw) else "placeholder"

        # Summaries can use the surrounding project files to produce short bios.
        summary_jp = get_description(
            "jp",
            jp,
            decompiled_path,
            translations_path,
        )
        summary_en = get_description(
            "en",
            en_val,
            decompiled_path,
            translations_path,
        )

        merged[sid] = {
            "jp": jp,
            "en": en_val,
            "jp_summary": summary_jp,
            "en_summary": summary_en,
        }

    # Stable ordering by numeric speaker id.
    ordered = dict(sorted(merged.items()))

    utils_path.mkdir(parents=True, exist_ok=True)
    save_yaml(out_file, ordered)

    print(f"[Info] Wrote {len(ordered)} speaker entry(ies) → {out_file}")


# ─── CLI convenience (optional) ────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    if len(sys.argv) != 4:
        print("Usage: python utils/prepare_names.py <decompiled_dir> "
              "<translations_dir> <utils_dir>")
        sys.exit(1)
    run_prepare_names(Path(sys.argv[1]),
                      Path(sys.argv[2]),
                      Path(sys.argv[3]))
