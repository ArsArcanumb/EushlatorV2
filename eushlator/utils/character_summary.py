from __future__ import annotations
from pathlib import Path
import re

# ─── regex & constants ────────────────────────────────────────────────────
# Match lines of the form:
#   set-string (global-string <hex_addr>) "<string>"
# Capturing groups:
#   1) the hex address
#   2) the quoted string
_NAME_LINE = re.compile(
    r'set-string\s+\(global-string\s+([0-9a-fA-F]+)\)\s+"([^"]+)"'
)

# CIINIT text files (optionally prefixed by $..$ variants), case-insensitive.
CI_PATTERN      = re.compile(r'(?:\$\d{1,2}\$)?CIINIT\.txt$', re.IGNORECASE)

# Address window that marks "name" entries in CIINIT, inclusive.
# Any following set-string entries with addresses ABOVE NAME_ADDR_MAX are
# considered part of that character's multi-line description until the
# address falls back into the "name" window again.
NAME_ADDR_MIN   = 0x4B53        # ← updated
NAME_ADDR_MAX   = 0x4F3E        # ← updated


def _scan_ciinit(folder: Path, target_name: str) -> str:
    """
    Scan all CIINIT variants in *folder* for *target_name* and return the
    concatenated description (JP text). Empty string if not found.

    Algorithm:
      1) Iterate all *.txt files matching CI_PATTERN.
      2) Walk each file line-by-line, looking for a "name" line whose
         address lies within [NAME_ADDR_MIN, NAME_ADDR_MAX] and whose
         payload equals *target_name*.
      3) Starting after that name line, collect subsequent set-string payloads
         whose addresses are > NAME_ADDR_MAX; stop when the address returns
         to the name range (≤ NAME_ADDR_MAX). Join with newlines and return.
    """
    for txt in sorted(folder.glob("*.txt")):
        if not CI_PATTERN.match(txt.name):
            continue

        lines = txt.read_text(encoding="utf-8", errors="ignore").splitlines()
        i, n = 0, len(lines)

        while i < n:
            m = _NAME_LINE.search(lines[i])
            if not m:
                i += 1
                continue

            addr = int(m.group(1), 16)
            string = m.group(2)

            # Is this the target character name within the "name" address window?
            if NAME_ADDR_MIN <= addr <= NAME_ADDR_MAX and string == target_name:
                i += 1
                desc: list[str] = []

                # Collect follow-up description lines until the address falls
                # back into the name range (≤ NAME_ADDR_MAX).
                while i < n:
                    m2 = _NAME_LINE.search(lines[i])
                    if not m2:
                        i += 1
                        continue

                    addr2 = int(m2.group(1), 16)
                    if addr2 <= NAME_ADDR_MAX:
                        break
                    desc.append(m2.group(2))
                    i += 1

                return "\n".join(desc).strip()

            i += 1
    return ""


def get_description(language: str,
                    character: str,
                    decompiled_folder_path: str | Path,
                    translations_folder_path: str | Path) -> str:
    """
    Fetch character description from CIINIT files.

    Behavior:
      - If language == "en": search in translations_folder_path, return the
        found description or "" if none exists. (No JP fallback here.)
      - Otherwise (JP or any other): search in decompiled_folder_path.

    Returns: description text (possibly multi-line) or "" if not found.
    """
    decomp_dir = Path(decompiled_folder_path)
    trans_dir  = Path(translations_folder_path)

    if language.lower() == "en":
        en_desc = _scan_ciinit(trans_dir, character)
        if en_desc:
            return en_desc
        return ""

    return _scan_ciinit(decomp_dir, character)
