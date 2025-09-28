"""
Prepare a master JP/EN dictionary from the various *INIT.txt files.

Output file:
    Eushlator/Utils/dictionary.yaml

Lines are **not** overwritten on re-run; edit dictionary.yaml freely.
"""

from pathlib import Path
import re
import yaml

# Name of the generated dictionary file (stored under Utils/)
UTIL_NAME = "dictionary.yaml"
# Offsets/addresses metadata used to slice per-*INIT group ranges.
OFFSETS_YAML = "dictionary_offsets.yaml"

# ── regex for string extraction ────────────────────────────────────────────
# Matches lines like:
#   set-string (global-string 001A) "学院"
# Captures the hex address ("001A") and the quoted string ("学院").
STR_RE = re.compile(
    r'set-string\s+\(global-string\s+([0-9a-fA-F]+)\)\s+"([^"]+)"'
)

# Pattern cache for locating files like AMINIT.txt, optionally with a prefix:
#   AMINIT.txt
#   $1$AMINIT.txt
#   $12$AMINIT.txt
PATTERN_CACHE: dict[str, re.Pattern] = {}


def file_pattern(base: str) -> re.Pattern:
    """
    Return a compiled regex to match a specific INIT filename (with optional
    $<n>$ prefix), caching compiled patterns by `base`.

    Example:
      base="AMINIT" -> matches "$11$AMINIT.txt", "AMINIT.txt", etc.
    """
    if base not in PATTERN_CACHE:
        PATTERN_CACHE[base] = re.compile(
            rf'(?:\$\d{{1,2}}\$)?{re.escape(base)}\.txt$', re.IGNORECASE
        )
    return PATTERN_CACHE[base]


# ── language heuristic ────────────────────────────────────────────────────
def is_english(s: str) -> bool:
    """
    Heuristic to decide if a string is likely English:
      - contains at least one ASCII letter
      - contains no CJK/kana characters
    Used to determine whether a candidate translation looks valid or should
    be replaced with a placeholder.
    """
    has_lat = any("A" <= c <= "Z" or "a" <= c <= "z" for c in s)
    has_cjk = any(
        "\u3040" <= c <= "\u30FF" or  # kana
        "\u4E00" <= c <= "\u9FFF"     # CJK
        for c in s
    )
    return has_lat and not has_cjk


# ── loader for offsets.yaml ────────────────────────────────────────────────
def load_offsets(util_path: Path) -> dict:
    """
    Load the per-file address ranges from Utils/dictionary_offsets.yaml.

    Expected shape:
      {
        "AMINIT": { "start": 0x..., "end": 0x... | null },
        "BMINIT": { "start": 0x..., ... },
        ...
      }
    """
    f = util_path / OFFSETS_YAML
    if not f.exists():
        raise FileNotFoundError(f"Missing {OFFSETS_YAML} in {util_path}")
    with f.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


# ── extract strings from a folder ─────────────────────────────────────────
def gather_strings(folder: Path, base: str,
                   start: int, end: int | None) -> dict[int, str]:
    """
    Scan a folder for *INIT.txt files of a given base name and extract
    string entries within the [start, end] address window.

    Returns:
      { idx: jp_str }, where idx = (address - start) is a 0-based offset
      within the INIT group.

    Notes:
      - If duplicate indices are encountered with conflicting JP strings,
        the first seen value is kept and a warning is printed.
    """
    out: dict[int, str] = {}
    pat = file_pattern(base)

    for txt in folder.glob("*.txt"):
        if not pat.match(txt.name):
            continue
        for line in txt.read_text(encoding="utf-8").splitlines():
            m = STR_RE.search(line)
            if not m:
                continue
            addr, jp = int(m.group(1), 16), m.group(2)
            if addr < start or (end is not None and addr > end):
                continue
            idx = addr - start
            if idx in out and out[idx] != jp:
                print(
                    f"[Warn] {base}: ID {idx} already '{out[idx]}' "
                    f"ignoring conflicting '{jp}' in {txt.name}"
                )
                continue
            out[idx] = jp
    return out


# ── main driver ───────────────────────────────────────────────────────────
def run_prepare_dictionary(decompiled: Path,
                           translations: Path,
                           utils_path: Path):
    """
    Build a JP/EN dictionary by scanning decompiled *INIT files for JP strings
    and optionally overlaying English strings from a parallel translations
    directory.

    Behavior:
      - If Utils/dictionary.yaml already exists, it is preserved (no overwrite),
        to allow manual curation without being clobbered on reruns.
      - For each INIT group defined in dictionary_offsets.yaml:
          * Extract JP strings from `decompiled` within the given address range.
          * Extract candidate EN strings from `translations` for the same range.
          * Keep EN only if `is_english(...)` returns True, otherwise set "placeholder".
      - Write the aggregated result to Utils/dictionary.yaml.
    """
    dst = utils_path / UTIL_NAME
    if dst.exists():
        print(f"[Skip] {dst} already exists – keeping your edits.")
        return

    offsets = load_offsets(utils_path)
    dictionary: dict[str, dict[int, dict[str, str]]] = {}

    for base, off in offsets.items():
        start = off["start"]
        end   = off.get("end")
        # Gather original JP strings from the decompiled sources.
        jp_map = gather_strings(decompiled, base, start, end)
        if not jp_map:
            continue

        # Gather any available EN strings from user-provided/edited translations.
        en_raw = gather_strings(translations, base, start, end)

        # Combine JP with EN (or placeholder).
        combined = {}
        for idx, jp in jp_map.items():
            en_val = en_raw.get(idx, "")
            combined[idx] = {
                "jp": jp,
                "en": en_val if is_english(en_val) else "placeholder",
            }
        # Keep a stable ordering by numeric index for readability/diffs.
        dictionary[base] = dict(sorted(combined.items()))

    if not dictionary:
        print("[Warn] No strings found – nothing written.")
        return

    # Ensure Utils/ exists and write the final dictionary YAML.
    utils_path.mkdir(parents=True, exist_ok=True)
    with dst.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(
            dictionary,
            fh,
            allow_unicode=True,
            sort_keys=False,
            width=200,
        )

    print(f"[Info] Dictionary written → {dst}")


# ── CLI (optional) ────────────────────────────────────────────────────────
if __name__ == "__main__":
    # Simple command-line entry for ad-hoc generation:
    #   python utils/prepare_dictionary.py <decompiled_dir> <translations_dir> <utils_dir>
    import sys
    if len(sys.argv) != 4:
        print("Usage: python utils/prepare_dictionary.py "
              "<decompiled_dir> <translations_dir> <utils_dir>")
        sys.exit(1)

    run_prepare_dictionary(
        Path(sys.argv[1]),
        Path(sys.argv[2]),
        Path(sys.argv[3]),
    )
