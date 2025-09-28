import re
from pathlib import Path

# This module generates SYS5 script command sequences for text boxes and
# locates helper labels present in the decompiled code to properly chain
# additional boxes (e.g., when splitting a long EN text across multiple boxes).

# ── Heuristic templates used to locate labels in a script ────────────────
# When these exact sequences are found in the code, we assume the *preceding*
# line is a label we can `call` to initialize state for the next text box.

new_char_text_box_label_template = [
    "add (local-int 0) 2 (global-int 6625)"
]

new_narr_text_box_label_template = [
    "u004160D0",
    "mov (global-int 6622) 1"
]

# ── Quoting helpers ───────────────────────────────────────────────────────
# Some engines/scripts are sensitive to double quotes inside string literals.
# The code replaces any `"` occurring in the EN line with `'` to stay safe.
shitter = '"'
safe_shitter = "'"

# ── Discovered labels (set by init_labels) ────────────────────────────────
current_char_label = ""
current_narr_label = ""


def init_labels(full_code):
    """
    Populate global label names by scanning the full script lines and
    capturing the label that appears *immediately before* each known
    template sequence.
    """
    global current_char_label
    global current_narr_label
    current_char_label = find_label_before(full_code, new_char_text_box_label_template)
    current_narr_label = find_label_before(full_code, new_narr_text_box_label_template)


def find_label_before(code, label_template):
    """
    Return the line directly preceding the unique occurrence of `label_template`
    in `code`. If the template occurs zero or multiple times, return "".

    `code` is expected to be a list of lines (strings) as read from the script.
    """
    matches = []
    seq_len = len(label_template)
    # Slide a window of size `seq_len` over the file
    for i in range(len(code) - seq_len + 1):
        if code[i: i + seq_len] == label_template:
            matches.append(i)

    if len(matches) == 1:
        return code[matches[0]-1]
    else:
        return ""


def create_text_box_code(en_box: list[str], speaker: str) -> list[str]:
    """
    Build a standard text-box command sequence for one or more EN containers.

    Behavior:
      • For each `box` string in `en_box`, split into lines and emit:
          - empty spacer line (keeps structure similar to decompiled output)
          - show-text lines (quoted; internal `"` replaced with `'`)
          - end-text-line after each line
          - wait-for-input + end-text-line + empty spacer after the box
      • Between consecutive boxes (when `b != last`), emit the engine-specific
        sequence to advance to the next textbox, including optional `call` to
        the previously discovered labels (if present), and a fresh `u00416120`
        start marker.

    Notes:
      • For the Narrator, we prefer `current_narr_label` if discovered; for
        other speakers, we prefer `current_char_label` when present.
    """
    final_code = []
    global current_char_label
    global current_narr_label

    for b, box in enumerate(en_box):
        lines = box.split("\n")
        final_code.append(f"")
        for l in lines:
            final_code.append(f"show-text 0 \"{l.replace(shitter, safe_shitter)}\"")
            final_code.append(f"end-text-line 0")
        # remove the last end-text-line so the next lines insert the correct trailer
        final_code = final_code[:-1]
        final_code.append(f"wait-for-input 0")
        final_code.append(f"end-text-line 0")
        final_code.append(f"")

        # If there is another EN box to display, emit the bridging sequence.
        if b != len(en_box) - 1:
            final_code.append(f'comment "Extra Textbox {speaker}"')
            final_code.append(f'u004213E0 0')
            final_code.append(f'u0041A7B0 2')
            final_code.append(f'u0041A7B0 1')
            if speaker == "Narrator":
                if current_narr_label:
                    final_code.append(f'call {current_narr_label.rstrip()}')
                final_code.append(f'u004160D0')
            else:
                final_code.append(f'u004160D0')
                if current_char_label:
                    final_code.append(f'call {current_char_label.rstrip()}')
            final_code.append(f'308 1')
            final_code.append(f'u004213E0 1')
            final_code.append(f'u00416120')

    return final_code


def create_text_box_code_concat(en_box: list[str]) -> list[str]:
    """
    Build a text-box sequence that *concatenates* current text with
    (global-string bbb), used when the game expects accumulating text.

    Behavior:
      • For each line:
          - show-text (verbatim)
          - concat (bbb) with the same line (with `"` replaced by `'`)
          - end-text-line
      • Adds/removes trailing lines to match the expected structure between boxes.
    """
    final_code = []
    for b, box in enumerate(en_box):
        lines = box.split("\n")
        final_code.append(f"")
        for l in lines:
            final_code.append(f"show-text 0 \"{l}\"")
            final_code.append(f"concat (global-string bbb) (global-string bbb) \"{l.replace(shitter, safe_shitter)}\"")
            final_code.append(f"end-text-line 0")
        # remove last end-text-line to prepare box trailer
        final_code = final_code[:-1]
        final_code.append(f"")

        # Between boxes, explicitly end the text line before continuing.
        if b != len(en_box) - 1:
            final_code = final_code[:-1]
            final_code.append(f"end-text-line 0")

    return final_code


def create_text_box_code_SG(en_box: list[str]) -> list[str]:
    """
    SG-scene variant: emit only a simple show-text/end-text-line block for
    the *first* EN container and then stop (as per existing behavior).
    """
    final_code = []
    for b, box in enumerate(en_box):
        lines = box.split("\n")
        final_code.append(f"")
        for l in lines:
            final_code.append(f"show-text 0 \"{l.replace(shitter, safe_shitter)}\"")
            final_code.append(f"end-text-line 0")
        final_code.append(f"")
        break  # intentionally use only the first container
    return final_code


if __name__ == "__main__":
    # Simple scanner to locate the label before a given sequence, over files in a folder.
    path = Path(r"..\Eushlator\2_Decompiled")
    look_for = ["u004160D0\n", "mov (global-int 6622) 1\n"]

    # Accept only SC/SN/SP scene files here (SG is intentionally excluded in this demo).
    VALID_SCENE = re.compile(r'(?:\$\d{1,2}\$)?(?:SC|SN|SP)\d{4}\.txt$', re.IGNORECASE)

    for txt_file in sorted(path.glob("*.txt")):
        if not VALID_SCENE.match(txt_file.name):
            continue

        # Read with line endings preserved so `look_for` matches include "\n".
        lines = txt_file.read_text(encoding="utf8").splitlines(keepends=True)

        find_label_before(lines, look_for)
