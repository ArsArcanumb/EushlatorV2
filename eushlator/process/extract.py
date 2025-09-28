from pathlib import Path

import importlib
# The extractor script lives in the Eushully-Decompiler repository.
# We import it dynamically to avoid a hard dependency at install/import time.
# Note: The module path contains a hyphen; using importlib avoids syntax issues
# that would occur with a regular `import eushlator.repositories.Eushully-Decompiler...`.
extract_alf = importlib.import_module("eushlator.repositories.Eushully-Decompiler.extract_alf")


def run_extraction(install_path: Path, extracted_path: Path):
    """
    Extract packed game assets from `install_path` into `extracted_path`.

    Behavior:
      - If `extracted_path` already has any files/folders, skip the extraction
        entirely (assumes it was done previously).
      - Otherwise, call the extractor's `main(install_dir, out_dir)` function.

    Notes:
      - This relies on the `extract_alf` module from the Eushully-Decompiler repo.
      - Paths are passed as strings to match the extractor's expected signature.
    """
    # Guard: don't redo work if the target folder isn't empty.
    if any(extracted_path.iterdir()):
        print("[Info] Skipping extraction, Extracted folder is not empty.")
        return

    print("[Info] Running extraction...")
    extract_alf.main(str(install_path), str(extracted_path))
