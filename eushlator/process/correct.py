# This module post-processes translation script files by applying manual
# replacement rules only to the quoted payload of lines that look like:
#
#   set-string <id> "<text>"
#

import os
from pathlib import Path
from typing import List, Tuple

from eushlator.utils.manual_replacements import replace_str


def correct_file(lines: List[str]) -> Tuple[List[str], bool]:
    """
    Apply manual replacements to a list of lines.

    Logic:
      - Only lines that (after strip) start with "set-string" and contain at least
        two quote characters (") are considered candidates.
      - The line is split by the quote character ("); the penultimate token
        `[-2]` is treated as the quoted text payload.
      - That payload is passed through `replace_str()`. If it changes, we
        substitute it back and mark that something was corrected.
      - All lines are returned with exactly one trailing newline preserved/added.

    Returns:
      (corrected_lines, anything_corrected)
        corrected_lines: the possibly edited lines (with trailing newlines normalized)
        anything_corrected: True iff at least one replacement was performed

    Notes / Assumptions:
      - This is a simple quote-splitting approach and assumes the payload does
        not contain escaped quotes that should remain inside the string. For
        example, a payload like:  "Hello \"world\""  would break this parser.
        The current logic intentionally does NOT handle escaping—matching the
        original behavior.
    """
    corrected_lines = []
    anything_corrected = False

    for line in lines:
        edited_line = line
        # Only consider simple 'set-string' lines with at least two quotes
        if edited_line.strip().startswith("set-string") and edited_line.strip().count('"') > 1:
            edited_line_split = edited_line.strip().split('"')
            # We expect something like: [prefix, <payload>, suffix...] -> len >= 3
            if len(edited_line_split) >= 3:
                original_text = edited_line_split[-2]
                replaced_text = replace_str(original_text)
                if replaced_text != original_text:
                    # Log what changed for transparency/debugging
                    print(f"\tReplaced:\n\t  {original_text}\n\t  {replaced_text}")
                    anything_corrected = True
                # Replace the payload and reassemble the line
                edited_line_split[-2] = replaced_text
                edited_line = '"'.join(edited_line_split)
        # Ensure exactly one trailing newline
        corrected_lines.append(edited_line + ("\n" if not edited_line.endswith("\n") else ""))

    return corrected_lines, anything_corrected


def run_corrections(translations_path: Path, out_path: Path):
    """
    Scan `translations_path` for files ending with 'INIT.txt', apply corrections,
    and write results to `out_path` only if any change occurred.

    Behavior:
      - If an already corrected file exists at `out_path/<file>`, that becomes
        the source for subsequent correction runs. This makes the operation
        idempotent and allows iterative improvements without re-reading the
        original.
      - Files are written to `out_path` only when at least one replacement was
        made, avoiding unnecessary writes/timestamp changes.
    """
    # Gather only INIT.txt files (convention for target scripts)
    files_to_correct = [f for f in os.listdir(translations_path) if f.endswith("INIT.txt")]
    print("Running set-string corrections.")

    for file_name in files_to_correct:
        out_file_path = out_path / file_name
        # Prefer the previously corrected file if it exists; otherwise read the original
        src_file_path = out_file_path if out_file_path.exists() else translations_path / file_name
        print(f"Running set-string corrections for {src_file_path}")

        with open(src_file_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        corrected_lines, anything_done = correct_file(lines)

        # Ensure the destination directory exists before writing
        out_path.mkdir(parents=True, exist_ok=True)
        if anything_done:
            with open(out_file_path, "w", encoding="utf-8") as f:
                f.writelines(corrected_lines)
