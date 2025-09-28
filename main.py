import sys
import yaml
from typing import Optional, Literal
from pathlib import Path

from eushlator.llm.claude import ClaudeLLM
from eushlator.llm.claude_batch import ClaudeBatchLLM
from eushlator.llm.openai_batch import OpenAIBatchLLM
from eushlator.process.correct import run_corrections
from eushlator.process.extract import run_extraction
from eushlator.process.decompile import run_decompilation, run_decompilation_translations
from eushlator.process.extract_dialogue import run_extract_folder
from eushlator.process.extract_dialogue_refine import run_refine_dialogue
from eushlator.process.recompile import run_recompilation
from eushlator.process.reinsert import reinsert_translations
from eushlator.process.translate import translate
from eushlator.utils.manual_replacements import load_replacement_file
from eushlator.utils.paths import ensure_folders_exist
from eushlator.utils.prepare_dictionary import run_prepare_dictionary
from eushlator.utils.prepare_names import run_prepare_names
from eushlator.utils.pua import collect_pua_symbols

CONFIG_PATH = Path("config.yaml")


def load_config(path: Path = CONFIG_PATH) -> dict:
    """
    Load YAML config. Exits with code 1 if the file is missing or invalid.
    """
    if not path.exists():
        print(f"Config file not found at: {path.resolve()}")
        sys.exit(1)
    try:
        with path.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception as exc:
        print(f"Failed to read config: {exc}")
        sys.exit(1)


Provider = Literal["anthropic", "anthropic-batch", "openai-batch"]


def build_llm(
    provider: Provider,
    model_name: Optional[str],
    api_keys: dict,
):
    """
    Create the desired LLM instance and return (llm, batch_flag, resolved_model_name).

    provider:
      - "anthropic"         -> ClaudeLLM
      - "anthropic-batch"   -> ClaudeBatchLLM
      - "openai-batch"      -> OpenAIBatchLLM

    The relevant API key is pulled from api_keys and passed as api_key=...
    """
    api_key = None
    if provider.startswith("anthropic"):
        api_key = (api_keys or {}).get("anthropic")
        default_model = "claude-sonnet-4-20250514"
        model = model_name or default_model
        if provider == "anthropic":
            if not api_key:
                print("Missing Anthropic API key in config under api_keys.anthropic")
                sys.exit(1)
            return ClaudeLLM(model=model, api_key=api_key), False, model
        elif provider == "anthropic-batch":
            if not api_key:
                print("Missing Anthropic API key in config under api_keys.anthropic")
                sys.exit(1)
            return ClaudeBatchLLM(model=model, api_key=api_key), True, model

    if provider == "openai-batch":
        api_key = (api_keys or {}).get("openai")
        default_model = "gpt-4.1-mini"
        model = model_name or default_model
        if not api_key:
            print("Missing OpenAI API key in config under api_keys.openai")
            sys.exit(1)
        return OpenAIBatchLLM(model=model, api_key=api_key), True, model

    print(f"Unknown provider: {provider!r}. Expected one of "
          f"'anthropic', 'anthropic-batch', 'openai-batch'.")
    sys.exit(1)


def main(
    game_id: int,
    run_type: str = "full",
    run_tag: str = "",
    provider: Provider = "anthropic",    # choose: "anthropic" | "anthropic-batch" | "openai-batch"
    model_name: Optional[str] = None,    # override model if desired
    simulate: bool = False,              # forwarded into translate()
):
    """
    End-to-end pipeline runner for EushlatorV2.

    - Reads game config and API keys from config.yaml
    - Builds folder structure under <install_path>/Eushlator
    - Runs extract → decompile → dictionary/name prep → dialogue extract/refine
    - Translates using chosen LLM provider/model (with api_key from config)
    - Applies corrections, reinserts, and recompiles

    Parameters
    ----------
    game_id : int
        ID used to pull the game's configuration block from config.yaml.
    run_type : str
        Pipeline mode:
          - "prep"      : stop after decompile (preparation only)
          - "extract"   : stop after dialogue extract/refine
          - "translate" : stop after corrections (before reinsertion/recompile)
          - "full"      : run all phases
    run_tag : str
        Identifier used in output folder names (e.g., inserted/<run_tag>).
    provider : Provider
        Which LLM implementation to use. See Provider type for options.
    model_name : Optional[str]
        Force a specific model (otherwise sensible defaults are used).
    simulate : bool
        Passed to `translate()`; useful for dry runs if that function supports it.
    """
    config = load_config()
    api_keys = config.get("api_keys", {})

    game_cfg = config.get(game_id, {})
    if not game_cfg:
        print(f"No configuration found for game ID {game_id}")
        sys.exit(1)

    game_name = game_cfg.get("name", "")
    install_path = Path(game_cfg["install_path"])

    print(f"Running EushlatorV2 on Game ID {game_id} ({game_name})")

    # Base working directory
    eushlator_dir = install_path / "Eushlator"
    ensure_folders_exist(eushlator_dir)

    # Standardized folder layout for each phase
    extracted_path = eushlator_dir / "1_Extracted"
    decompiled_path = eushlator_dir / "2_Decompiled"
    extracted_dialogue_path = eushlator_dir / "3_ExtractedDialogue"
    machine_translations_path = eushlator_dir / "4_MachineTranslations"
    translations_path = eushlator_dir / "4ex_Translations"
    inserted_path = eushlator_dir / "5_Inserted"
    recompiled_path = eushlator_dir / "6_Recompiled"
    utils_path = eushlator_dir / "Utils"

    # --- 1) Raw extraction from install directory into 1_Extracted
    run_extraction(install_path, extracted_path)

    # --- 2) Decompile game resources/scripts into 2_Decompiled
    run_decompilation(extracted_path, decompiled_path, install_path)

    # --- Break here if this is the first run
    if run_type == "prep":
        exit(0)

    # If you already have edited translations, prepare them for later stages
    run_decompilation_translations(translations_path)

    # --- 3) Utilities: collect PUA symbols, replacement lists, names, dictionary
    collect_pua_symbols(decompiled_path, utils_path)
    load_replacement_file(utils_path)
    run_prepare_names(decompiled_path, translations_path, utils_path)
    run_prepare_dictionary(decompiled_path, translations_path, utils_path)

    # --- 4) Dialogue extraction + refinement into 3_ExtractedDialogue
    run_extract_folder(decompiled_path, utils_path, extracted_dialogue_path)
    run_refine_dialogue(extracted_dialogue_path, extracted_dialogue_path)

    if run_type == "extract":
        exit(0)

    # --- 5) Build the LLM (provider/model configurable; api_key taken from config)
    llm_model, batch, resolved_model = build_llm(provider=provider, model_name=model_name, api_keys=api_keys)

    # Use the provided run_tag directly if given
    llm_id: str = run_tag or llm_model.id

    # --- 6) Machine translation step into 4_MachineTranslations
    if not run_tag:
        translate(
            extracted_dialogue_path=extracted_dialogue_path,
            utils_path=utils_path,
            machine_translations_path=machine_translations_path,
            game_name=game_name,
            llm_model=llm_model,
            batch=batch,
            simulate=simulate,
        )

    # --- 7) Post-translation corrections into 5_Inserted/<run_tag>
    run_corrections(
        translations_path=translations_path,
        out_path=inserted_path / llm_id
    )

    if run_type == "translate":
        exit(0)

    # --- 8) Reinsert translations back into decompiled assets
    reinsert_translations(
        decompiled_path=decompiled_path,
        extracted_dialogue_path=extracted_dialogue_path,
        machine_translations_path=machine_translations_path,
        edited_translations_path=translations_path,
        inserted_path=inserted_path,
        utils_path=utils_path,
        model_name=llm_id,
        config=game_cfg,
        batch=batch
    )

    # --- 9) Recompile final game assets into 6_Recompiled
    run_recompilation(inserted_path, recompiled_path, llm_id)


if __name__ == "__main__":
    main(
        game_id=22,
        run_type="full",
        run_tag="v1.00",                              # <- your configurable version/tag
        provider="anthropic-batch",                    # "anthropic" | "anthropic-batch" | "openai-batch"
        model_name="claude-sonnet-4-20250514",   # or None to use defaults per provider
        simulate=False,
    )
