# utils/pua.py   (replaces the old version)
"""
PUA (Private Use Area) utilities.

This module scans decompiled text files to detect *contiguous runs* of
characters in Unicode Private Use Areas and writes the unique runs to
`Utils/pua.txt`. These runs are later used to preserve/restore special
glyphs during translation/reinsertion.

Notes:
- We treat a "run" as one or more consecutive PUA code points.
- The scan is idempotent: if `pua.txt` already exists, we skip work.
"""

import os
import re
from pathlib import Path


def is_private_use(char: str) -> bool:
    """
    Return True if `char` is in any Unicode Private Use Area.

    Ranges covered:
      • BMP PUA:            U+E000..U+F8FF
      • Supplementary PUA-A U+F0000..U+FFFFD
      • Supplementary PUA-B U+100000..U+10FFFD
    """
    if not char:
        return False
    code = ord(char)
    return (
        0xE000   <= code <= 0xF8FF    or   # Basic PUA (BMP)
        0xF0000  <= code <= 0xFFFFD   or   # Supplementary Plane-A
        0x100000 <= code <= 0x10FFFD       # Supplementary Plane-B
    )


# Pre-compiled regex that matches one or more consecutive PUA characters.
# The escapes \uXXXX (BMP) and \U00XXXXXX (supplementary planes) are used
# to express the ranges portably in source code.
PUA_RUN = re.compile(
    r"["                     # character class
    r"\uE000-\uF8FF"         #   Basic PUA (BMP)
    r"\U000F0000-\U000FFFFD" #   Supplementary PUA-A
    r"\U00100000-\U0010FFFD" #   Supplementary PUA-B
    r"]+"
)


def collect_pua_symbols(input_folder: Path, output_folder: Path) -> None:
    """
    Scan all *.txt files under `input_folder` and collect *unique* contiguous
    runs of PUA characters. Write the runs (one per line) to:
        <output_folder>/pua.txt

    Behavior:
      • Fast path check: lines without any PUA chars are skipped quickly.
      • If the output file already exists, the function returns immediately
        (idempotent), assuming the collection has been done before.
      • Output is sorted deterministically by (first code point, length).

    Args:
        input_folder: Root folder to walk (recursively) for *.txt files.
        output_folder: Folder where 'pua.txt' will be created if missing.
    """
    output_file = output_folder / "pua.txt"

    # Idempotency guard: do nothing if the file already exists.
    if os.path.exists(output_file):
        return

    print("[Info] Collecting PUA characters from Decompiled folder…")

    pua_runs: set[str] = set()

    # Walk the tree and process only .txt files (case-insensitive).
    for root, _, files in os.walk(input_folder):
        for file in files:
            if not file.lower().endswith(".txt"):
                continue

            file_path = os.path.join(root, file)
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    for line in f:
                        # Quick reject: if no char in the line is PUA, skip regex.
                        if not any(is_private_use(ch) for ch in line):
                            continue

                        # Collect every contiguous PUA run in the line.
                        for match in PUA_RUN.finditer(line):
                            pua_runs.add(match.group(0))

            except Exception as e:
                # Non-fatal: keep scanning other files.
                print(f"[Warn] Error reading {file_path}: {e}")

    # Write unique runs, sorted for determinism and readability.
    with open(output_file, "w", encoding="utf-8") as out:
        for run in sorted(pua_runs, key=lambda s: (ord(s[0]), len(s))):
            out.write(f"{run}\n")

    print(f"[Info] Found {len(pua_runs)} unique PUA word(s).  Written to: {output_file}")
