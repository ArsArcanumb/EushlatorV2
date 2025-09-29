# Eushlator

Eushlator is a small toolchain that extracts, decompiles, parses, translates, reinserts, and recompiles dialogue for Eushully/SYS5-style visual novels.

It automates a multi-phase pipeline:

1. **Extract** packed assets from the game install directory.
2. **Decompile** `.BIN` scripts to readable `.txt`.
3. **Detect & extract dialogue** into per-scene YAML.
4. **Refine** consecutive lines into speaker chunks.
5. **Translate** with an LLM (Anthropic/OpenAI), incrementally and resumable.
6. **Reinsert** translated text as proper SYS5 textbox commands.
7. **Recompile** `.txt` back to `.BIN`.

Features include speaker resolution via `Utils/names.yaml`, glossary injection from `Utils/dictionary.yaml`, PUA symbol handling, manual replacement rules, batch or single-shot translation modes, and idempotent phase directories.

> **Note:** This project doesn’t ship any game assets. The decompiler/assembler binary is expected at:
> `eushlator/repositories/Eushully-Decompiler/x64/Release/Decompiler.exe` (Windows).

---

# Install

## Prereqs

* Python **3.10+** (3.11 recommended)
* Windows (for the provided decompiler `.exe`)
* `git` (optional, if cloning)
* The actual game (Installed, Full official patch + Appends, without any unofficial translation patches in the install folder!)

## Python Environment

- Open the project in your favourite IDE

```bash
# create venv
python -m venv .venv

.venv\Scripts\activate

pip install -r requirements.txt
```

## Decompiler (setup & build)

1. **Fetch the sources**

We are using Kelebek1's decompiler for the important De- and Recompile steps.

```bash
cd eushlator/repositories
git clone https://github.com/Kelebek1/Eushully-Decompiler temp
```

2. **Place the project where Eushlator expects it**

* Copy the folder `eushlator/repositories/temp/Decompiler` → `eushlator/repositories/Eushully-Decompiler`
* Copy the file `eushlator/repositories/temp/Decompiler.sln` → `eushlator/repositories/Eushully-Decompiler`

3. **Build the C++ Decompiler (Visual Studio)**

* Open `eushlator/repositories/Eushully-Decompiler/Decompiler.sln` in **Visual Studio**
* Switch the configuration from **Debug** → **Release**
* Build the solution (**Ctrl+B**)
* After a successful build, you should have:

  ```
  eushlator/repositories/Eushully-Decompiler/x64/Release/Decompiler.exe
  ```

4. **Clean up**

* Remove the temp folder

> Notes:
>
> * You need the **Desktop development with C++** workload installed in Visual Studio.
> * If your build creates a different output path, adjust `DECOMPILER_PATH` accordingly:
>   `eushlator/repositories/Eushully-Decompiler/x64/Release/Decompiler.exe`.


## Configure the project (`config.yaml`)

Create (or edit) a `config.yaml` at the repo root. Here’s a commented example:

```yaml
# Each top-level number is a GAME ID you pass to the runner (e.g., 22)
22:
  name: "百千の定にかわたれし剋"         # Display name (any string)
  install_path: "D:/Games/Hyakusen"  # Absolute path to the game install folder
  text_lines: 2                      # Max lines per text box when reinserting
  line_length: 58                    # Max characters per line (wrapping target)

# API keys used by the translator phase (choose at least one provider)
api_keys:
  anthropic: "ANTHROPIC_API_KEY_HERE"
  openai:    "OPENAI_API_KEY_HERE"
```

### Notes

* **`install_path`**: Use an absolute path. On Windows you can use either `D:/…` or `"D:\\…"` (if you prefer backslashes).
* **Multiple games**: You can add more entries (`23:`, `24:` …). The runner selects by numeric ID. (Currently not fully implemented)
* **`text_lines` / `line_length`**: Control how English is split into SYS5 text boxes. If lines get cut oddly, raise `line_length` or `text_lines`.
* **API keys**:

  * Put your **Anthropic** and/or **OpenAI** key here.
  * These are read at runtime and passed to the selected LLM client.
* Don't share your API keys


# Usage

## Step 1 — Prep-only run

In your IDE, run `main.py` with `run_type="prep"`. This prepares assets (extracts, decompiles) and stops **before** translation.

```python
if __name__ == "__main__":
    main(
        game_id=22,
        run_type="prep",                    # ← prepare only (no translation yet)
        run_tag="",
        provider="anthropic",
        model_name="claude-sonnet-4-20250514",
        simulate=False,
    )
```

#### (Optional) Add existing patches before the full run

After the **prep** run, you can drop any **existing script patches** (ZAP Translations) into `4ex_Translations/` so they’re picked up in the full pipeline:

* Put them in:

  ```
  <install_path>/Eushlator/4ex_Translations/
  ```
* If a `SCxxxx.txt` exists here, its differing **`set-string` lines** are overlaid during reinsertion.
* If you only have a `SCxxxx.BIN`, it will be auto-decompiled to `SCxxxx.txt` at the start of the full run.

## Step 2 — Create `dictionary_offsets.yaml` (manual)

After **`run_type="prep"`**, you must define where the **short definition tables** live inside the INIT scripts. Create:

```
<install_path>/Eushlator/Utils/dictionary_offsets.yaml
```

Use hex offsets to mark the **inclusive start/end** of each contiguous string table you want treated as a dictionary (names, classes, etc.). Example (also in the repo at `examples/22/dictionary_offsets.yaml`):

```yaml
# hex addresses → *inclusive* start / end of the contiguous string table
AFINIT:
  start: 0x4B295          # races
$1$VIINIT:
  start: 0x03860
  end:   0x03862
...
```

### Why this is manual (and necessary)

INIT files often mix **short definitions** (what we want in the glossary) with **long descriptive text** (lore, paragraphs). Automatic detection is unreliable. For example, in `$1$VIINIT`:

```
mov (global-int 1a02bb) 5dc
set-string (global-string 3861) "ロセアン山脈"
...
set-string (global-string 3e0b) "セテトリ地方に東西へ長く伸びる火山帯。"
...
```

Here, we want only the **definitions** (e.g., place/race/class names), **not** the multi-sentence descriptions. Offsets let you bracket just the table of short entries and exclude long prose.

### How to pick the offsets (quick guide)

1. Open the corresponding decompiled file in `2_Decompiled/` (e.g., `VIINIT.txt`, `CLINIT.txt`, …).
2. Locate the **contiguous block** of `set-string` lines that represent **short names/terms** you want in the dictionary.
3. Set:

   * `start`: hex address (after *set-string (global-string*) of the **first** entry in that block.
   * `end` (optional): hex address of the **last** entry if text continues beyond the block.
4. For appended/variant tables within the same INIT, prefix with `$1$`, `$2$`, etc. (as in the example).


## Step 3 — Extract dialogue

With the offset dict in place, run the **extraction** phase to build per-scene YAML and the consolidated script map.

```python
if __name__ == "__main__":
    main(
        game_id=22,
        run_type="extract",                 # ← stop after extract + refine
        run_tag="",
        provider="anthropic",               # ignored in this phase
        model_name=None,                    # ignored in this phase
        simulate=False,
    )
```

### What this does

* Parses decompiled scripts in `2_Decompiled/`
* Uses `Utils/dictionary_offsets.yaml`
* Writes:

  * `3_ExtractedDialogue/SCxxxx.yaml` … per-scene, numbered text boxes (JP + speaker + offsets)
  * `3_ExtractedDialogue/$$full_script.yaml` … scene → merged speaker chunks (used by translation)
  * `Utils/names.yaml`, and `Utils/dictionary.yaml` for further translation help

### Verify quickly

* Open a few files in `3_ExtractedDialogue/` and confirm:

  * `speaker` names look right
  * `text` contains only the intended dialogue
  * `offset` values are present (nonnegative integers)

> Re-running is safe: existing outputs are skipped. Delete specific `SCxxxx.yaml` if you want to regenerate it.


### Optional: Validate & tune your Utils

* **Review `Utils/dictionary.yaml`**
  Make sure term → translation pairs look right (no long prose, only definitions). If English definitions are missing, add them manually if you want.

* **Review `Utils/names.yaml`**
  Check JP → EN names and `en_summary` entries for key characters. Again, feel free to complete missing data.

* **Add `Utils/manual_replacements_dict.yaml`** *(post-processing, names/titles only)*
  Use this for **proper nouns** you want force-replaced after translation (not common words).
  Example in the repo at `examples/22/manual_replacements_dict.yaml`

* **Normalize PUA symbols (`Utils/pua.txt`)**
  Use `Utils/scene_tests/` and `Utils/SC0000.txt` to preview how PUA glyphs render in-game.
  Enter observed PUA sequences in the first text field, decide readable ASCII/Unicode replacements, and record them in `pua.txt` as `PUA_SEQUENCE=REPLACEMENT`.

  Example mappings:

  ```
  =...
  =...
  =...――
  =......
  =......
  =.........
  =―
  =――
  =――...
  ```

  Tips:

  * Prefer standard punctuation (e.g., `―`, `—`, `…`, `...`) and ASCII where it reads well.
  * Keep sequences **lossless and consistent**—the same PUA run should always map to the same replacement.
  * You can later refine with `reverse_pua.txt` for reinsertion fidelity.

## Step 4 — Translation

Set `run_tag` **only** if you want to **reuse an existing** translations folder in `4_MachineTranslations/`.

### A) Do a fresh translation (no `run_tag`)

Runs the LLM and writes new scene YAMLs under `4_MachineTranslations/<model_id>[ _sim ]/`.

```python
if __name__ == "__main__":
    main(
        game_id=22,
        run_type="full",          # or keep going after this phase
        run_tag="",               # ← empty = perform translation now
        provider="anthropic",     # or "anthropic-batch" / "openai-batch"
        model_name="claude-sonnet-4-20250514",
        simulate=False,           # True = simulator (if supported)
    )
```

**What happens**

* System + per-scene prompts are generated.
* Translations are saved incrementally to:

  ```
  4_MachineTranslations/<model_id>[ _sim ]/SCxxxx.yaml
  ```
* The pipeline proceeds to corrections/reinsert/recompile using that folder.

> Resume behavior: if a scene file already exists in the target model folder, it’s skipped. Delete specific `SCxxxx.yaml` to re-translate it.

---

### B) Reuse an existing translation (set `run_tag`)

**Skip translation** and continue using the pre-existing folder:

```
4_MachineTranslations/<run_tag>/
```

```python
if __name__ == "__main__":
    main(
        game_id=22,
        run_type="full",
        run_tag="v1.00",          # ← use existing translations under 4_MachineTranslations/v1.00/
        provider="anthropic",     # ignored for reuse
        model_name=None,          # ignored for reuse
        simulate=False,
    )
```

**Requirements**

* The folder must exist and contain per-scene files like:

  ```
  4_MachineTranslations/v1.00/SC0001.yaml
  4_MachineTranslations/v1.00/SP0123.yaml
  ...
  ```
* The pipeline will treat `run_tag` as the **model identifier** for downstream steps:

  * Corrections → `5_Inserted/<run_tag>/`
  * Reinsert & Recompile output → `6_Recompiled/<run_tag>/`

---

## Step 5 — Finish the pipeline with `run_type="full"`

Run the **full** pipeline to apply corrections, reinsert the English text, and recompile `.BIN` files.

```python
if __name__ == "__main__":
    main(
        game_id=22,
        run_type="full",                 # ← run all remaining phases
        run_tag="",                      # empty = perform translation now
        # run_tag="v1.00",               # or reuse translations in 4_MachineTranslations/v1.00/
        provider="anthropic",            # only used when run_tag is empty
        model_name="claude-sonnet-4-20250514",
        simulate=False,
    )
```

### What happens in “full”

1. **(If `run_tag` is empty)** Translate scenes →
   `4_MachineTranslations/<model_id>[_sim]/SCxxxx.yaml`

2. **Corrections** (post-processing & manual overrides) →
   `5_Inserted/<RUN_ID>/SCxxxx.txt`

   * Uses `Utils/manual_replacements_dict.yaml` (proper nouns only)
   * Applies `Utils/pua.txt` / `reverse_pua.txt` mappings during flows

3. **Reinsert** English textboxes back into decompiled scripts →
   Updates `5_Inserted/<RUN_ID>/SCxxxx.txt` with proper SYS5 commands

4. **Recompile** back to game binaries →
   `6_Recompiled/<RUN_ID>/SCxxxx.BIN`

> **RUN_ID** is:
>
> * the **LLM model id** (or `<id>_sim`) when translating now (`run_tag=""`)
> * the **exact `run_tag`** string when reusing an existing translations folder

### Where to look afterward

* `5_Inserted/<RUN_ID>/` — human-readable, reinjected `.txt` (good for diffing)
* `6_Recompiled/<RUN_ID>/` — final `.BIN` for the game

### Quick checks

* Open a few files in `5_Inserted/<RUN_ID>/` and confirm:

  * Textboxes are properly wrapped to your `text_lines` / `line_length`
  * Speaker tags / concatenations look correct
* If you provided patches in `4ex_Translations/SCxxxx.txt`, their differing
  **`set-string`** lines should be reflected in the inserted `.txt`.

### Common tweaks

* Lines too tight/long? Adjust in `config.yaml`:

  ```yaml
  22:
    text_lines: 2
    line_length: 58
  ```
* Need extra name fixes? Add to `Utils/manual_replacements_dict.yaml`
  (proper nouns / titles only—avoid common words).


# FAQ

### Does this also work with other Eushully games?

Probably not out of the box without heavy modifications and knowledge of the game engine and python.

While I was planning on making this project modular, hence the different ID's in config.yaml, I wanted to focus on Hyakusen first and it became a bit too specific to also take the other games into consideration. 

It might work up to the dialogue extraction step, or even the translation step but I'm not entirely certain. Reinsert will most certainly fail. Per game, pretty much all magic strings, such as *NAME_ADDR_MIN* and *NAME_ADDR_MAX* in character_summary.py as well as the entire code_utils.py would need to be rewritten/refactored. If you honestly plan on doing something like that and need help, let me know.


### I just want to recomplile my own files into game files. How?

If you just want to edit an already existing translation from the yaml files, you can do it like this:

- After running the prep and extract steps, make sure that you have all Utils files (and existing patch files in 4ex_Translations) set up correctly.
- Create a folder in the Eushlator/4_MachineTranslations folder of your game (Let's call it "my_translation" in this example).
- Put the yaml files into your "my_translation" folder.
- Edit them as you like, but the only field that matters is the "text" field in each entry. Make sure the English "text" and Japanese "input" values have the exact same amount of line breaks. It also works if not, but might cause formatting problems. Be careful with text indentation and characters like " and '. YAML is very strict with its formatting.
- When you're done, run the program with "full" as run_type and "my_translation" as run_tag. It should now compile your own translation.
