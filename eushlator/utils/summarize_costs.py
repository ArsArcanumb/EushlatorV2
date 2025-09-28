from pathlib import Path
import yaml
from collections import defaultdict


def summarize_costs(machine_translations_root: Path):
    """
    Walk the machine translations output folder and summarize per-scene costs/time,
    then roll them up by model and overall.

    Expected layout:
      <machine_translations_root>/
        <model_name_1>/
          SC0001.yaml
          SC0002.yaml
          ...
        <model_name_2>/
          ...
      Each scene .yaml contains:
        {
          "system": "...",
          "model": "...",
          "translations": [
            {"cost": <float>, "time": <float>, ...},
            ...
          ]
        }
    """
    # Header with the absolute path we are scanning.
    print(f"\n📊 Summarizing costs from: {machine_translations_root.resolve()}\n")

    # Running grand totals across all models.
    overall_cost = 0.0
    overall_time = 0.0
    # Per-model accumulators: { model_name: {"cost": float, "time": float} }
    per_model_totals = defaultdict(lambda: {"cost": 0.0, "time": 0.0})

    # Iterate each model subfolder under the root.
    for subdir in sorted(machine_translations_root.iterdir()):
        if not subdir.is_dir():
            continue  # ignore stray files

        model_name = subdir.name
        model_cost = 0.0
        model_time = 0.0

        print(f"Model: {model_name}")

        # Sum costs/time per scene YAML inside this model folder.
        for yaml_file in sorted(subdir.glob("*.yaml")):
            with yaml_file.open("r", encoding="utf-8") as f:
                data = yaml.safe_load(f)

            file_cost = 0.0
            file_time = 0.0
            # Defensive defaults (0) if keys are missing.
            for entry in data.get("translations", []):
                file_cost += float(entry.get("cost", 0))
                file_time += float(entry.get("time", 0))

            # Print per-file subtotal, then add to model totals.
            print(f"  {yaml_file.name:25}  ${file_cost:7.4f}   ⏱ {file_time:6.2f}s")
            model_cost += file_cost
            model_time += file_time

        # Per-model summary.
        print(f"  {'-'*25}  --------   ----------")
        print(f"  Total for {model_name:13}  ${model_cost:7.4f}   ⏱ {model_time:6.2f}s\n")

        # Save and add to grand totals.
        per_model_totals[model_name]["cost"] = model_cost
        per_model_totals[model_name]["time"] = model_time
        overall_cost += model_cost
        overall_time += model_time

    # Grand total across all models, with a per-model breakdown.
    print("🌐 Grand Total")
    print(f"  {'='*25}  ========   ==========")
    for model, values in per_model_totals.items():
        print(f"  {model:25}  ${values['cost']:7.4f}   ⏱ {values['time']:6.2f}s")
    print(f"  {'-'*25}  --------   ----------")
    print(f"  OVERALL TOTAL         ${overall_cost:7.4f}   ⏱ {overall_time:6.2f}s")


# Example usage:
if __name__ == "__main__":
    # Load game config to determine the project root under the install path.
    with open("config.yaml", 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)
    game_cfg = config.get(22, {})
    install_path = Path(game_cfg.get("install_path"))
    root = install_path / "Eushlator"
    # Summarize everything under 4_MachineTranslations (all models).
    summarize_costs(root / "4_MachineTranslations")
