from pathlib import Path
import subprocess
import sys
import shutil

# Path to the Eushully Decompiler/Assembler executable.
# Used here in assembler mode (-a) to compile a .txt script back into a .BIN.
DECOMPILER_PATH = "eushlator/repositories/Eushully-Decompiler/x64/Release/Decompiler.exe"


def test_recompile_scene(install_path: Path, utils_path: Path):
    """
    Smoke-test recompilation of a single scene:
      - Reads a prepared/decompiled script from: <utils_path>/SC0000.txt
      - Assembles it into: <install_path>/SC0000.BIN   (using -a)
      - Creates a backup of any existing SC0000.BIN as SC0000.BIN.BAK (once)

    Args:
        install_path: Game install directory containing the target BIN files.
        utils_path:   The Eushlator Utils directory where SC0000.txt is expected.

    Notes:
        • This function is intentionally conservative: it will not overwrite an
          existing backup file (SC0000.BIN.BAK), and will proceed if it already exists.
        • It does not restore backups; it only assembles and writes SC0000.BIN.
    """
    source_txt = utils_path / "SC0000.txt"
    target_bin = install_path / "SC0000.BIN"
    backup_bin = install_path / "SC0000.BIN.BAK"

    # Ensure the input .txt exists before attempting to assemble.
    if not source_txt.exists():
        print("[Error] Decompiled SC0000.txt not found in Utils folder.")
        return

    # Backup original BIN if needed (only once).
    if target_bin.exists() and not backup_bin.exists():
        shutil.copy2(target_bin, backup_bin)
        print(f"[Info] Backed up original SC0000.BIN -> SC0000.BIN.BAK")
    elif backup_bin.exists():
        print(f"[Info] Backup already exists. Proceeding without overwrite.")
    else:
        # No original BIN to back up (e.g., first-time test or clean install).
        print(f"[Info] No original SC0000.BIN found to backup (may be testing from scratch).")

    # Assemble: Decompiler.exe -a <input.txt> <output.BIN>
    print("[Info] Recompiling SC0000.txt...")
    cmd = [DECOMPILER_PATH, "-a", str(source_txt), str(target_bin)]
    result = subprocess.run(cmd)

    # Report outcome of the external process.
    if result.returncode != 0:
        print(f"[Error] Failed to recompile {source_txt}")
    else:
        print(f"[Success] Recompiled SC0000.txt -> SC0000.BIN")


# Optional CLI interface
if __name__ == "__main__":
    # Expect exactly one argument: the game install path.
    if len(sys.argv) != 2:
        print("Usage: python utils/test_recompile.py <install_path>")
        sys.exit(1)

    install_path = Path(sys.argv[1])
    # Utils is assumed to live under the Eushlator working directory inside install_path.
    utils_path = install_path / "Eushlator" / "Utils"

    test_recompile_scene(install_path, utils_path)
