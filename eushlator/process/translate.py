"""
translate.py – Phase 4 (translation)

• loads $$full_script.yaml                                   (scene structure)
• generates a static system prompt (cache-friendly)
• translates box-by-box (or in batch) with Claude / simulator
• writes / resumes incremental outputs to 4_MachineTranslations/<model>/*.yaml
"""

from __future__ import annotations

import os
import time
import re
from itertools import islice
from pathlib import Path
from typing import Dict, List, Tuple, Optional

import yaml
from tqdm import tqdm

from eushlator.llm.llm import BaseLLM, ChatMessage, LLMResponse
from eushlator.utils.prompt_utils import (
    generate_system_prompt,
    generate_system_prompt_batch,
    generate_scene_context_prompt,
    generate_translation_prompt,
)
from eushlator.utils.yaml_utils import load_yaml, save_yaml

from concurrent.futures import ThreadPoolExecutor, as_completed


def chunked(iterable, size):
    """Yield successive chunks (lists) of length `size` from `iterable` (last chunk may be smaller)."""
    it = iter(iterable)
    while chunk := list(islice(it, size)):
        yield chunk


def translate_parallel(
    scene_names: list[str],
    translate_fn: callable,
    max_workers: int = 5,
):
    """
    Fire-and-forget helper to run `translate_fn(scene_name)` in parallel
    for each name in `scene_names`. Errors are caught and logged so that
    one failing scene doesn't cancel the whole batch.
    """
    futures = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        for scene in scene_names:
            futures.append(executor.submit(translate_fn, scene))

        for future in as_completed(futures):
            try:
                future.result()
            except Exception as e:
                print(f"[Error] Scene translation failed: {e}")


# ──────────────────────────────────────────────────────────────────────
# regex helpers
# Matches scene ids like "SC0001", optionally prefixed by "$<n>$" (handled elsewhere).
SCENE_RE = re.compile(r'(?:\$\d{1,2}\$)?(?P<prefix>SN|SC|SP|SG)(?P<num>\d{4})')


def scene_sort_key(name: str) -> Tuple[int, int]:
    """
    Stable sort key for scene files:
      1) Order by prefix SN < SC < SP < SG (via rank)
      2) Then by numeric id ascending
      3) Appended variants (those that start with "$") come after the base
    """
    m = SCENE_RE.match(name)
    pref_rank = {"SN": 0, "SC": 1, "SP": 2, "SG": 3}[m["prefix"]]
    num = int(m["num"])
    append_flag = name.startswith("$")
    return (pref_rank, append_flag * 10_000 + num)  # append after base


# ──────────────────────────────────────────────────────────────────────
# data loaders

def load_pua(path: Path) -> Dict[str, str]:
    """
    Load PUA (Private Use Area) mapping from a simple KEY=VALUE text file.
    Example line: あ=\\uE000
    """
    mp = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            mp[k] = v
    return mp


def load_flat_dictionary(path: Path) -> Dict[str, str]:
    """
    Flatten a structured dictionary YAML into a single JP→EN map.

    Input shape (example):
      SectionA:
        term1: { jp: "学院", en: "Academy" }
        term2: { jp: "先生", en: "Teacher" }
      SectionB:
        ...

    Output:
      {
        "学院": "Academy",
        "先生": "Teacher",
        ...
      }

    The resulting dict is sorted by JP key length (desc) so longer
    matches can be checked first by callers if desired.
    """
    flat = {}
    tree = load_yaml(path)
    for sect in tree.values():
        for entry in sect.values():
            if entry["jp"] not in flat:
                flat[entry["jp"]] = entry["en"]
    return dict(sorted(flat.items(), key=lambda kv: len(kv[0]), reverse=True))


# ──────────────────────────────────────────────────────────────────────
# glossary extraction
def strip_furigana(txt: str) -> str:
    """Remove any parentheses content (standard or full-width); used before dictionary scanning."""
    return re.sub(r'（.*?）|\(.*?\)', '', txt)


def extract_scene_glossary(
    scene_texts: List[str],
    dictionary: Dict[str, str],
    speakers: List[str],
) -> Dict[str, str]:
    """
    Build a minimal per-scene glossary by scanning the concatenated scene text
    for occurrences of dictionary keys, skipping items that overlap previous
    matches or match speaker names.

    Notes:
      • Expects `dictionary` possibly sorted by key length desc (from load_flat_dictionary)
        which encourages longer, more specific terms to be captured first.
      • `spans` avoids overlapping matches: once a longer term is placed,
        a shorter contained term is ignored.
    """
    joined = strip_furigana("\n".join(scene_texts))
    glossary: Dict[str, str] = {}
    spans: list[tuple[int, int]] = []

    for jp, en in dictionary.items():
        if jp in speakers:
            continue
        start = joined.find(jp)
        if start == -1:
            continue
        end = start + len(jp)
        if any(s <= start < e or s < end <= e for s, e in spans):
            continue      # overlapped by longer match
        glossary[jp] = en
        spans.append((start, end))
    return glossary


# ──────────────────────────────────────────────────────────────────────
# translation helpers
def translate_box(
    llm: BaseLLM,
    history: List[ChatMessage],
    speaker: str,
    jp: str,
    pua_map: Dict[str, str],
    simulate: bool,
    retries: int = 6,
) -> LLMResponse:
    """
    Translate a single JP box with retry/backoff.

    • Builds the per-box user prompt via `generate_translation_prompt`.
    • Appends it to `history` (system + prior pairs).
    • Calls `llm.chat(history, simulate=...)`.
    • On failure, retries up to `retries` with 30s sleep between attempts.
    """
    user_msg = generate_translation_prompt(speaker, jp, pua_map)
    history.append(ChatMessage("user", user_msg))

    attempt = 0
    while True:
        try:
            return llm.chat(history, simulate=simulate)
        except Exception as e:
            attempt += 1
            if attempt >= retries:
                raise
            print(f"[Retry {attempt}/{retries}] {e}")
            time.sleep(30)


def save_progress(path: Path, data: dict):
    """Thin wrapper around `save_yaml` to persist incremental outputs."""
    save_yaml(path, data)


def clean_up(text: str) -> str:
    """
    Normalize LLM output:
      • Convert literal "\\n" to newlines.
      • Strip each line's edges.
      • Collapse 2+ blank lines to one.
      • Trim leading/trailing blank lines.
    """
    text = text.replace("\\n", "\n")
    # strip each line
    text = "\n".join(line.strip() for line in text.split("\n"))
    # collapse 2+ blank lines to exactly one
    text = re.sub(r"\n{2,}", "\n", text)
    # trim leading/trailing blank lines
    return text.strip("\n")


# ──────────────────────────────────────────────────────────────────────
# main
def translate(
    extracted_dialogue_path: Path,
    utils_path: Path,
    machine_translations_path: Path,
    game_name: str,
    llm_model: BaseLLM,
    *,
    batch: bool = False,
    simulate: bool = True,
) -> None:
    """
    Phase 4 driver:
      • Loads scene structure (full JP chunks) from $$full_script.yaml
      • Prepares a per-scene system prompt (cached inside each scene's YAML)
      • Translates either:
          - single mode: sequential message history per scene, or
          - batch mode: one batch request per scene, with lightweight context
      • Writes/updates 4_MachineTranslations/<model>[*_sim]/<scene>.yaml incrementally
    """
    print("[Phase 4] translate.py – start")

    # small utility closures
    def _prepare_scene_state(scene_name, blocks, out_path):
        """(re)load yaml, rebuild history, return (data, done_ids, history).

        data schema:
          {
            "system": <system prompt text>,
            "model": <llm_model.id>,
            "translations": [
              {
                "speaker": str,
                "input": str,    # last user prompt
                "text": str,     # assistant translation
                "id": int,       # full-chunk id
                "time": float,   # latency seconds
                "errors": int,   # retries made (batch) / 0 (single)
                "cost": float,   # USD
                "raw": any       # provider_raw
              },
              ...
            ]
          }
        """
        data = load_yaml(out_path) if out_path.exists() else {
            "system": None, "model": llm_model.id, "translations": []
        }
        done = {t["id"] for t in data["translations"]}

        # Create and cache the system prompt once per scene file.
        if data["system"] is None:
            speakers = list(dict.fromkeys(b["speaker"] for b in blocks))
            gloss = extract_scene_glossary(
                [b["text"] for b in blocks], dictionary, speakers
            )
            gen_sys = generate_system_prompt_batch if batch else generate_system_prompt
            data["system"] = gen_sys(speakers, gloss, utils_path / "names.yaml", game_name)
            save_progress(out_path, data)

        # Rebuild the message history from saved translations (for single mode continuation).
        hist = [ChatMessage("system", data["system"])]
        for t in data["translations"]:
            jp_old = next(b["text"] for b in blocks if b["id"] == t["id"])
            hist.extend([
                ChatMessage("user", generate_translation_prompt(t["speaker"], jp_old, pua_map)),
                ChatMessage("assistant", t["text"])
            ])
        return data, done, hist

    def _make_entry(blk, last_user_msg, resp):
        """Assemble a translation record for single-mode responses."""
        return {
            "speaker": blk["speaker"],
            "input": last_user_msg.content,
            "text": clean_up(resp.content),
            "id": blk["id"],
            "time": resp.latency,
            "errors": 0,
            "cost": round(resp.cost, 6),
            "raw": resp.provider_raw
        }

    def _bare_entry(blk, user_msg):
        """Create a placeholder record before sending a batch request."""
        return {
            "speaker": blk["speaker"],
            "input": user_msg,
            "text": "N/A",
            "id": blk["id"],
            "time": 0.0,
            "errors": 0,
            "cost": 0.0,
            "raw": "N/A",
        }

    def translate_single(scene_name: str):
        """
        Single (chat-style) mode:
          • Maintain rolling history per scene
          • Append each user/assistant pair
          • Save after each chunk for resumability
        """
        print(f"\n=== {scene_name} ===")
        blocks = full_script[scene_name]
        out_path = out_dir / f"{scene_name}.yaml"
        data, done_ids, history = _prepare_scene_state(scene_name, blocks, out_path)

        if not blocks:
            print(f"✓ {scene_name} skipped, no dialogue")
            return

        for blk in tqdm(blocks, desc=f"{scene_name} (single)"):
            if blk["id"] in done_ids:
                continue

            resp = translate_box(
                llm_model, history, blk["speaker"], blk["text"],
                pua_map, simulate
            )
            history.append(ChatMessage("assistant", clean_up(resp.content)))
            data["translations"].append(_make_entry(blk, history[-2], resp))
            save_progress(out_path, data)
        print(f"✓ {scene_name} complete")

    def translate_batch(scene_name: str):
        """
        Batch mode:
          • Build a list of {msg_id, add_context, message} for the entire scene
          • One `llm_model.chat(...)` call returns a dict of msg_id -> response
          • Merge back into the scene YAML and save once
        """
        print(f"\n=== {scene_name} ===")
        blocks = full_script[scene_name]
        out_path = out_dir / f"{scene_name}.yaml"
        data, done_ids, _ = _prepare_scene_state(scene_name, blocks, out_path)

        batch_reqs: list[dict] = []
        temp: dict[int, dict] = {}

        if not blocks:
            print(f"✓ {scene_name} skipped, no dialogue")
            return

        for idx, blk in enumerate(blocks):
            if blk["id"] in done_ids:
                continue

            # Provide minimal context: previous JP box prompt (if present).
            prev_ctx = (
                generate_translation_prompt(blocks[idx - 1]["speaker"],
                                            blocks[idx - 1]["text"], pua_map)
                if idx > 0 else None
            )
            ctx_prompt = generate_scene_context_prompt(prev_ctx, blk["speaker"])
            # In batch, we usually omit explicit speaker in the main prompt body (with_speaker=False).
            user_msg = generate_translation_prompt(blk["speaker"], blk["text"], pua_map, with_speaker=False)

            # msg_id must be unique and stable per block within the scene.
            cid = f"{scene_name.replace('$', 'S')}_{blk['id']}"
            batch_reqs.append({
                "msg_id": cid,
                "add_context": ctx_prompt,
                "message": user_msg,
            })
            temp[blk["id"]] = _bare_entry(blk, user_msg)

        # single batch call with retry loop
        retries = 0
        while True:
            try:
                batch_resp: Dict[str, LLMResponse] = llm_model.chat(
                    batch_reqs, system=data["system"], simulate=simulate
                )
                break
            except Exception as e:
                retries += 1
                if retries >= 6:
                    raise
                print(f"[Batch retry {retries}/6] {scene_name}: {e}")
                time.sleep(30)

        # merge responses into the scene data
        for blk in blocks:
            if blk["id"] in done_ids:
                continue
            cid = f"{scene_name.replace('$', 'S')}_{blk['id']}"
            r = batch_resp[cid]
            e = temp[blk["id"]]
            e.update(text=clean_up(r.content), time=r.latency,
                     cost=round(r.cost, 6), raw=r.provider_raw, errors=retries)
            data["translations"].append(e)

        save_progress(out_path, data)
        print(f"✓ {scene_name} complete")

    # -----------------------------------------------------------------
    # translation loop over scenes
    # Load per-project assets used across all scenes.
    pua_map = load_pua(utils_path / "pua.txt")
    dictionary = load_flat_dictionary(utils_path / "dictionary.yaml")
    full_script = load_yaml(extracted_dialogue_path / "$$full_script.yaml")

    # DEBUG scene restriction:
    #   • Filter out SN0000
    #   • Only translate SG* scenes
    #   • Keep deterministic ordering via scene_sort_key
    # scene_names = [n for n in sorted(full_script, key=scene_sort_key) if n not in ["SN0000"] and "SG" in n]
    scene_names = [n for n in sorted(full_script, key=scene_sort_key)]

    # Output folder is keyed by model id (+ "_sim" suffix when simulate=True).
    out_dir = machine_translations_path / (
            llm_model.id + ("_sim" if simulate else "")
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    # Skip scenes that already have a YAML output in out_dir.
    scene_names = [s for s in scene_names if s + ".yaml" not in os.listdir(out_dir)]

    # Dispatch translation in chunked parallel mode.
    if batch:
        print(f"\n[Parallel Chunked] Translating {len(scene_names)} scenes in batch mode...")
        for chunk in chunked(scene_names, 5):
            print(f"\n→ Starting batch of {len(chunk)} scenes: {chunk}")
            with ThreadPoolExecutor(max_workers=5) as executor:
                executor.map(translate_batch, chunk)
    else:
        print(f"\n[Parallel Chunked] Translating {len(scene_names)} scenes in single mode...")
        for chunk in chunked(scene_names, 1):
            print(f"\n→ Starting batch of {len(chunk)} scenes: {chunk}")
            with ThreadPoolExecutor(max_workers=1) as executor:
                executor.map(translate_single, chunk)


# ── CLI test convenience --------------------------------------------------
if __name__ == "__main__":
    # Minimal local invocation for quick testing using config.yaml.
    with open("config.yaml", 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)
    game_cfg = config.get(22, {})
    install_path = Path(game_cfg.get("install_path"))
    root = install_path / "Eushlator"
    translate(
        extracted_dialogue_path=root / "3_ExtractedDialogue",
        utils_path=root / "Utils",
        machine_translations_path=root / "4_MachineTranslations",
        game_name=game_cfg.get("name"),
        simulate=True
    )
