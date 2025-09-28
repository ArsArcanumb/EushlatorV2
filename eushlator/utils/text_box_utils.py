import re
from typing import List

from eushlator.utils.manual_replacements import replace_str

# Detect any Japanese characters:
# - Hiragana:     U+3040–U+309F
# - Katakana:     U+30A0–U+30FF
# - Kanji (CJK):  U+4E00–U+9FFF
# - Half-width Katakana: U+FF66–U+FF9F
_JP_CHAR_PATTERN = re.compile(r"[\u3040-\u30FF\u4E00-\u9FFF\uFF66-\uFF9F]")


def contains_japanese(text: str) -> bool:
    """
    Returns True if the text contains any Japanese character (Hiragana, Katakana, Kanji).

    Notes:
      - Useful as a sanity check against untranslated output from the LLM.
      - Also flags half-width katakana (U+FF66–U+FF9F).
    """
    return bool(_JP_CHAR_PATTERN.search(text))


def is_wide_char(ch: str) -> bool:
    """
    Heuristic for monospace width:
      - Treat CJK, Kana, most full-width ranges, and PUA code points as double-width.
      - Count common wide dashes/waves as double-width as well.

    This is used by `measure_text_width` to approximate on-screen text fitting.
    """
    code = ord(ch)
    return (
        # PUA ranges (private-use areas)
        0xE000 <= code <= 0xF8FF or
        0xF0000 <= code <= 0xFFFFD or
        0x100000 <= code <= 0x10FFFD or

        # CJK punctuation & Kana blocks
        0x3000 <= code <= 0x303F or  # CJK Symbols and Punctuation
        0x3040 <= code <= 0x309F or  # Hiragana
        0x30A0 <= code <= 0x30FF or  # Katakana
        0x4E00 <= code <= 0x9FFF or  # CJK Unified Ideographs
        0xF900 <= code <= 0xFAFF or  # CJK Compatibility Ideographs

        # Full-width forms (roman, punctuation, currency, etc.)
        0xFF01 <= code <= 0xFF60 or
        0xFFE0 <= code <= 0xFFE6 or

        # Commonly-rendered-as-wide punctuation
        code in (0x2014, 0x2015, 0x301C)
    )


def measure_text_width(s: str) -> int:
    """Count wide chars (CJK, PUA, etc.) as 2, others as 1."""
    return sum(2 if is_wide_char(ch) else 1 for ch in s)


def correct_en_box(
    en_box: str,
    max_lines: int,
    max_chars_per_line: int
) -> List[str]:
    """
    Break `en_box` into one or more sub-boxes, each up to max_lines×max_chars wide,
    splitting only at spaces and never in the middle of a word.

    Returns:
      A list of strings; each string is up to `max_lines` lines (joined by '\\n').

    Behavior:
      - Greedy word-wrapping within a box up to `max_lines`.
      - If the current box is full, flush it and start a new one.
      - Width calculation uses `measure_text_width` (double-width for CJK/PUA).
    """
    words = en_box.split(" ")
    boxes: List[str] = []
    lines: List[str] = []

    def _flush():
        # Push accumulated lines as a single multi-line box (joined by \n)
        nonlocal lines
        if lines:
            boxes.append("\n".join(lines))
        lines = []

    for word in words:
        if not lines:
            # start first line of a new box
            lines = [word]
            continue

        # try appending to current last line
        candidate = f"{lines[-1]} {word}"
        if measure_text_width(candidate) <= max_chars_per_line:
            lines[-1] = candidate
        else:
            # need a new line in this box?
            if len(lines) < max_lines:
                lines.append(word)
            else:
                # box is full → flush and start a new one
                _flush()
                lines = [word]

    # flush any remaining lines
    _flush()
    return boxes


def reconcile(jp_boxes, en_containers):
    """
    Ensure the number of English containers matches the number of JP boxes.

    Cases:
      - If there are fewer EN containers than JP boxes:
          (1) Try to split the last EN container by carving off sub-boxes.
          (2) Otherwise, flatten all sub-boxes and re-chunk evenly to jp_len.
              (Empty placeholders are inserted if needed.)
      - If there are more EN containers than JP boxes:
          Merge the extras into the last JP-aligned container (by concatenating
          their sub-box lists).

    Returns:
      A (possibly new) list of EN containers with length == len(jp_boxes).

    Note:
      Each "container" is a list[str] produced by `correct_en_box` for one JP line.
    """
    jp_len = len(jp_boxes)
    cont_len = len(en_containers)

    if jp_len > cont_len:
        need = jp_len - cont_len
        last = en_containers[-1]

        # 1) If the last container has enough sub‐boxes to split off…
        if len(last) > need:
            # carve off the first `need` sub‐boxes and make them stand-alone containers
            new = [[last.pop(-1)] for _ in range(need)]
            en_containers.extend(reversed(new))

        # 2) else, flatten & re-chunk to exactly jp_len containers
        else:
            flat = [b for container in en_containers for b in container]
            total = len(flat)

            # even distribution: first `rem` containers get (div+1), rest get div
            div, rem = divmod(total, jp_len)

            new_containers = []
            idx = 0
            for i in range(jp_len):
                size = div + 1 if i < rem else div
                new_containers.append(flat[idx: idx + size])
                idx += size

            # Guarantee at least one sub-box per container (empty string if needed)
            for nc in new_containers:
                if not nc:
                    nc.append("")
            en_containers = new_containers

    # If we have more containers than JP lines…
    elif jp_len < cont_len:
        # merge the extra containers into the last “real” one
        extras = en_containers[jp_len:]
        en_containers = en_containers[:jp_len]
        # flatten extras and append
        for container in extras:
            en_containers[-1].extend(container)

    return en_containers


def refine_llm_output(llm_text: str, scene_id: str, text_id: int):
    """
    Post-process a raw LLM translation string.

    Steps:
      - Warn and collapse double blank lines.
      - Warn if any Japanese characters remain (likely untranslated).
      - Strip enclosing quotes/brackets on each line (", [], 「」) if balanced.
      - Apply `replace_str` (manual replacements) to the final text.
    """
    result_text = llm_text

    if "\n\n" in llm_text:
        print(f"⚠️ Empty lines in text in {scene_id}:{text_id}")
        result_text = result_text.replace("\n\n", "\n")

    if contains_japanese(llm_text):
        print(f"⚠️ Japanese text in {scene_id}:{text_id}")

    # Remove one layer of enclosing quotes per line, if present.
    with_quotation = result_text.split("\n")
    without_quotation = ""
    quot = [('"', '"'), ('[', ']'), ('「', '」')]
    for l in with_quotation:
        clean_line = l.strip()
        for lq, rq in quot:
            if clean_line.startswith(lq) and clean_line.endswith(rq):
                without_quotation += clean_line[1:-1]
                break
        else:
            without_quotation += clean_line
        without_quotation += "\n"
    result_text = without_quotation.strip()

    # Apply project-specific manual replacements (symbols, punctuation, etc.)
    result_text = replace_str(result_text)

    return result_text


def create_english_box(
    jp_text: str,
    en_text: str,
    reverse_pua_map: dict[str, str],
    text_lines: int,
    line_width: int,
    scene_id: str,
    text_id: int,
) -> list[str]:
    """
    Format English text into a wrapped block that fits in a JP textbox.

    Args:
        jp_text: Original JP full box text (unused here but useful for alignment extensions)
        en_text: English translation (already validated)
        reverse_pua_map: Mapping from replacements like "..." or "--" back to PUA symbols
        text_lines: Max number of lines allowed
        line_width: Max characters per line

    Returns:
        NOTE: Despite the return annotation, this function returns List[List[str]]:
          - Outer list size == number of JP boxes (len(jp_text.splitlines()))
          - Each inner list[str] are wrapped lines (<= text_lines) for that box

    Pipeline:
      1) Compare JP vs EN box counts; warn on mismatch.
      2) Wrap each EN box using `correct_en_box`.
      3) Reconcile container counts with `reconcile` to match the JP count.
    """

    # Restore reverse PUA symbols if needed (currently disabled by design).
    # for pua in sorted(reverse_pua_map.keys(), key=len, reverse=True):
    #     en_text = en_text.replace(pua, reverse_pua_map[pua])

    # get the original japanese boxes
    jp_boxes = jp_text.split("\n")
    en_boxes = en_text.split("\n")
    if len(jp_boxes) != len(en_boxes):
        print(f"⚠️ Mismatching textboxes in {scene_id}:{text_id}")

    # now we have to check if each en_box actually fits into the textbox constraints
    en_containers = [correct_en_box(en_box, text_lines, line_width) for en_box in en_boxes]

    # en_boxes is now List[List[str]]: one “container” per original JP line
    en_containers = reconcile(jp_boxes, en_containers)

    # Now en_containers has exactly len == jp_len
    # And each item is a List[str] of the wrapped‐lines for that JP box
    return en_containers


if __name__ == "__main__":
    import pprint

    # 1) Test correct_en_box()
    print("▶ Testing correct_en_box()…")
    inp = "Hello my baby, hello my honey."
    out = correct_en_box(inp, max_lines=2, max_chars_per_line=10)
    expected = ["Hello my\nbaby,", "hello my\nhoney."]
    assert out == expected, f"got {out!r}, expected {expected!r}"
    print("  ✓ simple wrap test passed")

    # PUA-aware: count PUA as 2 chars
    pua_test = "\ue000 \ue000 A B C D E"
    # line_width=6: \ue000\ue000 counts as 4, + 'AB' =6
    out2 = correct_en_box(pua_test, max_lines=2, max_chars_per_line=6)
    expected2 = ["\ue000 \ue000\nA B C", "D E"]  # CDE spills to new line, but only one line allowed→ flush, produces two boxes
    assert out2 == expected2
    print("  ✓ PUA-measure test passed")

    # 2) Test reconciliation logic
    print("▶ Testing reconciliation…")

    # a) exact match → no change
    jp = ["A","B","C"]
    en = [["a1"],["b1"],["c1"]]
    assert reconcile(jp, en) == en

    # b) fewer containers, last has extra
    jp = ["A","B","C"]
    en = [["x1","x2","x3","x4"]]
    res = reconcile(jp, en)
    # according to the logic: last=["x1","x2","x3","x4"], need=2, so pop x1,x2 → last becomes ["x3","x4"], then new=[[x1],[x2]]
    assert res == [["x1","x2"],["x3"],["x4"]]

    # c) fewer containers, all too small → extend with empty
    jp = ["A","B","C"]
    en = [["z1"]]
    res = reconcile(jp, en)
    assert res == [["z1"],[""],[""]]

    # d) fewer containers, last too small → flatten branch
    jp = ["A", "B", "C", "D"]
    en = [["z1", "z2"], ["z3", "z4", "z5", "z6", "z7", "z8"], ["z9"]]
    res = reconcile(jp, en)
    assert res == [['z1', 'z2', 'z3'], ['z4', 'z5'], ['z6', 'z7'], ['z8', 'z9']]

    # e) more containers → merge extras
    jp = ["A","B"]
    en = [["a1"],["b1"],["c1"],["d1","d2"]]
    res = reconcile(jp, en)
    # extras = [["c1"],["d1","d2"]], base cont = [["a1"],["b1"]], so last gets c1,d1,d2
    assert res == [["a1"],["b1","c1","d1","d2"]]

    print("  ✓ reconciliation tests passed")

    print("\n🎉 All text_box_utils tests passed successfully!")
