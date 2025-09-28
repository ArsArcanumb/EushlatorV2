from pathlib import Path
import subprocess

# Path to the Eushully Decompiler executable.
# Expectation: this relative path is valid from the project's working directory.
DECOMPILER_PATH = "eushlator/repositories/Eushully-Decompiler/x64/Release/Decompiler.exe"


def run_decompilation(extracted_path: Path, decompiled_path: Path, install_path: Path):
    """
    Decompile all *.BIN files found under `extracted_path` into text files
    written into `decompiled_path`.

    Behavior:
      - If `decompiled_path` already contains any files, the function skips
        processing entirely (assumes decompilation already done).
      - Skips files named SYS5INI (special system file not needed here).
      - For each BIN file, if a patched BIN with the same name exists directly
        in `install_path`, use that instead (patch override). Otherwise use the
        BIN from `extracted_path`.
      - Produces <stem>.txt next to `decompiled_path`; if the output already
        exists, it is skipped.
      - Invokes the external decompiler via subprocess and reports success/fail.
    """
    # Quick guard: don't redo work if the target folder is already populated.
    if any(decompiled_path.iterdir()):
        print("[Info] Skipping decompilation, Decompiled folder is not empty.")
        return

    # Find every BIN file recursively inside the extracted files tree.
    bin_files = list(extracted_path.rglob("*.BIN"))

    if not bin_files:
        print("[Info] No .BIN files found to decompile.")
        return

    print(f"[Info] Decompiling .BIN files from Extracted folder (with patch override)...")

    for extracted_file in bin_files:
        # SYS5INI is intentionally skipped.
        if extracted_file.stem == "SYS5INI":
            continue

        # Determine the output *.txt path in the decompiled directory.
        out_file = decompiled_path / (extracted_file.stem + ".txt")
        if out_file.exists():
            # Already decompiled; skip.
            continue

        # Prefer a patched BIN placed directly in the game install root, if present.
        patched_file = install_path / extracted_file.name
        actual_file = patched_file if patched_file.exists() else extracted_file

        # Run external decompiler: -d <input.BIN> <output.txt>
        cmd = [DECOMPILER_PATH, "-d", str(actual_file), str(out_file)]
        result = subprocess.run(cmd)
        if result.returncode != 0:
            print(f"[Error] Failed to decompile {actual_file}")
        else:
            print(f"[Decompiled] {actual_file.name} -> {out_file.name}")


def run_decompilation_translations(translations_path: Path):
    """
    Decompile any *.BIN files already present under `translations_path`
    (e.g., user-provided or edited translation BINs) into .txt files in-place.

    Behavior:
      - If `translations_path` doesn't exist or contains no BIN files, this is a no-op.
      - Skips files named SYS5INI.
      - For each BIN, writes a sibling .txt (same folder) if it doesn't already exist.
      - Invokes the same decompiler executable as `run_decompilation`.
    """
    # If there is no translations directory yet, nothing to do.
    if not translations_path.exists():
        return

    translation_bin_files = list(translations_path.rglob("*.BIN"))

    if not translation_bin_files:
        print("[Info] No .BIN translation files found to decompile.")
        return

    print(f"[Info] Decompiling .BIN files from Translations folder...")

    for in_file in translation_bin_files:
        # Skip SYS5INI (system config).
        if in_file.stem == "SYS5INI":
            continue

        # Output path is the same location, just with .txt extension.
        out_file = in_file.with_suffix(".txt")

        if out_file.exists():
            # Already decompiled; skip.
            continue

        # Run external decompiler: -d <input.BIN> <output.txt>
        cmd = [DECOMPILER_PATH, "-d", str(in_file), str(out_file)]
        result = subprocess.run(cmd)
        if result.returncode != 0:
            print(f"[Error] Failed to decompile translation {in_file}")
        else:
            print(f"[Decompiled] {in_file.name} -> {out_file.name} (in Translations)")
