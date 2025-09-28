from pathlib import Path
import subprocess

# Path to the Eushully Decompiler/Assembler executable used for both
# decompilation (-d) and recompilation/assembly (-a).
DECOMPILER_PATH = "eushlator/repositories/Eushully-Decompiler/x64/Release/Decompiler.exe"


def recompile_script(inserted_path: Path, recompiled_path: Path):
    """
    Assemble translated .txt scripts back into .BIN files.

    Behavior:
      - Expects `inserted_path` to contain flat (non-recursive) *.txt files
        that were previously prepared for reinsertion.
      - Skips processing if `inserted_path` is missing or contains no *.txt files.
      - Writes assembled .BIN files into `recompiled_path` (created if needed).
      - Skips any output file that already exists.
      - Invokes the external tool with: Decompiler.exe -a <in.txt> <out.BIN>
    """
    if not inserted_path.exists():
        print(f"[Error] Inserted path does not exist: {inserted_path}")
        return

    # Only look at immediate children; no recursive search by design.
    txt_files = list(inserted_path.glob("*.txt"))
    if not txt_files:
        print("[Info] No inserted .txt files found to compile.")
        return

    # Ensure the output folder exists before writing BIN files.
    recompiled_path.mkdir(parents=True, exist_ok=True)

    print(f"[Info] Recompiling .txt files from {inserted_path.name} → {recompiled_path.name}")

    for txt_file in txt_files:
        out_file = recompiled_path / (txt_file.stem + ".BIN")

        # Idempotent: skip if the target BIN already exists.
        if out_file.exists():
            continue

        # Assemble: -a <input.txt> <output.BIN>
        cmd = [DECOMPILER_PATH, "-a", str(txt_file), str(out_file)]
        result = subprocess.run(cmd)
        if result.returncode != 0:
            print(f"[Error] Failed to recompile {txt_file}")
        else:
            print(f"[Compiled] {txt_file.name} → {out_file.name}")


def run_recompilation(inserted_path: Path, recompiled_path: Path, model_name: str):
    """
    Phase wrapper: compile all .txt files for a specific model run.

    Layout:
      - Inputs are under:  <inserted_path>/<model_name>/*.txt
      - Outputs go under:  <recompiled_path>/<model_name>/*.BIN
    """
    inserted = inserted_path / model_name
    recompiled = recompiled_path / model_name

    print("[Phase 6] recompile_script.py – start")
    recompile_script(inserted, recompiled)
    print("[Phase 6] done.")


if __name__ == "__main__":
    # Example usage placeholder (intentionally no-op in module mode).
    pass
