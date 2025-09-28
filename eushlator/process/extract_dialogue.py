# eushlator/process/extract_dialogue.py
"""
• Detect text-boxes in a decompiled SYS5 script
• Resolve speaker IDs via Utils/names.yaml
• Return / write YAML ready for 3_ExtractedDialogue
"""

from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import List, Sequence, Dict
import re
import yaml

# ─── command patterns ─────────────────────────────────────────────────────
# Lines that may belong to a text-box payload. We treat the presence of these
# commands as "inside a box" for the simple state machine below.
TEXT_CMDS = (
    "show-text 0",
    "display-furigana 0",
    "wait-for-input 0",
    "end-text-line 0",
    "concat",
)

# Heuristic to detect when the game script writes a speaker-id into (local-ptr 0)
# using a table at global-int 6623. We set a flag and parse the next `mov` to
# capture the actual numeric speaker id (hex).
LOOKUP_6623 = re.compile(
    r"lookup-array\s+\(local-ptr 0\)\s+\(global-int 6623"
)
# When `waiting_mov` is set, we look for the subsequent mov of a hex constant
# into (local-ptr 0), e.g. `mov (local-ptr 0) 1a` (hex).
MOV_LOCAL_PTR = re.compile(
    r"mov\s+\(local-ptr 0\)\s+([0-9a-fA-F]+)"
)

# Pull the payload from: show-text 0 "...."
SHOW_RE = re.compile(r'show-text 0\s+"(.*)"')
# Pull the pair from: display-furigana 0 "漢字" "かんじ"
FURI_RE = re.compile(r'display-furigana 0\s+"([^"]+)"\s+"([^"]+)"')

# Very light unescape map for strings stored in the script text.
ESC = {r'\"': '"', r"\\": "\\"}

# Scene file names we consider valid input:
# Optional $<1-2 digits>$ prefix, then SC|SN|SP|SG, then exactly 4 digits, ".txt".
VALID_SCENE = re.compile(r'(?:\$\d{1,2}\$)?(SC|SN|SP|SG)\d{4}\.txt$', re.IGNORECASE)

# ─── dataclass ────────────────────────────────────────────────────────────
@dataclass
class TextBox:
    idx: int                  # 1-based index of the box within a file
    speaker: str              # mapped speaker name (fallback "Narrator")
    offset: int               # line distance from the previous box end
    jp_text: str              # concatenated JP text extracted from the box
    raw: Sequence[str]        # raw lines of the detected box (for debugging)


# ─── helpers ──────────────────────────────────────────────────────────────
def unesc(s: str) -> str:
    """Very small unescape for \" and \\ occurrences."""
    for k, v in ESC.items():
        s = s.replace(k, v)
    return s


def join_text(lines: Sequence[str]) -> str:
    """
    Collapse a sequence of box lines to a single JP string:
      - collect `show-text 0 "..."` payloads
      - render `display-furigana 0 "漢字" "ふりがな"` as 漢字(ふりがな)
    """
    out: list[str] = []
    for ln in lines:
        if ln.lstrip().startswith("show-text 0"):
            m = SHOW_RE.search(ln)
            if m:
                out.append(unesc(m.group(1)))
        elif ln.lstrip().startswith("display-furigana 0"):
            m = FURI_RE.search(ln)
            if m:
                kanji, furi = m.groups()
                out.append(f"{kanji}({furi})")
    return "".join(out)


def is_box_start(line, file_name):
    """
    Identify the beginning of a text-box.
    Heuristics:
      - exact opcode "u00416120"
      - numeric marker "304"
      - or, for SG* files, a particular call pattern
    """
    box_start = (line == "u00416120"
                 or line == "304"
                 or ("SG" in file_name and line == "call label_00004c70"))
    return box_start


# ─── main algorithm ───────────────────────────────────────────────────────
def extract_boxes(path: Path, name_map: Dict[int, str]) -> List[TextBox]:
    """
    Parse a decompiled scene .txt and extract dialogue boxes.

    The parser is a small state machine:
      - Track when a speaker lookup occurs (LOOKUP_6623) and capture the next
        `mov (local-ptr 0) <hex>` value as `pending_speaker`.
      - Detect box starts (is_box_start). After the start, ensure the next line
        is `show-text` to avoid false positives.
      - While inside a box, accumulate lines that start with known TEXT_CMDS.
      - On any other line, close the current box and attempt to start a new one.

    Each closed box becomes a TextBox with:
      - idx: 1-based sequence id
      - speaker: resolved via `name_map` from `pending_speaker` (or "Narrator")
      - offset: distance in lines since the end of the previous box
      - jp_text: result of `join_text` on the buffered lines (skipping the opener)
      - raw: entire buffered lines for reference
    """
    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()

    boxes: List[TextBox] = []

    pending_speaker: int | None = None      # captured since last box (hex id)
    waiting_mov = False                     # if True, parse next mov as speaker id

    in_box = False                          # we are currently buffering a box
    after_u = False                         # saw a start marker; waiting for TEXT_CMDS
    buf: list[str] = []                     # raw lines of the current box
    start_idx = -1                          # index where the current box started
    last_end = 0                            # last line index at which a box ended

    def close(end_before: int):
        """
        Close the current box (if any), emit a TextBox, and reset local state.
        `end_before` is the index of the last line belonging to the box.
        """
        nonlocal in_box, after_u, buf, start_idx, last_end, pending_speaker
        if in_box and buf:
            speaker_name = name_map.get(pending_speaker, "Narrator")
            offset = start_idx - last_end if last_end >= 0 else 0
            jp = join_text(buf[1:])                 # skip the opener (e.g., u00416120)
            boxes.append(TextBox(len(boxes) + 1, speaker_name, offset, jp, tuple(buf)))
            last_end = end_before
        in_box = after_u = False
        buf[:] = []
        start_idx = -1
        pending_speaker = None                      # reset after use

    for idx, ln in enumerate(lines):
        st = ln.strip()

        # --- speaker tracking ---
        # Detect the table lookup that precedes setting the speaker id.
        if LOOKUP_6623.match(st):
            waiting_mov = True
        # Immediately after, capture the hex moved into (local-ptr 0).
        elif waiting_mov:
            mv = MOV_LOCAL_PTR.match(st)
            if mv:
                pending_speaker = int(mv.group(1), 16)
            waiting_mov = False

        # --- box state machine ---
        if not in_box:
            # Potential start: require that the *next* line is show-text to confirm.
            if is_box_start(st, path.stem):
                if not lines[idx+1].startswith("show-text"):
                    continue
                after_u = True
                buf = [ln]
                start_idx = idx
                continue
            # After seeing a start marker, we accept only TEXT_CMDS to enter the box.
            if after_u:
                if any(st.startswith(cmd) for cmd in TEXT_CMDS):
                    in_box = True
                    buf.append(ln)
                else:
                    # False start, reset.
                    after_u = False
                    buf = []
        else:
            # While inside a box, keep lines that are part of text content.
            if any(st.startswith(cmd) for cmd in TEXT_CMDS):
                buf.append(ln)
            else:
                # Exiting a box. Close it and immediately check if a new one starts here.
                close(idx - 1)
                if is_box_start(st, path.stem):
                    if not lines[idx + 1].startswith("show-text"):
                        continue
                    after_u = True
                    buf = [ln]
                    start_idx = idx

    # Close a trailing open box at EOF (if present).
    close(len(lines) - 1)
    return boxes


# ─── public API ───────────────────────────────────────────────────────────
def run_extract_dialogue(decompiled_txt: Path,
                         names_yaml: Path,
                         out_yaml: Path) -> None:
    """
    Extract text-boxes from a single decompiled scene file and write YAML.

    names.yaml format:
      {
        "<hex_id>": { "jp": "<speaker name>", ... },
        ...
      }
    """
    name_data = yaml.safe_load(names_yaml.read_text(encoding="utf-8")) or {}
    # Map numeric id (int) -> JP speaker name.
    name_map = {int(k): v["jp"] for k, v in name_data.items()}

    boxes = extract_boxes(decompiled_txt, name_map)

    out_yaml.parent.mkdir(parents=True, exist_ok=True)
    with out_yaml.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(
            {
                b.idx: {
                    "text": b.jp_text,
                    "speaker": b.speaker,
                    "offset": b.offset,
                }
                for b in boxes
            },
            fh,
            allow_unicode=True,
            sort_keys=False,
            width=200,
        )
    print(f"[Info] {len(boxes)} text boxes → {out_yaml}")


def run_extract_folder(in_dir: Path,
                       utils_dir: Path,
                       out_dir: Path) -> None:
    """
    Process all SC*.txt files in a folder into YAML files,
    skipping files that already exist in the output folder.

    File selection:
      - Only files matching VALID_SCENE are considered (SC/SN/SP/SG + 4 digits,
        optionally prefixed with `$..$`).
    """
    names_yaml = utils_dir / "names.yaml"
    name_data = yaml.safe_load(names_yaml.read_text(encoding="utf-8")) or {}
    # Build speaker lookup once for the whole folder.
    name_map = {int(k): v["jp"] for k, v in name_data.items()}

    out_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    skipped = 0

    for txt_file in sorted(in_dir.glob("*.txt")):
        if not VALID_SCENE.match(txt_file.name):
            continue

        scene_id = txt_file.stem
        out_file = out_dir / f"{scene_id}.yaml"

        if out_file.exists():
            skipped += 1
            continue

        boxes = extract_boxes(txt_file, name_map)
        with out_file.open("w", encoding="utf-8") as fh:
            yaml.safe_dump(
                {
                    b.idx: {
                        "text": b.jp_text,
                        "speaker": b.speaker,
                        "offset": b.offset,
                    }
                    for b in boxes
                },
                fh,
                allow_unicode=True,
                sort_keys=False,
                width=200,
            )
        count += 1

    print(f"[Info] Extracted {count} scene(s), skipped {skipped} → {out_dir}")


# ─── tiny demo harness ────────────────────────────────────────────────────
def _demo() -> None:
    """Minimal local test: run extraction for a single known file."""
    root = Path.cwd()
    run_extract_dialogue(
        root / "2_Decompiled" / "$11$SP6932.txt",
        root / "Utils" / "names.yaml",
        root / "3_ExtractedDialogue" / "$11$SP6932.yaml",
    )


if __name__ == "__main__":
    _demo()
